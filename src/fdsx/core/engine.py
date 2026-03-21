import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from fdsx.checkpoint.manager import CheckpointManager, _extract_meta_from_checkpoint
from fdsx.core.batch import (
    display_batch_summary,
    display_task_list,
    display_tasks_dir_summary,
    split_tasks,
)
from fdsx.core.compiler import compile_flow
from fdsx.core.config import load_config
from fdsx.core.loader import load_flow
from fdsx.core.selector import (
    resolve_workflow_for_task,
)
from fdsx.display.terminal import (
    Spinner,
    _sanitize_output,
    display_completion_summary,
    display_wait_prompt,
)
from fdsx.logging import RunRecorder
from fdsx.logging.recorder import FDSX_DIR_NAME, LOGS_DIR_NAME, RUNS_DIR_NAME
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file


class FlowValidationError(Exception):
    """Raised when flow validation fails."""

    pass


def run_flow(
    flow_path: Path,
    inputs: dict[str, str] | None = None,
    thread_id: str | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Run a flow from a YAML file.

    Args:
        flow_path: Path to the YAML workflow file
        inputs: Optional input variables
        thread_id: Optional thread ID (generated if not provided)
        base_dir: Optional base directory for checkpoints (.fdsx/).
                  If None, uses MemorySaver (no persistence).

    Returns:
        Final state variables as result dict. When max_loop is reached,
        returns partial results from the last completed iteration rather
        than raising an error.

    Raises:
        RuntimeError: If flow validation fails or execution fails
    """
    if thread_id is None:
        thread_id = str(uuid.uuid4())

    print(f"Thread ID: {_sanitize_output(thread_id)}", file=sys.stderr)

    flow, errors = load_flow(
        flow_path, input_keys=set(inputs.keys()) if inputs else None
    )
    if flow is None:
        raise FlowValidationError(f"Flow validation failed: {', '.join(errors)}")

    from fdsx.models.flow import WaitState, ParallelState

    needs_checkpointer = any(isinstance(s, WaitState) for s in flow.states.values())

    checkpoint_manager: CheckpointManager | None = None
    checkpointer: Any = None
    if base_dir is not None:
        checkpoint_manager = CheckpointManager(base_dir=base_dir)
        if not checkpoint_manager.acquire_lock(thread_id):
            locked, pid = checkpoint_manager.is_locked(thread_id)
            if locked:
                raise RuntimeError(f"Thread {thread_id} is locked by PID {pid}")
        checkpointer = checkpoint_manager.get_checkpointer()
        needs_checkpointer = True
    elif needs_checkpointer:
        checkpointer = MemorySaver()

    recorder = RunRecorder(
        thread_id=thread_id,
        flow_name=flow.name,
        flow_version=flow.version,
    )

    fdsx_config = load_config(project_dir=base_dir.parent if base_dir is not None else None)

    _runs_base = base_dir if base_dir is not None else Path.cwd() / FDSX_DIR_NAME
    log_dir = _runs_base / RUNS_DIR_NAME / thread_id / LOGS_DIR_NAME

    compiled = compile_flow(
        flow,
        input_keys=set(inputs.keys()) if inputs else None,
        checkpointer=checkpointer,
        recorder=recorder,
        config=fdsx_config,
        log_dir=log_dir,
    )

    initial_state: dict[str, Any] = {
        "_meta": {
            "thread_id": thread_id,
            "flow_path": str(flow_path),
            "flow_name": flow.name,
        }
    }

    if inputs:
        for key, value in inputs.items():
            initial_state[key] = value

    parallel_extra = sum(
        len(s.branches) + 1
        for s in flow.states.values()
        if isinstance(s, ParallelState)
    )
    wait_extra = sum(1 for s in flow.states.values() if isinstance(s, WaitState))
    steps_per_iter = len(flow.states) + parallel_extra + wait_extra
    recursion_limit = flow.max_loop * steps_per_iter + 1

    config: dict[str, Any] = {
        "recursion_limit": recursion_limit,
        "configurable": {"thread_id": thread_id},
    }

    last_state: dict[str, Any] = initial_state.copy()

    try:
        for state_snapshot in compiled.graph.stream(
            initial_state, config=config, stream_mode="values"
        ):
            if "__interrupt__" not in state_snapshot:
                last_state = state_snapshot

        if needs_checkpointer:
            while True:
                state_info = compiled.graph.get_state(config)

                if not state_info.tasks:
                    break

                payload = None
                for task in state_info.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        payload = task.interrupts[0].value
                        break

                if payload is None:
                    break

                message = payload.get("message", "")
                choices = payload.get("choices", [])
                state_name = payload.get("state_name", "wait")

                user_selection = display_wait_prompt(state_name, message, choices)

                for state_snapshot in compiled.graph.stream(
                    Command(resume=user_selection),
                    config=config,
                    stream_mode="values",
                ):
                    if "__interrupt__" not in state_snapshot:
                        last_state = state_snapshot

        if needs_checkpointer:
            final_state_info = compiled.graph.get_state(config)
            if final_state_info.values:
                last_state = final_state_info.values

        results = _extract_results(last_state, compiled.result_paths)
        recorder.finalize(_sanitize_state_for_log(last_state), "completed")
        recorder.save(base_dir=base_dir)
        display_completion_summary(flow.name, _calc_elapsed(recorder))
        return results
    except GraphRecursionError:
        print(f"Loop completed after {flow.max_loop} iterations", file=sys.stderr)
        results = _extract_results(last_state, compiled.result_paths)
        recorder.finalize(_sanitize_state_for_log(last_state), "completed")
        recorder.save(base_dir=base_dir)
        display_completion_summary(flow.name, _calc_elapsed(recorder))
        return results
    except Exception as e:
        if checkpoint_manager is not None:
            print(
                f"Checkpoint saved. Resume with: fdsx resume --thread-id {_sanitize_output(thread_id)}",
                file=sys.stderr,
            )
        recorder.finalize(_sanitize_state_for_log(last_state), "error")
        recorder.save(base_dir=base_dir)
        failed = _find_failed_state(recorder)
        failed_state_name = failed[0] if failed else "unknown"
        error_message = failed[1] if (failed and failed[1]) else str(e)
        display_completion_summary(
            flow.name, _calc_elapsed(recorder), failed_state_name, error_message
        )
        raise RuntimeError(f"Flow execution failed: {e}")
    finally:
        if checkpoint_manager is not None:
            checkpoint_manager.release_lock(thread_id)


def _extract_results(state: dict[str, Any], result_paths: list[str]) -> dict[str, Any]:
    """Extract result values from final state preserving nested paths."""
    from fdsx.core.variables import resolve_jsonpath, set_jsonpath

    results: dict[str, Any] = {}
    for path in result_paths:
        clean_path = path[2:] if path.startswith("$.") else path
        value = resolve_jsonpath(clean_path, state)
        if value is not None:
            results = set_jsonpath(clean_path, results, value)

    return results


def _sanitize_state_for_log(state: dict[str, Any]) -> dict[str, Any]:
    """Create a sanitized copy of state for logging, stripping internal keys."""
    return {
        k: v
        for k, v in state.items()
        if not k.startswith("_meta")
        and not k.startswith("__")
        and not k.startswith("_br_")
    }


def _calc_elapsed(recorder: RunRecorder) -> float:
    """Calculate elapsed seconds between recorder.started_at and completed_at.

    Falls back to current time if completed_at is not set.

    Args:
        recorder: The RunRecorder instance

    Returns:
        Elapsed time in seconds as a float
    """
    try:
        start = datetime.fromisoformat(recorder.started_at.replace("Z", "+00:00"))
        end_str = recorder.completed_at
        end = (
            datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if end_str is not None
            else datetime.now(timezone.utc)
        )
        return (end - start).total_seconds()
    except (ValueError, TypeError):
        return 0.0


def _find_failed_state(recorder: RunRecorder) -> tuple[str, str] | None:
    """Return (state_name, error_message) for the most recent error state.

    Searches recorder.states in reverse order for the first state with
    status=="error".

    Args:
        recorder: The RunRecorder instance

    Returns:
        Tuple of (state_name, error_message) or None if no error state found
    """
    for state in reversed(recorder.states):
        if state.get("status") == "error":
            return (str(state.get("name", "unknown")), str(state.get("error", "")))
    return None


def run_batch(
    workflow_path: Path,
    tasks_file: Path,
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Orchestrate batch execution of tasks.

    Args:
        workflow_path: Path to the YAML workflow file
        tasks_file: Path to the task file
        base_dir: Optional base directory for checkpoints (.fdsx/).

    Returns:
        List of result dicts with task_index, task_description, thread_id, status, error

    Raises:
        FlowValidationError: If flow validation fails
        RuntimeError: If task_splitter is missing or execution fails
    """
    import uuid

    flow, errors = load_flow(workflow_path)
    if flow is None:
        raise FlowValidationError(f"Flow validation failed: {', '.join(errors)}")

    config = load_config()
    if config.task_splitter is None:
        raise FlowValidationError(
            "Batch execution requires task_splitter configuration. "
            "Add task_splitter settings to your .fdsx/config.yaml:\n"
            "  task_splitter:\n"
            "    provider: claude\n"
            "    model: claude-sonnet-4-6"
        )
    task_splitter = config.task_splitter

    tasks_file_content = tasks_file.read_text()

    tasks = split_tasks(tasks_file_content, flow, task_splitter)

    if not tasks:
        print("No tasks to execute.", file=sys.stderr)
        return []

    approved = display_task_list(tasks)
    if not approved:
        print("Task list rejected. Aborting batch execution.", file=sys.stderr)
        return []

    results: list[dict[str, Any]] = []

    for i, task_description in enumerate(tasks):
        thread_id = str(uuid.uuid4())

        print(
            f"\nExecuting task {i + 1}/{len(tasks)}: {_sanitize_output(task_description[:50])}...",
            file=sys.stderr,
        )

        try:
            task_inputs = {"task": task_description}
            run_flow(
                flow_path=workflow_path,
                inputs=task_inputs,
                thread_id=thread_id,
                base_dir=base_dir,
            )
            results.append(
                {
                    "task_index": i,
                    "task_description": task_description,
                    "thread_id": thread_id,
                    "status": "completed",
                    "error": None,
                }
            )
        except Exception as e:
            results.append(
                {
                    "task_index": i,
                    "task_description": task_description,
                    "thread_id": thread_id,
                    "status": "failed",
                    "error": str(e),
                }
            )

            if i < len(tasks) - 1:
                print(
                    f"Task {i + 1} failed: {_sanitize_output(str(e))}", file=sys.stderr
                )
                while True:
                    response = (
                        input("Continue with remaining tasks? (y/n): ").strip().lower()
                    )
                    if response == "y":
                        break
                    elif response == "n":
                        print("Stopping batch execution.", file=sys.stderr)
                        display_batch_summary(results)
                        return results

    display_batch_summary(results)

    return results


def resume_flow(
    thread_id: str,
    base_dir: Path | None = None,
    flow_path: Path | None = None,
) -> dict[str, Any]:
    """Resume a flow from a checkpoint.

    Args:
        thread_id: The thread ID to resume
        base_dir: Base directory for checkpoints (.fdsx/). Defaults to '.fdsx/'.
        flow_path: Optional path to the flow YAML file. Required if not stored in checkpoint.

    Returns:
        Final state variables as result dict.

    Raises:
        RuntimeError: If checkpoint is corrupt or execution fails
    """
    if base_dir is None:
        base_dir = CheckpointManager.DEFAULT_BASE_DIR

    checkpoint_manager = CheckpointManager(base_dir=base_dir)

    if not checkpoint_manager.verify_checkpoint(thread_id):
        raise RuntimeError(f"No checkpoint found for thread ID {thread_id}")

    if not checkpoint_manager.acquire_lock(thread_id):
        locked, pid = checkpoint_manager.is_locked(thread_id)
        if locked:
            raise RuntimeError(f"Thread {thread_id} is locked by PID {pid}")

    print(f"Resuming from thread: {_sanitize_output(thread_id)}", file=sys.stderr)

    recorder: RunRecorder | None = None
    last_state: dict[str, Any] = {}

    try:
        checkpointer = checkpoint_manager.get_checkpointer()

        if flow_path is None or not flow_path.exists():
            config_for_lookup: Any = {"configurable": {"thread_id": thread_id}}
            checkpoint = checkpointer.get(config_for_lookup)

            if checkpoint:
                stored_meta = _extract_meta_from_checkpoint(checkpoint)
                if isinstance(stored_meta, dict):
                    flow_path_str = stored_meta.get("flow_path")
                    if flow_path_str:
                        flow_path = Path(flow_path_str)

            if flow_path is None or not (flow_path and flow_path.exists()):
                raise RuntimeError(
                    f"Flow path not found for thread ID {thread_id}. "
                    "Please provide the flow YAML path using the flow_path parameter."
                )

        flow, errors = load_flow(flow_path)
        if flow is None:
            raise RuntimeError(f"Failed to load flow for resume: {', '.join(errors)}")

        from fdsx.models.flow import WaitState, ParallelState
        from fdsx.logging.recorder import RUNS_DIR_NAME, RUN_FILENAME

        runs_dir = base_dir / RUNS_DIR_NAME
        existing_log_path = runs_dir / thread_id / RUN_FILENAME

        if existing_log_path.exists():
            import json

            with open(existing_log_path, "r") as f:
                existing_log = json.load(f)
            flow_name = existing_log.get("flow_name", flow.name)
            flow_version = existing_log.get("flow_version")
        else:
            flow_name = flow.name
            flow_version = flow.version

        recorder = RunRecorder(
            thread_id=thread_id,
            flow_name=flow_name,
            flow_version=flow_version,
        )

        fdsx_config = load_config(project_dir=base_dir.parent if base_dir is not None else None)

        resume_log_dir = base_dir / RUNS_DIR_NAME / thread_id / LOGS_DIR_NAME

        compiled = compile_flow(
            flow,
            checkpointer=checkpointer,
            recorder=recorder,
            config=fdsx_config,
            log_dir=resume_log_dir,
        )

        parallel_extra = sum(
            len(s.branches) + 1
            for s in flow.states.values()
            if isinstance(s, ParallelState)
        )
        wait_extra = sum(1 for s in flow.states.values() if isinstance(s, WaitState))
        steps_per_iter = len(flow.states) + parallel_extra + wait_extra
        recursion_limit = flow.max_loop * steps_per_iter + 1

        resume_config: dict[str, Any] = {
            "recursion_limit": recursion_limit,
            "configurable": {"thread_id": thread_id},
        }

        state_info = compiled.graph.get_state(resume_config)
        if state_info.tasks:
            payload = None
            for task in state_info.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    payload = task.interrupts[0].value
                    break

            if payload:
                print(
                    f"Resuming from state: {_sanitize_output(payload.get('state_name', 'wait'))}",
                    file=sys.stderr,
                )
                message = payload.get("message", "")
                choices = payload.get("choices", [])
                state_name = payload.get("state_name", "wait")

                user_selection = display_wait_prompt(state_name, message, choices)

                for state_snapshot in compiled.graph.stream(
                    Command(resume=user_selection),
                    config=resume_config,
                    stream_mode="values",
                ):
                    if "__interrupt__" not in state_snapshot:
                        last_state = state_snapshot
            else:
                # Error/pending task (no interrupt) — re-execute from checkpoint
                for state_snapshot in compiled.graph.stream(
                    None, config=resume_config, stream_mode="values"
                ):
                    if "__interrupt__" not in state_snapshot:
                        last_state = state_snapshot
        else:
            for state_snapshot in compiled.graph.stream(
                None, config=resume_config, stream_mode="values"
            ):
                if "__interrupt__" not in state_snapshot:
                    last_state = state_snapshot

        # Continue handling any further interrupts (e.g. multi-Wait flows)
        while True:
            state_info = compiled.graph.get_state(resume_config)
            if not state_info.tasks:
                break
            payload = None
            for task in state_info.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    payload = task.interrupts[0].value
                    break
            if payload is None:
                break
            message = payload.get("message", "")
            choices = payload.get("choices", [])
            state_name = payload.get("state_name", "wait")
            user_selection = display_wait_prompt(state_name, message, choices)
            for state_snapshot in compiled.graph.stream(
                Command(resume=user_selection),
                config=resume_config,
                stream_mode="values",
            ):
                if "__interrupt__" not in state_snapshot:
                    last_state = state_snapshot

        # Read authoritative state from checkpointer after resume completes
        final_state_info = compiled.graph.get_state(resume_config)
        if final_state_info.values:
            last_state = final_state_info.values

        results = _extract_results(last_state, compiled.result_paths)
        if recorder is not None:
            recorder.finalize(_sanitize_state_for_log(last_state), "completed")
            recorder.save(base_dir=base_dir)
            display_completion_summary(recorder.flow_name, _calc_elapsed(recorder))
        return results
    except GraphRecursionError:
        if flow is not None:
            print(f"Loop completed after {flow.max_loop} iterations", file=sys.stderr)
        if recorder is not None:
            recorder.finalize(_sanitize_state_for_log(last_state), "completed")
            recorder.save(base_dir=base_dir)
            display_completion_summary(recorder.flow_name, _calc_elapsed(recorder))
        return {}
    except Exception as e:
        if recorder is not None:
            recorder.finalize(_sanitize_state_for_log(last_state), "error")
            recorder.save(base_dir=base_dir)
            failed = _find_failed_state(recorder)
            failed_state_name = failed[0] if failed else "unknown"
            error_message = failed[1] if (failed and failed[1]) else str(e)
            display_completion_summary(
                recorder.flow_name,
                _calc_elapsed(recorder),
                failed_state_name,
                error_message,
            )
        raise RuntimeError(f"Flow resume failed: {e}")
    finally:
        checkpoint_manager.release_lock(thread_id)


def validate_flow(flow_path: Path) -> tuple[bool, list[str]]:
    """Validate a flow without executing it.

    Args:
        flow_path: Path to the YAML workflow file

    Returns:
        tuple of (is_valid, list of error messages)
    """
    flow, errors = load_flow(flow_path)
    return flow is not None, errors


def load_tasks_dir(tasks_dir: Path) -> list[tuple[Path, TaskFile]]:
    """Load and sort all task YAML files from a directory.

    Args:
        tasks_dir: Directory containing task YAML files.

    Returns:
        List of (file_path, task_file) tuples sorted alphabetically by filename.

    Raises:
        FileNotFoundError: If the tasks directory does not exist.
        ValueError: If no .yaml files are found.
    """
    if not tasks_dir.exists():
        raise FileNotFoundError(f"Tasks directory not found: {tasks_dir}")
    if tasks_dir.is_symlink():
        raise ValueError(f"Tasks directory must not be a symlink: {tasks_dir}")

    yaml_files = sorted(tasks_dir.glob("*.yaml"))
    if not yaml_files:
        raise ValueError(f"No .yaml files found in {tasks_dir}")

    result: list[tuple[Path, TaskFile]] = []
    for fp in yaml_files:
        if fp.is_symlink() or not fp.is_file():
            raise ValueError(
                f"Task file must be a regular file (not a symlink or special file): {fp}"
            )
        task_file = load_task_file(fp)
        result.append((fp, task_file))

    return result


def _filter_actionable_entries(
    task_file: TaskFile,
) -> list[tuple[int, TaskEntry]]:
    """Return entries that need execution.

    Skips entries with status "completed". Treats "failed" and "running"
    as retriable (returns them for execution).

    Args:
        task_file: The TaskFile to filter.

    Returns:
        List of (entry_index, entry) tuples for entries that should run.
    """
    actionable: list[tuple[int, TaskEntry]] = []
    for i, entry in enumerate(task_file.entries):
        if entry.status == "completed":
            continue
        actionable.append((i, entry))
    return actionable


def _update_task_status(
    file_path: Path,
    task_file: TaskFile,
    entry_index: int,
    status: str,
    thread_id: str | None = None,
    error: str | None = None,
) -> None:
    """Update and persist a single entry's status in the task file.

    Args:
        file_path: Path to the task YAML file.
        task_file: The TaskFile containing the entry.
        entry_index: Index of the entry within task_file.entries.
        status: New status value.
        thread_id: Optional thread ID to store.
        error: Optional error message to store.
    """
    entry = task_file.entries[entry_index]
    entry.status = cast(Literal["pending", "running", "completed", "failed"], status)
    entry.thread_id = thread_id
    entry.error = error
    save_task_file(file_path, task_file)


def run_tasks_dir(
    workflow_path: Path | None,
    tasks_dir: Path,
    base_dir: Path | None = None,
    auto_workflow: bool = False,
) -> list[dict[str, Any]]:
    """Execute tasks from a directory of YAML task files with crash-resilient persistence.

    Args:
        workflow_path: Path to the YAML workflow file. If None, workflows are auto-selected
            per task entry using the selector.
        tasks_dir: Directory containing task YAML files.
        base_dir: Optional base directory for checkpoints (.fdsx/).
        auto_workflow: If True, skip workflow confirmation prompts and auto-select.

    Returns:
        List of result dicts with file_index, file_name, entry_index,
        entry_description, thread_id, status, error, category.

    Raises:
        FlowValidationError: If flow validation fails.
    """
    import uuid

    try:
        task_files = load_tasks_dir(tasks_dir)
    except ValueError as e:
        raise FlowValidationError(str(e)) from e
    except FileNotFoundError as e:
        raise FlowValidationError(str(e))
    results: list[dict[str, Any]] = []
    workflow_assignments: dict[tuple[int, int], Path] = {}

    if base_dir is None:
        base_dir = Path.cwd() / ".fdsx"
    config = load_config(project_dir=base_dir.parent)
    project_root = base_dir.parent
    workflows_dir = project_root / config.workflows_dir

    auto_selection_entries: list[tuple[int, int, Path, str]] = []

    for file_idx, (file_path, task_file) in enumerate(task_files):
        actionable = _filter_actionable_entries(task_file)
        for entry_idx, entry in actionable:
            if entry.workflow is not None:
                wf_path = workflows_dir / entry.workflow
                workflow_assignments[(file_idx, entry_idx)] = wf_path
            elif workflow_path is not None:
                workflow_assignments[(file_idx, entry_idx)] = workflow_path
            else:
                auto_selection_entries.append(
                    (file_idx, entry_idx, file_path, entry.description)
                )

    auto_selection_keys: list[tuple[int, int]] = []
    if auto_selection_entries:
        total = len(auto_selection_entries)
        with Spinner(
            f"Auto-selecting workflows for {total} task{'s' if total != 1 else ''}..."
        ) as spinner:
            for count, (file_idx, entry_idx, file_path, description) in enumerate(
                auto_selection_entries, start=1
            ):
                spinner.update(
                    f"Auto-selecting workflows for {total} task{'s' if total != 1 else ''}... ({count}/{total})"
                )
                auto_selection_keys.append((file_idx, entry_idx))
                try:
                    resolved = resolve_workflow_for_task(
                        task_description=description,
                        workflows_dir=workflows_dir,
                        selector_config=config.workflow_selector,
                        auto_workflow=True,
                    )
                    if resolved is not None:
                        workflow_assignments[(file_idx, entry_idx)] = resolved
                except (ValueError, RuntimeError) as e:
                    print(
                        f"  Warning: auto-selection failed for entry {entry_idx} "
                        f"in {file_path.name}: {_sanitize_output(str(e))}",
                        file=sys.stderr,
                    )

    if (workflow_assignments or auto_selection_keys) and not auto_workflow:
        from fdsx.core.selector import discover_workflows
        from fdsx.display.terminal import confirm_workflow_assignments_interactive

        discovered = discover_workflows(workflows_dir)
        display_keys = sorted(workflow_assignments.keys()) + [
            k for k in auto_selection_keys if k not in workflow_assignments
        ]
        result = confirm_workflow_assignments_interactive(
            display_keys=display_keys,
            workflow_assignments=workflow_assignments,
            task_files=task_files,
            available_workflows=discovered,
        )
        if result is None:
            print("Workflow assignments cancelled.", file=sys.stderr)
            return results
        workflow_assignments = result
        for (file_idx, entry_idx), wf_path in workflow_assignments.items():
            file_path, task_file = task_files[file_idx]
            entry = task_file.entries[entry_idx]
            entry.workflow = wf_path.name
            save_task_file(file_path, task_file)
    elif auto_workflow and auto_selection_keys:
        for (file_idx, entry_idx), wf_path in workflow_assignments.items():
            file_path, task_file = task_files[file_idx]
            entry = task_file.entries[entry_idx]
            if entry.workflow is None:
                entry.workflow = wf_path.name
                save_task_file(file_path, task_file)

    for file_idx, (file_path, task_file) in enumerate(task_files):
        actionable = _filter_actionable_entries(task_file)

        if not actionable:
            for entry_idx, entry in enumerate(task_file.entries):
                results.append(
                    {
                        "file_index": file_idx,
                        "file_name": file_path.name,
                        "entry_index": entry_idx,
                        "entry_description": entry.description,
                        "thread_id": entry.thread_id or "",
                        "status": "completed",
                        "error": None,
                        "category": "skipped",
                    }
                )
            continue

        print(
            f"\nProcessing file {file_idx + 1}/{len(task_files)}: {_sanitize_output(file_path.name)}",
            file=sys.stderr,
        )
        print(
            f"  {len(actionable)} actionable entries out of {len(task_file.entries)}",
            file=sys.stderr,
        )

        for entry_idx, entry in enumerate(task_file.entries):
            if entry.status == "completed":
                results.append(
                    {
                        "file_index": file_idx,
                        "file_name": file_path.name,
                        "entry_index": entry_idx,
                        "entry_description": entry.description,
                        "thread_id": entry.thread_id or "",
                        "status": "completed",
                        "error": None,
                        "category": "skipped",
                    }
                )

        for entry_idx, entry in actionable:
            thread_id = str(uuid.uuid4())
            description = entry.description
            original_status = entry.status
            category = "retried" if original_status in ("failed", "running") else "new"

            effective_workflow = workflow_assignments.get(
                (file_idx, entry_idx), workflow_path
            )
            if effective_workflow is None:
                raise ValueError(
                    f"No workflow available for entry {entry_idx} in {file_path}. "
                    "Ensure the task has a workflow set or that workflows exist in the workflows directory."
                )

            print(
                f"  Executing entry {entry_idx + 1}/{len(task_file.entries)}: {_sanitize_output(description[:50])}...",
                file=sys.stderr,
            )

            _update_task_status(
                file_path, task_file, entry_idx, "running", thread_id=thread_id
            )

            try:
                task_inputs = {"task": description}
                run_flow(
                    flow_path=effective_workflow,
                    inputs=task_inputs,
                    thread_id=thread_id,
                    base_dir=base_dir,
                )
                _update_task_status(
                    file_path, task_file, entry_idx, "completed", thread_id=thread_id
                )
                results.append(
                    {
                        "file_index": file_idx,
                        "file_name": file_path.name,
                        "entry_index": entry_idx,
                        "entry_description": description,
                        "thread_id": thread_id,
                        "status": "completed",
                        "error": None,
                        "category": category,
                    }
                )
            except Exception as e:
                _update_task_status(
                    file_path,
                    task_file,
                    entry_idx,
                    "failed",
                    thread_id=thread_id,
                    error=str(e),
                )
                results.append(
                    {
                        "file_index": file_idx,
                        "file_name": file_path.name,
                        "entry_index": entry_idx,
                        "entry_description": description,
                        "thread_id": thread_id,
                        "status": "failed",
                        "error": str(e),
                        "category": category,
                    }
                )

                print(
                    f"  Entry {entry_idx + 1} failed: {_sanitize_output(str(e))}",
                    file=sys.stderr,
                )
                while True:
                    response = (
                        input("Continue with remaining entries? (y/n): ")
                        .strip()
                        .lower()
                    )
                    if response == "y":
                        break
                    elif response == "n":
                        print("Stopping tasks-dir execution.", file=sys.stderr)
                        display_tasks_dir_summary(results)
                        return results

    display_tasks_dir_summary(results)
    return results

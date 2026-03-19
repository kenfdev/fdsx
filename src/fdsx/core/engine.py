import sys
import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from fdsx.checkpoint.manager import CheckpointManager, _extract_meta_from_checkpoint
from fdsx.core.batch import (
    display_batch_summary,
    display_task_list,
    split_tasks,
)
from fdsx.core.compiler import compile_flow
from fdsx.core.config import load_config
from fdsx.core.loader import load_flow
from fdsx.display.terminal import _sanitize_output, display_wait_prompt
from fdsx.logging import RunRecorder


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

    compiled = compile_flow(
        flow,
        input_keys=set(inputs.keys()) if inputs else None,
        checkpointer=checkpointer,
        recorder=recorder,
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
        recorder.save()
        return results
    except GraphRecursionError:
        print(f"Loop completed after {flow.max_loop} iterations", file=sys.stderr)
        results = _extract_results(last_state, compiled.result_paths)
        recorder.finalize(_sanitize_state_for_log(last_state), "completed")
        recorder.save()
        return results
    except Exception as e:
        if checkpoint_manager is not None:
            print(
                f"Checkpoint saved. Resume with: fdsx resume --thread-id {_sanitize_output(thread_id)}",
                file=sys.stderr,
            )
        recorder.finalize(_sanitize_state_for_log(last_state), "error")
        recorder.save()
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

        runs_dir = Path.cwd() / "runs"
        existing_log_path = runs_dir / f"{thread_id}.json"

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

        compiled = compile_flow(
            flow,
            checkpointer=checkpointer,
            recorder=recorder,
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
            recorder.save()
        return results
    except GraphRecursionError:
        if flow is not None:
            print(f"Loop completed after {flow.max_loop} iterations", file=sys.stderr)
        if recorder is not None:
            recorder.finalize(_sanitize_state_for_log(last_state), "completed")
            recorder.save()
        return {}
    except Exception as e:
        if recorder is not None:
            recorder.finalize(_sanitize_state_for_log(last_state), "error")
            recorder.save()
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

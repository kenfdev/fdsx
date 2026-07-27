"""Tasks directory execution for the engine package."""

import hashlib
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Literal, cast

import structlog

import fdsx.core.mode
from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core.batch import (
    display_tasks_dir_summary,
    move_task_to_completed,
)
from fdsx.core.config import _resolve_xdg_config_dir, load_config
from fdsx.core.thread_id import generate_thread_id
from fdsx.display.terminal import (
    Spinner,
    _sanitize_output,
)
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file

from .run import run_flow
from .validate import FlowValidationError

logger = structlog.get_logger(__name__)


def load_tasks_dir(tasks_dir: Path) -> list[tuple[Path, TaskFile]]:
    """Load and sort all task YAML files from a directory.

    Args:
        tasks_dir: Directory containing task YAML files.

    Returns:
        List of (file_path, task_file) tuples sorted alphabetically by filename.

    Raises:
        FileNotFoundError: If the tasks directory does not exist.
        ValueError: If the directory or a task file is unsafe or invalid.
    """
    if not tasks_dir.exists():
        raise FileNotFoundError(f"Tasks directory not found: {tasks_dir}")
    if tasks_dir.is_symlink():
        raise ValueError(f"Tasks directory must not be a symlink: {tasks_dir}")

    yaml_files = sorted([*tasks_dir.glob("*.yaml"), *tasks_dir.glob("*.yml")])

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


def _workflow_persist_id(wf_path: Path, workflows_dir: Path) -> str:
    """Return a persistable workflow identifier that round-trips via workflows_dir / id."""
    try:
        rel = wf_path.resolve().relative_to(workflows_dir.resolve())
        return str(rel)
    except ValueError:
        return wf_path.name


def _discover_available_workflows(
    workflows_dir: Path,
    global_workflows_dir: Path | None,
    config_profiles: dict[str, dict[str, Any]] | None,
) -> list[tuple[Path, str, str]]:
    """Discover project and global workflows, preferring project duplicates."""
    from fdsx.core.selector import discover_workflows

    project_workflows = discover_workflows(
        workflows_dir, config_profiles=config_profiles
    )
    if (
        global_workflows_dir is None
        or global_workflows_dir.resolve() == workflows_dir.resolve()
    ):
        return project_workflows

    global_workflows = discover_workflows(
        global_workflows_dir, config_profiles=config_profiles
    )
    project_ids = {
        _workflow_persist_id(path, workflows_dir) for path, _, _ in project_workflows
    }
    combined: list[tuple[Path, str, str, str]] = [
        (path, description, display_name, "project")
        for path, description, display_name in project_workflows
    ]
    combined.extend(
        (path, description, display_name, "global")
        for path, description, display_name in global_workflows
        if _workflow_persist_id(path, global_workflows_dir) not in project_ids
    )

    name_counts = Counter(display_name for _, _, display_name, _ in combined)
    result: list[tuple[Path, str, str]] = []
    for path, description, display_name, scope in combined:
        if name_counts[display_name] > 1:
            root = workflows_dir if scope == "project" else global_workflows_dir
            workflow_id = _workflow_persist_id(path, root)
            display_name = f"{display_name} ({scope}/{workflow_id})"
        result.append((path, description, display_name))
    result.sort(key=lambda item: item[2])
    return result


def _resolve_persisted_workflow(
    workflow_id: str,
    workflow_dirs: list[Path],
) -> Path:
    """Resolve a persisted workflow ID from project, then global scope."""
    for workflows_dir in workflow_dirs:
        wf_path = workflows_dir / workflow_id
        try:
            resolved_wf = wf_path.resolve()
            wf_dir_resolved = workflows_dir.resolve()
            if (
                not str(resolved_wf).startswith(str(wf_dir_resolved) + "/")
                and resolved_wf != wf_dir_resolved
            ):
                raise ValueError(
                    f"Workflow path escapes workflows directory: {workflow_id}"
                )
        except OSError as e:
            raise ValueError(f"Cannot resolve workflow path: {workflow_id}") from e

        if wf_path.is_symlink():
            raise ValueError(f"Workflow path must not be a symlink: {wf_path}")
        if wf_path.is_dir():
            wf_file = wf_path / "workflow.yaml"
            if not wf_file.exists():
                wf_file = wf_path / "workflow.yml"
            if wf_file.exists():
                if wf_file.is_symlink():
                    raise ValueError(f"Workflow file must not be a symlink: {wf_file}")
                return wf_file
        elif wf_path.exists():
            return wf_path
        else:
            for ext in (".yaml", ".yml"):
                candidate = wf_path.with_suffix(ext)
                if candidate.exists() and not candidate.is_symlink():
                    return candidate

    return workflow_dirs[0] / workflow_id


def run_tasks_dir(
    workflow_path: Path | None,
    tasks_dir: Path,
    base_dir: Path | None = None,
    auto_workflow: bool = False,
    quiet: bool = False,
    continue_on_error: bool = False,
) -> list[dict[str, Any]]:
    """Drain task files until no newly queued files remain."""
    effective_base_dir = base_dir or Path.cwd() / ".fdsx"
    lock_manager = CheckpointManager(base_dir=effective_base_dir)
    lock_digest = hashlib.sha256(str(tasks_dir.resolve()).encode("utf-8")).hexdigest()[
        :16
    ]
    lock_id = f"tasks-dir-{lock_digest}"
    if not lock_manager.acquire_lock(lock_id):
        _, pid = lock_manager.is_locked(lock_id)
        owner = f" by PID {pid}" if pid is not None else ""
        logger.error(
            "tasks_dir_already_running",
            tasks_dir=str(tasks_dir),
            owner_pid=pid,
        )
        raise RuntimeError(
            f"Tasks directory is already being drained{owner}: {tasks_dir}"
        )

    try:
        return _drain_tasks_dir(
            workflow_path,
            tasks_dir,
            base_dir=base_dir,
            auto_workflow=auto_workflow,
            quiet=quiet,
            continue_on_error=continue_on_error,
        )
    finally:
        lock_manager.release_lock(lock_id)


def _drain_tasks_dir(
    workflow_path: Path | None,
    tasks_dir: Path,
    base_dir: Path | None = None,
    auto_workflow: bool = False,
    quiet: bool = False,
    continue_on_error: bool = False,
) -> list[dict[str, Any]]:
    """Drain newly discovered task files without reacquiring the directory lock."""
    results: list[dict[str, Any]] = []
    attempted_files: set[Path] = set()

    while True:
        try:
            queued_files = load_tasks_dir(tasks_dir)
        except ValueError as e:
            logger.error(
                "tasks_dir_load_failed", tasks_dir=str(tasks_dir), error=str(e)
            )
            raise FlowValidationError(str(e)) from e
        except FileNotFoundError as e:
            logger.error(
                "tasks_dir_load_failed", tasks_dir=str(tasks_dir), error=str(e)
            )
            raise FlowValidationError(str(e)) from e

        new_files = [
            item for item in queued_files if item[0].resolve() not in attempted_files
        ]
        if not new_files:
            if not results:
                print("No tasks queued.", file=sys.stderr)
            return results

        attempted_files.update(path.resolve() for path, _ in new_files)
        batch_results = _run_tasks_dir_snapshot(
            workflow_path,
            tasks_dir,
            base_dir=base_dir,
            auto_workflow=auto_workflow,
            quiet=quiet,
            continue_on_error=continue_on_error,
            task_files=new_files,
        )
        results.extend(batch_results)

        if not continue_on_error and any(
            result.get("status") == "failed" for result in batch_results
        ):
            return results


def _run_tasks_dir_snapshot(
    workflow_path: Path | None,
    tasks_dir: Path,
    base_dir: Path | None = None,
    auto_workflow: bool = False,
    quiet: bool = False,
    continue_on_error: bool = False,
    *,
    task_files: list[tuple[Path, TaskFile]],
) -> list[dict[str, Any]]:
    """Execute tasks from a directory of YAML task files with crash-resilient persistence.

    Args:
        workflow_path: Path to the YAML workflow file. If None, workflows are auto-selected
            per task entry using the selector.
        tasks_dir: Directory containing task YAML files.
        base_dir: Optional base directory for checkpoints (.fdsx/).
        auto_workflow: If True, skip workflow confirmation prompts and auto-select.
        quiet: If True, suppress streaming output during execution.
        continue_on_error: If True, continue processing remaining entries when an error
            occurs. If False (default), stop execution on first error in CI mode.

    Returns:
        List of result dicts with file_index, file_name, entry_index,
        entry_description, thread_id, status, error, category.

    Raises:
        FlowValidationError: If flow validation fails.
    """
    results: list[dict[str, Any]] = []
    workflow_assignments: dict[tuple[int, int], Path] = {}

    if base_dir is None:
        base_dir = Path.cwd() / ".fdsx"
    config = load_config(project_dir=base_dir.parent)
    project_root = base_dir.parent
    workflows_dir = project_root / config.workflows_dir
    if workflows_dir.is_symlink():
        raise FlowValidationError(
            f"Workflows directory must not be a symlink: {workflows_dir}"
        )
    global_config_dir = _resolve_xdg_config_dir()
    global_workflows_dir = (
        global_config_dir / "workflows" if global_config_dir is not None else None
    )
    if global_workflows_dir is not None and global_workflows_dir.is_symlink():
        raise FlowValidationError(
            f"Global workflows directory must not be a symlink: {global_workflows_dir}"
        )
    workflow_dirs = [workflows_dir]
    if (
        global_workflows_dir is not None
        and global_workflows_dir.resolve() != workflows_dir.resolve()
    ):
        workflow_dirs.append(global_workflows_dir)

    config_profiles = None
    if config.profiles:
        config_profiles = {
            name: prof.model_dump() for name, prof in config.profiles.items()
        }

    auto_selection_entries: list[tuple[int, int, Path, str]] = []

    for file_idx, (file_path, task_file) in enumerate(task_files):
        actionable = _filter_actionable_entries(task_file)
        for entry_idx, entry in actionable:
            if entry.workflow is not None:
                wf_path = _resolve_persisted_workflow(entry.workflow, workflow_dirs)
                workflow_assignments[(file_idx, entry_idx)] = wf_path
            elif workflow_path is not None:
                workflow_assignments[(file_idx, entry_idx)] = workflow_path
            else:
                auto_selection_entries.append(
                    (file_idx, entry_idx, file_path, entry.description)
                )

    available_workflows: list[tuple[Path, str, str]] = []
    if auto_selection_entries or (workflow_assignments and not auto_workflow):
        available_workflows = _discover_available_workflows(
            workflows_dir,
            global_workflows_dir,
            config_profiles,
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
                    from fdsx.core.selector import resolve_workflow_for_task

                    resolved = resolve_workflow_for_task(
                        task_description=description,
                        workflows_dir=workflows_dir,
                        selector_config=config.workflow_selector,
                        auto_workflow=True,
                        config_profiles=config_profiles,
                        available_workflows=available_workflows,
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
        from fdsx.display.terminal import confirm_workflow_assignments_interactive

        display_keys = sorted(workflow_assignments.keys()) + [
            k for k in auto_selection_keys if k not in workflow_assignments
        ]
        result = confirm_workflow_assignments_interactive(
            display_keys=display_keys,
            workflow_assignments=workflow_assignments,
            task_files=task_files,
            available_workflows=available_workflows,
        )
        if result is None:
            print("Workflow assignments cancelled.", file=sys.stderr)
            return results
        workflow_assignments = result
        for (file_idx, entry_idx), wf_path in workflow_assignments.items():
            file_path, task_file = task_files[file_idx]
            entry = task_file.entries[entry_idx]
            persist_root = workflows_dir
            if global_workflows_dir is not None and wf_path.resolve().is_relative_to(
                global_workflows_dir.resolve()
            ):
                persist_root = global_workflows_dir
            entry.workflow = _workflow_persist_id(wf_path, persist_root)
            save_task_file(file_path, task_file)
    elif auto_workflow and auto_selection_keys:
        for (file_idx, entry_idx), wf_path in workflow_assignments.items():
            file_path, task_file = task_files[file_idx]
            entry = task_file.entries[entry_idx]
            if entry.workflow is None:
                persist_root = workflows_dir
                if (
                    global_workflows_dir is not None
                    and wf_path.resolve().is_relative_to(global_workflows_dir.resolve())
                ):
                    persist_root = global_workflows_dir
                entry.workflow = _workflow_persist_id(wf_path, persist_root)
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
            # All entries were already completed — move to completed/
            try:
                move_task_to_completed(file_path)
            except Exception as e:
                print(
                    f"  Warning: could not move {_sanitize_output(file_path.name)} "
                    f"to completed/: {_sanitize_output(str(e))}",
                    file=sys.stderr,
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
            thread_id = generate_thread_id()
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
                task_inputs = {"task": description, "source": task_file.source or ""}
                # FR-4/FR-5: on_workflow_start and on_workflow_end fire once per task, inside
                # run_flow() / resume_flow() below. Do NOT wrap the outer tasks-dir loop with
                # additional workflow hook calls — hooks must fire per-task, not per-directory-run.
                flow_result = run_flow(
                    flow_path=effective_workflow,
                    inputs=task_inputs,
                    thread_id=thread_id,
                    base_dir=base_dir,
                    quiet=quiet,
                    task_file_path=file_path,
                    task_entry_index=entry_idx,
                )
                if flow_result.status != "completed":
                    error_msg = (
                        f"workflow aborted at state '{flow_result.abort_state}'"
                        if flow_result.status == "aborted"
                        else flow_result.status
                    )
                    _update_task_status(
                        file_path,
                        task_file,
                        entry_idx,
                        "failed",
                        thread_id=thread_id,
                        error=error_msg,
                    )
                    results.append(
                        {
                            "file_index": file_idx,
                            "file_name": file_path.name,
                            "entry_index": entry_idx,
                            "entry_description": description,
                            "thread_id": thread_id,
                            "status": "failed",
                            "error": error_msg,
                            "category": category,
                        }
                    )
                    if not continue_on_error:
                        display_tasks_dir_summary(results)
                        return results
                else:
                    _update_task_status(
                        file_path,
                        task_file,
                        entry_idx,
                        "completed",
                        thread_id=thread_id,
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
                if not fdsx.core.mode.is_interactive():
                    if continue_on_error:
                        print(
                            f"[CI] Continuing after error (entry {entry_idx}, file {file_path.name})",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"[CI] Failing fast (entry {entry_idx}, file {file_path.name})",
                            file=sys.stderr,
                        )
                        display_tasks_dir_summary(results)
                        return results
                else:
                    if continue_on_error:
                        print(
                            f"[interactive] Continuing after error (entry {entry_idx}, file {file_path.name})",
                            file=sys.stderr,
                        )
                    else:
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

        # Move the file to completed/ if all entries finished successfully
        if all(entry.status == "completed" for entry in task_file.entries):
            try:
                move_task_to_completed(file_path)
            except Exception as e:
                print(
                    f"  Warning: could not move {_sanitize_output(file_path.name)} "
                    f"to completed/: {_sanitize_output(str(e))}",
                    file=sys.stderr,
                )

    display_tasks_dir_summary(results)
    return results

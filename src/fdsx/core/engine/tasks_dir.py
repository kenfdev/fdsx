"""Tasks directory execution for the engine package."""

import sys
from pathlib import Path
from typing import Any, Literal, cast

from fdsx.core.batch import (
    display_tasks_dir_summary,
    move_task_to_completed,
)
from fdsx.core.config import load_config
from fdsx.core.thread_id import generate_thread_id
from fdsx.display.terminal import (
    Spinner,
    _sanitize_output,
)
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file

from .run import run_flow
from .validate import FlowValidationError


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


def _workflow_persist_id(wf_path: Path, workflows_dir: Path) -> str:
    """Return a persistable workflow identifier that round-trips via workflows_dir / id."""
    try:
        rel = wf_path.resolve().relative_to(workflows_dir.resolve())
        return str(rel)
    except ValueError:
        return wf_path.name


def run_tasks_dir(
    workflow_path: Path | None,
    tasks_dir: Path,
    base_dir: Path | None = None,
    auto_workflow: bool = False,
    quiet: bool = False,
) -> list[dict[str, Any]]:
    """Execute tasks from a directory of YAML task files with crash-resilient persistence.

    Args:
        workflow_path: Path to the YAML workflow file. If None, workflows are auto-selected
            per task entry using the selector.
        tasks_dir: Directory containing task YAML files.
        base_dir: Optional base directory for checkpoints (.fdsx/).
        auto_workflow: If True, skip workflow confirmation prompts and auto-select.
        quiet: If True, suppress streaming output during execution.

    Returns:
        List of result dicts with file_index, file_name, entry_index,
        entry_description, thread_id, status, error, category.

    Raises:
        FlowValidationError: If flow validation fails.
    """
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
    if workflows_dir.is_symlink():
        raise FlowValidationError(
            f"Workflows directory must not be a symlink: {workflows_dir}"
        )

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
                wf_path = workflows_dir / entry.workflow
                # Containment check
                try:
                    resolved_wf = wf_path.resolve()
                    wf_dir_resolved = workflows_dir.resolve()
                    if (
                        not str(resolved_wf).startswith(str(wf_dir_resolved) + "/")
                        and resolved_wf != wf_dir_resolved
                    ):
                        raise ValueError(
                            f"Workflow path escapes workflows directory: {entry.workflow}"
                        )
                except OSError:
                    raise ValueError(f"Cannot resolve workflow path: {entry.workflow}")
                if wf_path.is_symlink():
                    raise ValueError(f"Workflow path must not be a symlink: {wf_path}")
                if wf_path.is_dir():
                    wf_file = wf_path / "workflow.yaml"
                    if not wf_file.exists():
                        wf_file = wf_path / "workflow.yml"
                    if wf_file.is_symlink():
                        raise ValueError(
                            f"Workflow file must not be a symlink: {wf_file}"
                        )
                    wf_path = wf_file
                elif not wf_path.exists():
                    # Try adding extensions for display_name-based persistence
                    for ext in (".yaml", ".yml"):
                        candidate = wf_path.with_suffix(ext)
                        if candidate.exists() and not candidate.is_symlink():
                            wf_path = candidate
                            break
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
                    from fdsx.core.selector import resolve_workflow_for_task

                    resolved = resolve_workflow_for_task(
                        task_description=description,
                        workflows_dir=workflows_dir,
                        selector_config=config.workflow_selector,
                        auto_workflow=True,
                        config_profiles=config_profiles,
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

        discovered = discover_workflows(workflows_dir, config_profiles=config_profiles)
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
            entry.workflow = _workflow_persist_id(wf_path, workflows_dir)
            save_task_file(file_path, task_file)
    elif auto_workflow and auto_selection_keys:
        for (file_idx, entry_idx), wf_path in workflow_assignments.items():
            file_path, task_file = task_files[file_idx]
            entry = task_file.entries[entry_idx]
            if entry.workflow is None:
                entry.workflow = _workflow_persist_id(wf_path, workflows_dir)
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
                run_flow(
                    flow_path=effective_workflow,
                    inputs=task_inputs,
                    thread_id=thread_id,
                    base_dir=base_dir,
                    quiet=quiet,
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

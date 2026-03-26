"""run_batch implementation for the engine package."""

import sys
from pathlib import Path
from typing import Any

from fdsx.core.batch import (
    display_batch_summary,
    display_task_list,
    split_tasks,
)
from fdsx.core.config import load_config
from fdsx.core.loader import load_flow
from fdsx.core.thread_id import generate_thread_id
from fdsx.display.terminal import _sanitize_output

from .run import run_flow
from .validate import FlowValidationError


def run_batch(
    workflow_path: Path,
    tasks_file: Path,
    base_dir: Path | None = None,
    quiet: bool = False,
) -> list[dict[str, Any]]:
    """Orchestrate batch execution of tasks.

    Args:
        workflow_path: Path to the YAML workflow file
        tasks_file: Path to the task file
        base_dir: Optional base directory for checkpoints (.fdsx/).
        quiet: If True, suppress streaming output during execution.

    Returns:
        List of result dicts with task_index, task_description, thread_id, status, error

    Raises:
        FlowValidationError: If flow validation fails
        RuntimeError: If task_splitter is missing or execution fails
    """
    config = load_config()

    config_profiles = None
    if config.profiles:
        config_profiles = {
            name: prof.model_dump() for name, prof in config.profiles.items()
        }

    flow, errors = load_flow(workflow_path, config_profiles=config_profiles)
    if flow is None:
        raise FlowValidationError(f"Flow validation failed: {', '.join(errors)}")
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
        thread_id = generate_thread_id()

        print(
            f"\nExecuting task {i + 1}/{len(tasks)}: {_sanitize_output(task_description[:50])}...",
            file=sys.stderr,
        )

        try:
            task_inputs = {"task": task_description, "source": str(tasks_file)}
            run_flow(
                flow_path=workflow_path,
                inputs=task_inputs,
                thread_id=thread_id,
                base_dir=base_dir,
                quiet=quiet,
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

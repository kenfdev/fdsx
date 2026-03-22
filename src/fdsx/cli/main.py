import json
from pathlib import Path

import typer
import uuid_utils

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core import engine
from fdsx.core.batch import COMPLETED_SUBDIR, TASKS_DIR, split_tasks_to_groups, write_task_files
from fdsx.core.config import TaskSplitterConfig, load_config
from fdsx.core.engine import FlowValidationError
from fdsx.display.terminal import Spinner, _sanitize_output, display_resume_command

app = typer.Typer(help="fdsx - Declarative AI agent workflow execution framework")


@app.command()
def run(
    workflow: Path | None = typer.Argument(
        None,
        help="Path to the YAML workflow file (optional with --tasks-dir for auto-selection)",
    ),
    thread_id: str | None = typer.Option(None, help="Thread ID for this execution"),
    input_vars: list[str] | None = typer.Option(
        None, "--input", help="Input variable as KEY=VALUE"
    ),
    tasks_file: Path | None = typer.Option(
        None,
        "--tasks",
        help="Batch task file for in-memory splitting and execution (requires workflow argument)",
    ),
    tasks_dir: Path | None = typer.Option(
        None,
        "--tasks-dir",
        help="Directory of task YAML files for persistent batch execution with resume support",
    ),
    auto_workflow: bool | None = typer.Option(
        None,
        "--auto-workflow",
        help="Skip interactive workflow confirmation and auto-select (overrides config)",
    ),
    confirm_workflow: bool | None = typer.Option(
        None,
        "--confirm-workflow",
        help="Show interactive workflow confirmation UI before execution (overrides config)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Suppress stderr streaming output from providers. Log files are still written and completion summary is still shown.",
    ),
) -> None:
    """Run a workflow. Supports single execution, in-memory batch (--tasks), and persistent batch (--tasks-dir) modes.

    Shows an animated spinner during workflow auto-selection for tasks-dir mode.
    Displays an interactive numbered-list CUI for workflow confirmation (in interactive terminals).
    Use --auto-workflow to skip the confirmation UI.
    In non-interactive (non-TTY) terminals, auto-confirms without prompting."""
    if tasks_dir is not None:
        if input_vars is not None or tasks_file is not None:
            typer.echo(
                "Error: --tasks-dir is mutually exclusive with --input and --tasks",
                err=True,
            )
            raise typer.Exit(code=2)
        if not tasks_dir.exists():
            typer.echo(
                f"Error: Tasks directory not found: {tasks_dir}",
                err=True,
            )
            raise typer.Exit(code=2)
        if tasks_dir.is_symlink():
            typer.echo(
                f"Error: --tasks-dir must not be a symlink: {tasks_dir}",
                err=True,
            )
            raise typer.Exit(code=2)
        if not tasks_dir.is_dir():
            typer.echo(
                f"Error: --tasks-dir must be a directory: {tasks_dir}",
                err=True,
            )
            raise typer.Exit(code=2)
    elif input_vars and tasks_file is not None:
        typer.echo(
            "Error: --input and --tasks are mutually exclusive",
            err=True,
        )
        raise typer.Exit(code=2)
    elif workflow is None and tasks_dir is None:
        typer.echo(
            "Error: workflow argument is required when not using --tasks-dir",
            err=True,
        )
        raise typer.Exit(code=2)
    elif tasks_file is not None and workflow is None:
        typer.echo(
            "Error: workflow argument is required when using --tasks",
            err=True,
        )
        raise typer.Exit(code=2)

    if auto_workflow is not None and confirm_workflow is not None:
        typer.echo(
            "Error: --auto-workflow and --confirm-workflow are mutually exclusive",
            err=True,
        )
        raise typer.Exit(code=2)

    inputs = None
    if input_vars:
        inputs = {}
        for pair in input_vars:
            if "=" not in pair:
                typer.echo(
                    f"Invalid input format: {pair}. Use KEY=VALUE",
                    err=True,
                )
                raise typer.Exit(code=2)
            key, value = pair.split("=", 1)
            inputs[key] = value

    base_dir = Path(".fdsx")
    config = load_config()

    effective_auto_workflow = (
        auto_workflow if auto_workflow is not None else config.auto_workflow
    )
    if confirm_workflow is not None:
        effective_auto_workflow = not confirm_workflow

    current_thread_id = thread_id if thread_id else None

    try:
        if tasks_dir is not None:
            results = engine.run_tasks_dir(
                workflow,
                tasks_dir,
                base_dir,
                auto_workflow=effective_auto_workflow,
                quiet=quiet,
            )
            has_failure = any(r.get("status") == "failed" for r in results)
            if has_failure:
                raise typer.Exit(code=1)
            else:
                raise typer.Exit(code=0)
        elif tasks_file is not None:
            assert workflow is not None
            results = engine.run_batch(workflow, tasks_file, base_dir, quiet=quiet)
            has_failure = any(r.get("status") == "failed" for r in results)
            if has_failure:
                raise typer.Exit(code=1)
            else:
                raise typer.Exit(code=0)
        else:
            assert workflow is not None
            if current_thread_id is None:
                current_thread_id = str(uuid_utils.uuid7())
            engine.run_flow(workflow, inputs, current_thread_id, base_dir, quiet=quiet)
    except FlowValidationError as e:
        typer.echo(f"Validation error: {_sanitize_output(str(e))}", err=True)
        raise typer.Exit(code=2)
    except KeyboardInterrupt:
        _display_resume_on_error(tasks_dir, current_thread_id)
        raise typer.Exit(code=130)
    except RuntimeError as e:
        if isinstance(e, typer.Exit):
            raise
        typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
        _display_resume_on_error(tasks_dir, current_thread_id)
        raise typer.Exit(code=1)
    except Exception as e:
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
            _display_resume_on_error(tasks_dir, current_thread_id)
            raise typer.Exit(code=1)
        raise


def _display_resume_on_error(
    tasks_dir: Path | None,
    thread_id: str | None,
) -> None:
    """Display resume command on error, if applicable."""
    if tasks_dir is not None:
        display_resume_command(mode="tasks-dir", tasks_dir=tasks_dir)
    elif thread_id is not None:
        display_resume_command(mode="single-flow", thread_id=thread_id)


@app.command()
def validate(
    workflow: Path = typer.Argument(..., help="Path to the YAML workflow file"),
) -> None:
    """Validate a YAML workflow file without executing it."""
    is_valid, errors = engine.validate_flow(workflow)

    if is_valid:
        typer.echo(f"Flow '{workflow}' is valid.")
        raise typer.Exit(code=0)
    else:
        for error in errors:
            typer.echo(f"Error: {_sanitize_output(str(error))}", err=True)
        raise typer.Exit(code=2)


@app.command()
def resume(
    thread_id: str = typer.Option(..., "--thread-id", help="Thread ID to resume"),
    base_dir: Path | None = typer.Option(
        None, "--base-dir", help="Base directory for checkpoints (default: .fdsx/)"
    ),
) -> None:
    """Resume a flow from a checkpoint."""
    try:
        engine.resume_flow(thread_id, base_dir)
    except RuntimeError as e:
        error_msg = str(e)
        if "No checkpoint found" in error_msg:
            typer.echo(
                f"Error: No checkpoint found for thread ID {_sanitize_output(thread_id)}",
                err=True,
            )
            raise typer.Exit(code=2)
        elif "locked by PID" in error_msg:
            typer.echo(f"Error: {_sanitize_output(error_msg)}", err=True)
            raise typer.Exit(code=2)
        else:
            typer.echo(f"Error: {_sanitize_output(error_msg)}", err=True)
            raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
        raise typer.Exit(code=1)


@app.command(name="list")
def list_flows(
    base_dir: Path | None = typer.Option(
        None, "--base-dir", help="Base directory for checkpoints (default: .fdsx/)"
    ),
) -> None:
    """List all flow executions."""
    if base_dir is None:
        base_dir = Path(".fdsx")

    manager = CheckpointManager(base_dir=base_dir)
    threads = manager.list_threads()

    if not threads:
        typer.echo("No flow executions found.")
        return

    typer.echo(
        f"{'THREAD_ID':<40} {'FLOW_NAME':<20} {'STATUS':<12} "
        f"{'CURRENT_STATE':<20} {'STARTED_AT':<20}"
    )
    typer.echo("-" * 112)
    for thread in threads:
        typer.echo(
            f"{_sanitize_output(thread['thread_id']):<40} "
            f"{_sanitize_output(thread['flow_name']):<20} "
            f"{_sanitize_output(thread['status']):<12} "
            f"{_sanitize_output(thread.get('current_state', '')):<20} "
            f"{_sanitize_output(thread.get('started_at', '')):<20}"
        )


@app.command()
def split(
    task_file: Path = typer.Argument(..., help="Path to the task file to split"),
    force: bool = typer.Option(
        False, "--force", help="Clear existing tasks directory before splitting"
    ),
) -> None:
    """Split a task file into individual task files for persistent batch execution.

    Reads task_splitter configuration from .fdsx/config.yaml (or defaults).
    Writes numbered task files to .fdsx/tasks/ directory.
    Shows an animated spinner during LLM splitting. In non-interactive (non-TTY) terminals,
    prints plain log lines instead of animation.
    """
    if not task_file.exists():
        typer.echo(f"Error: Task file not found: {task_file}", err=True)
        raise typer.Exit(code=2)

    config = load_config()
    task_splitter = config.task_splitter or TaskSplitterConfig()

    tasks_dir = Path(TASKS_DIR)

    if tasks_dir.exists() and any(
        entry for entry in tasks_dir.iterdir() if entry.name != COMPLETED_SUBDIR
    ):
        if not force:
            typer.echo(
                f"Error: Tasks directory '{TASKS_DIR}' is not empty. "
                "Use --force to clear and overwrite.",
                err=True,
            )
            raise typer.Exit(code=2)
        if tasks_dir.is_symlink():
            typer.echo(
                f"Error: Tasks directory '{TASKS_DIR}' is a symlink. Refusing to delete.",
                err=True,
            )
            raise typer.Exit(code=2)
        for f in tasks_dir.glob("*.yaml"):
            f.unlink()
        typer.echo(f"Cleared existing task files in {TASKS_DIR}/", err=True)

    try:
        task_content = task_file.read_text()

        with Spinner("Splitting tasks...") as spinner:
            groups = split_tasks_to_groups(task_content, task_splitter)

            if not groups:
                typer.echo("No tasks were generated from the input file.", err=True)
                typer.echo(json.dumps([]))
                return

            spinner.update(f"Writing {len(groups)} task file(s)...")
            created_files = write_task_files(groups, tasks_dir)

        typer.echo(
            f"Created {len(created_files)} task file(s) in {TASKS_DIR}/", err=True
        )
        for f in created_files:
            typer.echo(f"  {f}", err=True)
        typer.echo(json.dumps([str(f) for f in created_files]))

    except RuntimeError as e:
        typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
        raise typer.Exit(code=1)
    except ValueError as e:
        typer.echo(f"Error parsing tasks: {_sanitize_output(str(e))}", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()

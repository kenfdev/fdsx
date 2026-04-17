import os
import sys
from pathlib import Path

import click
import typer
import typer.core

from fdsx import __version__
from fdsx.checkpoint.manager import CheckpointManager
from fdsx.cli.init_interactive import (
    assign_profiles,
    confirm_existing_project,
    confirm_overwrite,
    confirm_skill_overwrite,
    prompt_skill_install,
    select_models,
    select_providers,
    select_templates,
)
from fdsx.core import engine
from fdsx.core.batch import (
    COMPLETED_SUBDIR,
    TASKS_DIR,
    split_tasks_to_groups,
    write_task_files,
)
from fdsx.core.config import TaskSplitterConfig, load_config
from fdsx.core.engine import FlowValidationError
from fdsx.core.hooks import collect_run_hooks, execute_run_hooks
from fdsx.core.init import (
    check_conflicts,
    discover_templates,
    ensure_gitignore,
    install_skill,
    needs_init,
    scaffold,
)
from fdsx.core.mode import is_interactive, set_interactive_mode
from fdsx.core.thread_id import generate_thread_id
from fdsx.display.terminal import Spinner, _sanitize_output, display_resume_command
from fdsx.models.init import InitConfig

EXEMPT_SUBCOMMANDS = frozenset({"init", "validate"})

_RAW_ARGS_KEY = "_fdsx_raw_args"


class _FdsxGroup(typer.core.TyperGroup):
    """Custom Typer group that captures raw invocation args before Click consumes them.

    This is needed to detect `--help` on subcommands (e.g. `fdsx run --help`) inside
    the group callback, where Click has already separated the subcommand args from the
    group args by the time the callback runs.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if ctx.obj is None:
            ctx.obj = {}
        if isinstance(ctx.obj, dict):
            ctx.obj[_RAW_ARGS_KEY] = list(args)
        return super().parse_args(ctx, args)


app = typer.Typer(
    help="fdsx - Declarative AI agent workflow execution framework",
    cls=_FdsxGroup,
)


def _validate_tasks_dir(tasks_dir: Path) -> None:
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


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit.",
    ),
    ci: bool = typer.Option(
        False,
        "--ci",
        help="Run in CI mode (non-interactive, equivalent to --interactive=false).",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help="Run in interactive mode (enables TTY detection if not explicitly set).",
    ),
) -> None:
    if ci and interactive:
        typer.echo(
            "Error: --ci and --interactive are mutually exclusive",
            err=True,
        )
        raise typer.Exit(code=2)
    if interactive:
        set_interactive_mode(True)
    elif ci:
        set_interactive_mode(False)
    else:
        ci_env = os.environ.get("CI", "").lower() in ("true", "1", "yes")
        gh_actions = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
        if ci_env or gh_actions:
            set_interactive_mode(False)
        else:
            set_interactive_mode(sys.stdin.isatty())
    _raw_args: list[str] = (
        ctx.obj.get(_RAW_ARGS_KEY, []) if isinstance(ctx.obj, dict) else []
    )
    _exempt = (
        version
        or ctx.invoked_subcommand is None
        or ctx.invoked_subcommand in EXEMPT_SUBCOMMANDS
        or "--help" in _raw_args
        or "-h" in _raw_args
    )
    if not _exempt and needs_init(Path.cwd()):
        typer.echo(
            "No .fdsx/ directory found. Run 'fdsx init' to set up your project.",
            err=True,
        )
        raise typer.Exit(code=0)
    elif (
        not _exempt
        and is_interactive()
        and not needs_init(Path.cwd())
        and not (Path.cwd() / ".fdsx" / ".gitignore").exists()
    ):
        ensure_gitignore(Path.cwd())
    if version:
        typer.echo(f"fdsx {__version__}")
        raise typer.Exit()


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
    continue_on_error: bool = typer.Option(
        False,
        "--continue-on-error",
        help="Continue processing remaining entries when an error occurs in tasks-dir mode.",
    ),
) -> None:
    """Run a workflow. Supports single execution and persistent batch (--tasks-dir) modes.

    Shows an animated spinner during workflow auto-selection for tasks-dir mode.
    Displays an interactive numbered-list CUI for workflow confirmation (in interactive terminals).
    Use --auto-workflow to skip the confirmation UI.
    In non-interactive (non-TTY) terminals, auto-confirms without prompting."""
    config = load_config()
    if tasks_dir is not None:
        if input_vars is not None:
            typer.echo(
                "Error: --tasks-dir is mutually exclusive with --input",
                err=True,
            )
            raise typer.Exit(code=2)
        _validate_tasks_dir(tasks_dir)
    elif input_vars is not None and workflow is None:
        typer.echo(
            "Error: workflow argument is required when using --input",
            err=True,
        )
        raise typer.Exit(code=2)
    elif workflow is None and tasks_dir is None and not input_vars:
        resolved_tasks_dir = Path(
            config.default_tasks_dir if config.default_tasks_dir else ".fdsx/tasks/"
        ).expanduser()
        _validate_tasks_dir(resolved_tasks_dir)
        tasks_dir = resolved_tasks_dir

    if auto_workflow is not None and confirm_workflow is not None:
        typer.echo(
            "Error: --auto-workflow and --confirm-workflow are mutually exclusive",
            err=True,
        )
        raise typer.Exit(code=2)

    if confirm_workflow and not is_interactive():
        typer.echo(
            "Error: --confirm-workflow requires interactive mode and cannot be used with --ci or in CI environments",
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

    effective_auto_workflow = (
        auto_workflow if auto_workflow is not None else config.auto_workflow
    )
    if confirm_workflow is not None:
        effective_auto_workflow = not confirm_workflow

    current_thread_id = thread_id if thread_id else None

    _start_hooks = collect_run_hooks(
        "on_run_start", global_run_hooks=config.run_hooks, project_run_hooks=None
    )
    _end_hooks = collect_run_hooks(
        "on_run_end", global_run_hooks=config.run_hooks, project_run_hooks=None
    )
    execute_run_hooks(_start_hooks, status="starting", event="on_run_start")

    try:
        if tasks_dir is not None:
            results = engine.run_tasks_dir(
                workflow,
                tasks_dir,
                base_dir,
                auto_workflow=effective_auto_workflow,
                quiet=quiet,
                continue_on_error=continue_on_error,
            )
            run_status = _compute_run_status(results)
            execute_run_hooks(_end_hooks, status=run_status, event="on_run_end")
            raise typer.Exit(code=0 if run_status == "completed" else 1)
        else:
            assert workflow is not None
            if current_thread_id is None:
                current_thread_id = generate_thread_id()
            engine.run_flow(workflow, inputs, current_thread_id, base_dir, quiet=quiet)
            execute_run_hooks(_end_hooks, status="completed", event="on_run_end")
    except FlowValidationError as e:
        typer.echo(f"Validation error: {_sanitize_output(str(e))}", err=True)
        raise typer.Exit(code=2) from None
    except KeyboardInterrupt:
        _display_resume_on_error(tasks_dir, current_thread_id)
        raise typer.Exit(code=130) from None
    except RuntimeError as e:
        if isinstance(e, typer.Exit):
            raise
        typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
        _display_resume_on_error(tasks_dir, current_thread_id)
        execute_run_hooks(_end_hooks, status="failed", event="on_run_end")
        raise typer.Exit(code=1) from None
    except Exception as e:
        if not isinstance(e, typer.Exit):
            typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
            _display_resume_on_error(tasks_dir, current_thread_id)
            execute_run_hooks(_end_hooks, status="failed", event="on_run_end")
            raise typer.Exit(code=1) from None
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


def _compute_run_status(results: list[dict[str, object]]) -> str:
    """Compute aggregate status for a tasks-dir run from individual task results."""
    statuses = {r.get("status") for r in results}
    if statuses == {"completed"}:
        return "completed"
    if statuses == {"failed"}:
        return "failed"
    return "partial"


@app.command()
def init(
    skill: bool = typer.Option(
        False,
        "--skill",
        help="Install the /fdsx Claude Code skill only (skip .fdsx/ scaffold).",
    ),
) -> None:
    """Initialize a new fdsx project with interactive provider and template selection."""
    if not sys.stdin.isatty():
        typer.echo(
            "Error: fdsx init requires an interactive terminal.",
            err=True,
        )
        raise typer.Exit(code=2)

    try:
        if skill:
            _run_skill_only_install()
            raise typer.Exit(code=0)

        templates = discover_templates()
        cwd = Path.cwd()

        if not needs_init(cwd) and not confirm_existing_project():
            raise typer.Exit(code=0)

        providers = select_providers()
        provider_selections = select_models(providers)
        profile_assignments = assign_profiles(provider_selections)
        selected_templates = select_templates(templates)

        allow_overwrite: set[str] = set()
        if selected_templates:
            conflicts = check_conflicts(cwd, selected_templates)
            for conflict in conflicts:
                if confirm_overwrite(conflict):
                    allow_overwrite.add(conflict)
        _prompt_and_install_skill(cwd)

        config = InitConfig(
            providers=provider_selections,
            templates=selected_templates,
            profile_assignments=profile_assignments,
        )
        result = scaffold(cwd, config, allow_overwrite)

        typer.echo("Initialized .fdsx/ directory.\n", err=True)
        typer.echo("Created:", err=True)
        for f in result.created:
            typer.echo(f"  {f}", err=True)
        if result.skipped_config:
            typer.echo("  .fdsx/config.yaml (preserved)", err=True)
        if result.skipped_workflows:
            typer.echo("\nSkipped (already exist):", err=True)
            for w in result.skipped_workflows:
                typer.echo(f"  .fdsx/workflows/{w}", err=True)

        typer.echo("\nNext steps:", err=True)
        typer.echo(
            "  1. Customize model assignments per profile in .fdsx/config.yaml (smarty, doer, specialist, generalist, behemoth)",
            err=True,
        )
        typer.echo("  2. Customize workflows in .fdsx/workflows/", err=True)
        typer.echo(
            "  3. Run a workflow: fdsx run .fdsx/workflows/<name>/workflow.yaml",
            err=True,
        )
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None


def _run_skill_only_install() -> None:
    """Handle --skill flag: install skill only, no scaffold."""
    target_path = prompt_skill_install()
    if target_path is None:
        typer.echo("Skill installation skipped.", err=True)
        return

    skill_dir = target_path / "fdsx"
    overwrite = False
    if skill_dir.exists():
        overwrite = confirm_skill_overwrite(target_path)
        if not overwrite:
            typer.echo("Skill installation skipped.", err=True)
            return

    created = install_skill(target_path, overwrite=overwrite)
    typer.echo("Skill installed successfully!\n", err=True)
    typer.echo("Installed:", err=True)
    for f in created:
        typer.echo(f"  {f}", err=True)


def _prompt_and_install_skill(cwd: Path) -> None:
    """Prompt for skill install after scaffold completes. Handles decline gracefully."""
    if not is_interactive():
        return

    try:
        target_path = prompt_skill_install()
    except (EOFError, RuntimeError, click.exceptions.Abort):
        return
    if target_path is None:
        return

    skill_dir = target_path / "fdsx"
    overwrite = False
    if skill_dir.exists():
        try:
            overwrite = confirm_skill_overwrite(target_path)
        except (EOFError, RuntimeError, click.exceptions.Abort):
            return
        if not overwrite:
            return

    try:
        created = install_skill(target_path, overwrite=overwrite)
        typer.echo("\nSkill installed:", err=True)
        for f in created:
            typer.echo(f"  {f}", err=True)
    except FileExistsError:
        typer.echo(
            "\nSkill already exists. Use --skill flag to reinstall.",
            err=True,
        )


@app.command()
def validate(
    workflow: Path = typer.Argument(..., help="Path to the YAML workflow file"),
) -> None:
    """Validate a YAML workflow file without executing it."""
    is_valid, errors, flow_name = engine.validate_flow(workflow)

    if is_valid:
        typer.echo(f"Flow '{_sanitize_output(flow_name or str(workflow))}' is valid.")
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
    config = load_config()
    _start_hooks = collect_run_hooks(
        "on_run_start", global_run_hooks=config.run_hooks, project_run_hooks=None
    )
    _end_hooks = collect_run_hooks(
        "on_run_end", global_run_hooks=config.run_hooks, project_run_hooks=None
    )
    execute_run_hooks(_start_hooks, status="starting", event="on_run_start")
    try:
        engine.resume_flow(thread_id, base_dir)
        execute_run_hooks(_end_hooks, status="completed", event="on_run_end")
    except RuntimeError as e:
        error_msg = str(e)
        execute_run_hooks(_end_hooks, status="failed", event="on_run_end")
        if "No checkpoint found" in error_msg:
            typer.echo(
                f"Error: No checkpoint found for thread ID {_sanitize_output(thread_id)}",
                err=True,
            )
            raise typer.Exit(code=2) from None
        elif "locked by PID" in error_msg:
            typer.echo(f"Error: {_sanitize_output(error_msg)}", err=True)
            raise typer.Exit(code=2) from None
        else:
            typer.echo(f"Error: {_sanitize_output(error_msg)}", err=True)
            raise typer.Exit(code=1) from None
    except Exception as e:
        typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
        execute_run_hooks(_end_hooks, status="failed", event="on_run_end")
        raise typer.Exit(code=1) from None


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
def add(
    task_file: Path = typer.Argument(..., help="Path to the task file"),
    split: bool = typer.Option(
        False, "--split", help="Split the task file into multiple task files"
    ),
    force: bool = typer.Option(
        False, "--force", help="Clear existing tasks directory before writing"
    ),
) -> None:
    """Add a task file to the batch execution queue.

    When --split is specified, reads task_splitter configuration from .fdsx/config.yaml
    (or defaults) and splits the task file into individual task files in .fdsx/tasks/.
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

    single_task = not split

    try:
        task_content = task_file.read_text()

        with Spinner("Splitting tasks...") as spinner:
            groups = split_tasks_to_groups(
                task_content,
                task_splitter,
                single_task=single_task,
                progress=spinner.update,
            )

            if not groups:
                typer.echo("No tasks were generated from the input file.", err=True)
                return

            spinner.update(f"Writing {len(groups)} task file(s)...")
            created_files = write_task_files(groups, tasks_dir, source=str(task_file))

        typer.echo(
            f"Created {len(created_files)} task file(s) in {TASKS_DIR}/", err=True
        )
        for f in created_files:
            typer.echo(f"  {f}", err=True)

    except RuntimeError as e:
        typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
        raise typer.Exit(code=1) from None
    except ValueError as e:
        typer.echo(f"Error parsing tasks: {_sanitize_output(str(e))}", err=True)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()

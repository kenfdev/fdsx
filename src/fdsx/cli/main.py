import json
from pathlib import Path

import typer

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core import engine
from fdsx.core.engine import FlowValidationError
from fdsx.display.terminal import _sanitize_output

app = typer.Typer(help="fdsx - Declarative AI agent workflow execution framework")


@app.command()
def run(
    workflow: Path = typer.Argument(..., help="Path to the YAML workflow file"),
    thread_id: str | None = typer.Option(None, help="Thread ID for this execution"),
    input_vars: list[str] | None = typer.Option(
        None, "--input", help="Input variable as KEY=VALUE"
    ),
) -> None:
    """Run a workflow from a YAML file."""
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

    try:
        result = engine.run_flow(workflow, inputs, thread_id, base_dir)
        typer.echo(json.dumps(result, indent=2))
    except FlowValidationError as e:
        typer.echo(f"Validation error: {e}", err=True)
        raise typer.Exit(code=2)
    except RuntimeError as e:
        typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
        raise typer.Exit(code=1)
    except Exception as e:
        typer.echo(f"Error: {_sanitize_output(str(e))}", err=True)
        raise typer.Exit(code=1)


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
            typer.echo(f"Error: {error}", err=True)
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
        result = engine.resume_flow(thread_id, base_dir)
        typer.echo(json.dumps(result, indent=2))
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


if __name__ == "__main__":
    app()

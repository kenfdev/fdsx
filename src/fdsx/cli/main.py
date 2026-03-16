import json
from pathlib import Path

import typer

from fdsx.core import engine
from fdsx.core.engine import FlowValidationError
from fdsx.display.terminal import _sanitize_output

app = typer.Typer(help="fdsx - Declarative AI agent workflow execution framework")


@app.command()
def run(
    workflow: Path = typer.Argument(..., help="Path to the YAML workflow file"),
    thread_id: str | None = typer.Option(None, help="Thread ID for this execution"),
    input: list[str] | None = typer.Option(
        None, "--input", help="Input variable as KEY=VALUE"
    ),
) -> None:
    """Run a workflow from a YAML file."""
    inputs = None
    if input:
        inputs = {}
        for pair in input:
            if "=" not in pair:
                typer.echo(
                    f"Invalid input format: {pair}. Use KEY=VALUE",
                    err=True,
                )
                raise typer.Exit(code=2)
            key, value = pair.split("=", 1)
            inputs[key] = value

    try:
        result = engine.run_flow(workflow, inputs, thread_id)
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


if __name__ == "__main__":
    app()

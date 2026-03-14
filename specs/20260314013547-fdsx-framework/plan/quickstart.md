# Quickstart: fdsx Development

## Prerequisites

- Python ≥3.10
- uv (package manager)
- At least one provider CLI installed (e.g., `claude`)

## Setup

```bash
# Clone and setup
cd fdsx
uv sync

# Run tests
uv run pytest

# Run linting
uv run ruff check src/ tests/
uv run mypy src/fdsx/
```

## Running fdsx

```bash
# Validate a workflow
uv run fdsx validate workflow.yaml

# Run a workflow
uv run fdsx run workflow.yaml

# Run with input
uv run fdsx run workflow.yaml --input task="fix the login bug"

# Resume from checkpoint
uv run fdsx resume --thread-id abc-123

# List executions
uv run fdsx list
```

## Minimal Workflow Example

```yaml
# hello.yaml
name: hello_world
start_at: greet

states:
  greet:
    type: task
    provider: system
    command: "echo 'Hello from fdsx!'"
    result_path: $.greeting
    end: true
```

```bash
uv run fdsx run hello.yaml
```

## Development Workflow

1. Models are in `src/fdsx/models/flow.py` — edit Pydantic models for YAML schema changes
2. Compiler is in `src/fdsx/core/compiler.py` — maps YAML states to LangGraph nodes/edges
3. Provider adapters are in `src/fdsx/providers/` — one file per provider
4. Run unit tests: `uv run pytest tests/unit/`
5. Run integration tests: `uv run pytest tests/integration/`

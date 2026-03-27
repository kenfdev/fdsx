# Agent Instructions

## Development Environment

This is a Python project managed with `uv`.

**Running Python tools — always use `uv run`:**
- Tests: `uv run pytest tests/ -v`
- Type check: `uv run mypy src/`
- Lint check: `uv run ruff check src/ tests/`
- Lint fix: `uv run ruff check --fix src/ tests/`
- Single file: `uv run pytest tests/unit/test_foo.py -v`

**Before committing**, always run:
- Format check: `uv run ruff format --check .`
- If files need reformatting: `uv run ruff format .`

**Never use:**
- Bare `python` or `python3` — system Python lacks project dependencies
- `.venv/bin/python` directly — venv symlinks may be stale after Python version changes
- `pip install` — use `uv pip install` or `uv add` instead

## Testing Guidelines

### Test Trophy Strategy

This project follows the test trophy pattern. Tests are organized into three layers:

#### Directory Structure
- `tests/unit/` — Unit tests for complex pure logic
- `tests/integration/` — Integration tests (primary confidence layer)
- `tests/e2e/` — CLI end-to-end tests (thinnest layer)

#### What belongs at each level

**Unit tests** (`tests/unit/`):
- Complex parsing logic (YAML parsing, JSONPath resolution, variable substitution)
- State transition rules and algorithms
- Pure functions with non-trivial logic
- NOT: Pydantic model field assignments, default value checks, isinstance checks

**Integration tests** (`tests/integration/`):
- Complete workflow execution via `engine.run_flow()`
- Feature-centered tests: each file answers "Does feature X work correctly?"
- Checkpoint persistence and recovery
- State variable mutations and result paths
- Tests using `CliRunner` for testing CLI behavior with mocked internals

**E2E tests** (`tests/e2e/`):
- CLI surface tests via `run_fdsx()` subprocess calls
- Exit codes, stderr/stdout format validation
- CLI argument parsing and mutual exclusion
- Signal handling

#### Provider mocking

Tests must never invoke real provider binaries (`claude`, `codex`, `opencode`). Always mock `_run_subprocess` for the relevant provider module. Follow the pattern from `tests/integration/test_provider_options.py`:

```python
from unittest.mock import patch
from fdsx.providers.base import ProviderResult

fake = ProviderResult(exit_code=0, stdout="mocked output", stderr="")
with patch("fdsx.providers.claude._run_subprocess", return_value=fake):
    result = run_flow(path, base_dir=tmp_path)
```

Use `provider: system` with `echo` commands when the test doesn't need to verify provider-specific behavior.

#### Anti-patterns to avoid

- **Calling real provider binaries**: Never let tests hit `claude`, `codex`, or `opencode` binaries. CI doesn't have them.
- **Trivial field assertion tests**: Don't test that `TaskState(type="task").type == "task"`. Pydantic guarantees this.
- **isinstance checks**: Don't test `isinstance(generate_thread_id(), str)`. The type system handles this.
- **Default value tests**: Don't test that `ClaudeOptions().permission_mode is None`. This is framework behavior.
- **Unnecessary real-time waits**: Mock `time.sleep` for in-process delays. Minimize subprocess sleep durations.
- **Writing artifacts to project root**: Tests must never create `.fdsx/` artifacts in the project root. Always use `monkeypatch.chdir(tmp_path)` or `cwd=tmp_dir` for subprocess tests.

#### Naming conventions

- Test files: `test_<feature>.py` (never `test_phase1.py` or `test_e2e_phase2.py`)
- Test functions: `test_<scenario>_<expected_outcome>`
- Test classes: `Test<Feature><Aspect>` (e.g., `TestCheckpointResume`, `TestChoiceStateValidation`)

#### Integration Test Feature-Centeredness Assessment

All integration tests are already feature-centered. No files need restructuring beyond the e2e moves. Each integration test file is organized around a specific feature:

- `test_checkpoint_resume.py` — checkpoint and resume behavior
- `test_choice_flow.py` — choice state routing
- `test_parallel_flow.py` — parallel execution
- `test_linear_flow.py` — linear workflow execution
- `test_loop_flow.py` — loop state behavior
- `test_extraction_flow.py` — data extraction
- `test_quiet_mode.py` — quiet mode flag
- `test_result_file.py` — result file output
- `test_resume_interrupt.py` — interrupted workflow recovery
- `test_split.py` — batch split behavior
- `test_tasks_dir.py` — tasks directory handling
- `test_auto_select.py` — auto-select logic
- `test_lock_atomicity.py` — lock file atomicity
- `test_workflow_persistence.py` — workflow state persistence
- `test_inactivity_timeout.py` — inactivity timeout handling
- `test_scenario_flows.py` — cross-cutting scenario flows

The integration test suite is well-organized around features (checkpoint, choice, parallel, loop, extraction, etc.) rather than implementation phases. No restructuring was required.

## Error Handling

1. **Never leak implementation exceptions across module boundaries.** Catch low-level errors (`json.JSONDecodeError`, `KeyError`, `yaml.YAMLError`) and re-raise as domain-specific exceptions (e.g., `FlowValidationError`, `CheckpointCorruptedError`). Raw tracebacks from stdlib or third-party libs should not reach callers outside the module.
2. **Never use `assert` for production precondition checks.** `python -O` disables assertions. Use `if not condition: raise ValueError(...)` instead.
3. **Log at WARNING or ERROR before re-raising at module boundaries.** Log at DEBUG for internal state transitions. This ensures boundary errors are always observable in logs even if the caller swallows them.
4. **Only catch broad `Exception` at the top-level CLI entry point** (`cli/main.py`) to emit a user-facing message. Everywhere else, catch specific exception types.

## Logging

This project uses `structlog` for structured logging. Follow these conventions:

1. **Bind shared context once at flow start** using `structlog.contextvars.bind_contextvars(thread_id=..., flow_name=...)` so all subsequent log calls in that execution inherit those fields automatically.
2. **Emit one structured summary log line at flow completion** with fields: `thread_id`, `flow_name`, `status`, `duration_seconds`, `states_run`.
3. **Never use f-strings as the first argument to log calls.** Use keyword arguments: `log.info("state_entered", state=name)` not `log.info(f"entered {name}")`.
4. **Log levels:**
   - `DEBUG` — internal transitions, variable resolution, checkpoint reads
   - `INFO` — state entry/exit, flow start/completion
   - `WARNING` — retries, recoverable errors, deprecated usage
   - `ERROR` — unrecoverable failures, corrupt state

## Architecture

### Context

fdsx is a framework that executes multi-step AI agent workflows defined in declarative YAML. It compiles workflow definitions into LangGraph state machines, executes them by invoking LLM CLI tools (`claude`, `codex`, `opencode`) or shell commands as subprocesses, and manages checkpoint/resume across runs.

### Building Blocks

| Module | Responsibility | Does NOT |
|---|---|---|
| `cli/` | Parse CLI arguments (Typer), dispatch to engine | Own business logic; validate workflow semantics |
| `models/` | Pydantic models for workflow YAML (Flow, TaskState, ChoiceState, ParallelState, etc.) | Execute anything; access filesystem |
| `core/loader.py` | Load YAML, parse into Pydantic models, resolve profiles | Compile graphs; run workflows |
| `core/compiler/` | Compile a `Flow` model into a LangGraph `StateGraph` with nodes and edges | Load YAML; execute the graph; manage checkpoints |
| `core/engine/` | Execute compiled graphs (`run_flow`, `resume_flow`), handle signals, batch/tasks-dir orchestration | Compile the graph; own the provider abstraction |
| `core/variables.py` | JSONPath variable resolution and substitution in prompts | Persist state; know about providers |
| `providers/` | Adapter layer: invoke LLM CLIs or system commands as subprocesses, return `ProviderResult` | Parse YAML; know about graph structure or state |
| `checkpoint/` | SQLite-backed checkpoint persistence via LangGraph's `CheckpointSaver` | Execute workflows; compile graphs |
| `display/` | Terminal UI output (progress, summaries, prompts) to stderr | Own state; make decisions |
| `logging/` | Run recording, stream logging to stderr and structured log files | Control flow; modify state |
| `notify/` | Optional notification dispatch (e.g., on completion) | Block execution; own state |

### Runtime View

```
YAML file
  → loader.py (parse + validate + resolve profiles)
  → compiler/ (build StateGraph with nodes/edges)
  → engine/run.py (execute graph with checkpoint saver)
      → providers/ (subprocess call per task state)
      → checkpoint/ (persist after each state)
      → display/ (render progress to stderr)
  → engine/results.py (extract final results)
```

### Key Decisions

- **Subprocesses, not SDKs**: Providers invoke CLI binaries via `subprocess.run`, keeping fdsx independent of any LLM SDK.
- **LangGraph as runtime**: The compiled `StateGraph` handles state transitions, interrupts, and checkpoint integration.
- **Stderr for UI**: All human-facing output goes to stderr so stdout remains clean for machine-readable results.

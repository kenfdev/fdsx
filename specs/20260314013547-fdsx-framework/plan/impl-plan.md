# Implementation Plan: fdsx Framework

**Spec**: [../spec.md](../spec.md)
**Created**: 2026-03-15
**Branch**: `feat/fdsx-framework`

## Technical Context

| Decision | Choice | Rationale |
|---|---|---|
| Runtime | LangGraph (thin wrapper) | Compile YAML → StateGraph. Use public API only. |
| Python version | ≥3.10 | LangGraph minimum requirement |
| Project structure | Modular `src/fdsx/` | core/, cli/, models/, providers/, checkpoint/, notify/, display/, logging/ |
| Validation | Pydantic v2 models | Schema validation + internal data model. Already a LangGraph dep. |
| CLI | Typer | As specified in dependencies |
| Checkpointing | LangGraph SqliteSaver | `langgraph-checkpoint-sqlite` package |
| Parallel execution | LangGraph Send API | Fan-out/fan-in pattern |
| HTTP client | httpx | Webhook notifications |
| JSONPath | Custom (~30 LOC) | Only simple patterns needed |
| Logging | structlog | Structured JSON for run logs |
| Build tooling | uv + pyproject.toml | Modern, fast, lockfile support |
| Testing | Unit + integration from start | Mocks for unit, echo/cat-based providers for integration |
| CLI output language | English | Standard for open-source |

## Dependencies

```toml
[project]
requires-python = ">=3.10"

[project.dependencies]
langgraph = ">=1.0,<2"
langgraph-checkpoint-sqlite = ">=3,<5"
pyyaml = ">=6"
typer = ">=0.9"
httpx = ">=0.27"
structlog = ">=24"

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
    "mypy>=1.10",
]
```

## Implementation Phases (MVP-first)

### Phase 1: Core Engine MVP
**Goal**: `fdsx run` executes a linear flow of Task states with a single provider.

**Deliverables**:
1. **Pydantic models** (`src/fdsx/models/flow.py`)
   - Flow, TaskState, ChoiceState (all state types as models, but only Task + Choice implemented)
   - Validation rules: start_at exists, next references valid, mutual exclusions

2. **YAML loader + validator** (`src/fdsx/core/loader.py`)
   - Parse YAML → Pydantic models
   - `fdsx validate` command

3. **Variable resolution** (`src/fdsx/core/variables.py`)
   - `{variable}` safe substitution in prompt_template
   - `$.path` resolution for result_path (get/set)
   - Static analysis: detect unreachable variable references

4. **Provider adapters** (`src/fdsx/providers/`)
   - Base adapter interface
   - `claude` provider: `claude -p "{prompt}" --model {model}`
   - `system` provider: shell command execution
   - Provider CLI existence check (PATH validation)

5. **Flow compiler** (`src/fdsx/core/compiler.py`)
   - YAML → LangGraph StateGraph
   - Task state → LangGraph node (subprocess call)
   - Choice state → conditional edges
   - Linear `next` → edges

6. **CLI** (`src/fdsx/cli/main.py`)
   - `fdsx run <workflow.yaml>` — basic execution
   - `fdsx run --input key=value` — input variables
   - `fdsx validate <workflow.yaml>`
   - Thread ID generation + display

7. **Basic terminal output** (`src/fdsx/display/terminal.py`)
   - State transition lines (▶ started, ✓ completed)
   - LLM output streaming (line-buffered)

**Tests**:
- Unit: Pydantic model validation, variable resolution, extraction logic
- Integration: End-to-end flow with `system` provider (echo commands)

### Phase 2: Extraction + Parallel + Pass
**Goal**: Support multi-LLM parallel review with extraction and aggregation.

**Deliverables**:
1. **Output extraction** (`src/fdsx/core/extraction.py`)
   - json strategy (code block → parse → field lookup)
   - regex strategy
   - keyword strategy
   - Fallback chain execution
   - 2-phase LLM classify fallback

2. **Parallel state** (compiler + engine)
   - Compile Parallel → LangGraph Send API fan-out
   - Per-branch result collection as array
   - min_success enforcement
   - Per-branch retry (failed only)
   - Branch status display

3. **Pass state + aggregation**
   - Parameters mapping
   - Aggregate block: majority/all/any strategies

4. **Additional providers**
   - `opencode` adapter
   - `codex` adapter

5. **Loop control**
   - Global loop counter tracking
   - max_loop enforcement via LangGraph recursion_limit
   - "Loop completed" graceful stop

**Tests**:
- Unit: extraction strategies (json/regex/keyword), aggregation strategies
- Integration: parallel flow with system provider, loop flow

### Phase 3: Wait + Checkpoint/Resume
**Goal**: Human-in-the-loop approval gates and crash-resilient execution.

**Deliverables**:
1. **Wait state** (compiler + engine)
   - Compile Wait → LangGraph node with `interrupt()`
   - Terminal prompt display with choices
   - Result storage at result_path

2. **Webhook notifications** (`src/fdsx/notify/webhook.py`)
   - httpx POST to webhook URL
   - Template variable substitution in message
   - Failure → warning log, continue flow

3. **Checkpoint/Resume**
   - SqliteSaver configuration (`.fdsx/checkpoints/` directory)
   - `fdsx resume --thread-id` command
   - PID-based lock file for concurrent execution prevention
   - Stale lock detection (PID alive check)
   - Checkpoint integrity verification on resume

4. **`fdsx list` command**
   - Read checkpoint store for known thread IDs
   - PID-based status detection (running/waiting/stopped)

5. **prompt_file support**
   - Load prompt from external file
   - Resolve path relative to YAML file
   - Variable substitution within loaded file content

**Tests**:
- Unit: webhook sending (mocked), lock file logic
- Integration: interrupt/resume flow, checkpoint corruption handling

### Phase 4: Batch Tasks + Polish + Publish
**Goal**: Batch execution, structured logging, PyPI publish.

**Deliverables**:
1. **Batch task execution** (FR-13)
   - `--tasks <file>` option
   - task_splitter LLM call for file splitting
   - User confirmation prompt
   - Sequential task execution with independent thread IDs
   - Failure handling (continue/stop prompt)
   - Results summary

2. **Structured logging** (`src/fdsx/logging/recorder.py`)
   - JSON run log to `runs/<thread_id>.json`
   - Per-state input/output/duration recording
   - Resume appends to existing log

3. **Parallel display improvements**
   - Status line updates during parallel execution
   - Post-completion branch output display

4. **PyPI packaging**
   - pyproject.toml finalization
   - `fdsx` CLI entry point
   - README with quickstart
   - `pip install fdsx` ready

5. **Edge case hardening**
   - Timeout handling (timeout_seconds)
   - Exponential backoff for retries
   - `--input` / `--tasks` mutual exclusion validation

**Tests**:
- Integration: batch task flow, logging output format
- End-to-end: full scenario flows from spec (Scenarios 1-5)

## File Structure

```
fdsx/
├── pyproject.toml
├── uv.lock
├── src/
│   └── fdsx/
│       ├── __init__.py
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py              # Typer app: run, resume, validate, list
│       ├── core/
│       │   ├── __init__.py
│       │   ├── compiler.py          # YAML Flow → LangGraph StateGraph
│       │   ├── engine.py            # High-level execution orchestration
│       │   ├── extraction.py        # json/regex/keyword extraction + LLM fallback
│       │   ├── loader.py            # YAML parse → Pydantic validation
│       │   └── variables.py         # {var} substitution + $.path resolution
│       ├── models/
│       │   ├── __init__.py
│       │   └── flow.py              # Pydantic models for all YAML types
│       ├── providers/
│       │   ├── __init__.py
│       │   ├── base.py              # Provider protocol/base class
│       │   ├── claude.py
│       │   ├── opencode.py
│       │   ├── codex.py
│       │   └── system.py
│       ├── checkpoint/
│       │   ├── __init__.py
│       │   └── manager.py           # SqliteSaver wrapper + PID lock
│       ├── notify/
│       │   ├── __init__.py
│       │   └── webhook.py           # httpx webhook POST
│       ├── display/
│       │   ├── __init__.py
│       │   └── terminal.py          # Streaming output + status lines
│       └── logging/
│           ├── __init__.py
│           └── recorder.py          # Structured JSON run logging
├── tests/
│   ├── unit/
│   │   ├── test_models.py
│   │   ├── test_variables.py
│   │   ├── test_extraction.py
│   │   ├── test_aggregation.py
│   │   └── test_loader.py
│   ├── integration/
│   │   ├── test_linear_flow.py
│   │   ├── test_choice_flow.py
│   │   ├── test_parallel_flow.py
│   │   ├── test_wait_resume.py
│   │   └── test_batch.py
│   └── fixtures/
│       ├── simple_flow.yaml
│       ├── parallel_review.yaml
│       ├── wait_approval.yaml
│       └── invalid_flows/
└── specs/
    └── 20260314013547-fdsx-framework/
        ├── spec.md
        └── plan/
            ├── impl-plan.md          # This file
            ├── research.md
            ├── data-model.md
            └── contracts/
                ├── cli.md
                └── yaml-schema.md
```

## Key Design Decisions

### 1. YAML → LangGraph Compilation Strategy

Each fdsx state type maps to LangGraph constructs:

| fdsx State | LangGraph Construct |
|---|---|
| Task | Node function (subprocess call) |
| Choice | `add_conditional_edges` with routing function |
| Parallel | `Send` API for fan-out, `Annotated[list, operator.add]` for fan-in |
| Pass | Node function (pure data transformation) |
| Wait | Node function with `interrupt()` call |
| Loop | Back-edges in graph (Choice.next → earlier state) |

### 2. LangGraph State Schema

The compiled graph uses a single `TypedDict` state with dynamic fields:
```python
# Generated at compile time based on all result_path fields in the flow
class FlowState(TypedDict):
    plan: str                    # from $.plan
    implementation: str          # from $.implementation
    reviews: list[dict]          # from $.reviews
    decision: str                # from $.decision
    # ... etc
```

### 3. Provider Subprocess Execution

Each provider adapter:
1. Constructs the CLI command from provider/model/prompt
2. Runs via `subprocess.Popen` with line-buffered stdout
3. Streams output lines to display layer
4. Captures full output for result_path storage
5. Handles timeout (if configured) via `subprocess.TimeoutExpired`
6. Returns (exit_code, stdout, stderr)

### 4. Checkpoint Directory Layout

```
.fdsx/
├── checkpoints/
│   └── checkpoints.db          # SQLite database (SqliteSaver)
└── locks/
    └── <thread_id>.lock        # PID lock file
```

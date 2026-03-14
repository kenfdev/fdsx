# Research: fdsx Framework

## 1. LangGraph Integration

### Decision: Use LangGraph as thin-wrapper runtime
**Rationale**: LangGraph provides StateGraph, checkpointing (SqliteSaver), interrupt/resume, and parallel execution (Send API) — all features fdsx needs. Using only the public API minimizes coupling and makes upgrades easier.
**Alternatives considered**:
- Deep integration (subclassing LangGraph internals): rejected due to upgrade risk
- Custom runtime without LangGraph: rejected — would require reimplementing checkpoint, interrupt, and graph execution from scratch

### Key LangGraph APIs for fdsx

| fdsx Feature | LangGraph API | Notes |
|---|---|---|
| Flow compilation | `StateGraph`, `add_node`, `add_edge`, `add_conditional_edges` | Dynamic graph construction |
| Parallel execution | `Send` API + `add_conditional_edges` | Fan-out/fan-in pattern; each Send spawns a parallel node instance |
| Checkpointing | `SqliteSaver.from_conn_string(path)` | From `langgraph-checkpoint-sqlite` package |
| Wait/Human-in-the-loop | `interrupt()` + `Command(resume=value)` | Pauses graph, resumes with user input |
| Loop control | `recursion_limit` in config | Limits total graph steps; maps to `max_loop` |
| State management | `TypedDict` state schema | fdsx state = dict of variable bindings |

### LangGraph Version
- **langgraph** v1.1.2 (latest stable)
- **langgraph-checkpoint-sqlite** v3.0.3
- **Requires Python ≥3.10**

### Parallel Execution via Send API
```python
from langgraph.types import Send

def fan_out(state) -> list[Send]:
    return [Send("review_node", {"input": state["implementation"], "branch_id": i})
            for i in range(3)]

builder.add_conditional_edges("implement", fan_out, ["review_node"])
```
- Each `Send` creates a parallel instance of the target node
- Results are aggregated via `Annotated[list, operator.add]` in the state schema
- This maps directly to fdsx's Parallel state branches

### Interrupt/Resume for Wait State
```python
from langgraph.types import interrupt, Command

def wait_node(state):
    response = interrupt({"message": "Approve?", "choices": ["approve", "reject"]})
    return {"approval": response}

# Resume:
graph.invoke(Command(resume="approve"), config)
```
- `interrupt()` pauses the graph and surfaces a payload
- `Command(resume=value)` resumes from the interrupt point
- Works with SqliteSaver for persistence across process restarts

### Multiple Parallel Interrupts
- When parallel branches each call `interrupt()`, all interrupts are collected
- Resume with a map: `Command(resume={interrupt_id: value, ...})`

## 2. Python Version

### Decision: Python ≥3.10
**Rationale**: LangGraph requires ≥3.10. The spec said 3.11+ but LangGraph's own minimum is 3.10. Targeting 3.10 maximizes compatibility while meeting all dependency requirements.
**Alternatives considered**:
- Python 3.11+: unnecessarily restrictive; no features from 3.11 are needed
- Python 3.12+: too restrictive for adoption
- Python 3.13+: too new, limits users

## 3. HTTP Client for Webhooks

### Decision: httpx
**Rationale**: httpx (v0.28.1, BSD-3) provides both sync and async HTTP. fdsx needs sync POST for webhook notifications from within LangGraph nodes. httpx has a cleaner API than requests and is actively maintained. If LangGraph nodes are async in the future, httpx already supports it.
**Alternatives considered**:
- requests (v2.32.5): sync-only, equally viable but httpx is more modern
- urllib3 directly: too low-level

## 4. JSONPath Resolution

### Decision: Custom implementation (no library)
**Rationale**: fdsx uses only simple JSONPath patterns: `$.variable_name`, `$.field.subfield`, `$.array[0].field`. These are trivially implementable with Python's built-in string splitting and dict/list access (~30 lines of code). Adding jsonpath-ng (v1.8.0) for this would be over-engineering.
**Alternatives considered**:
- jsonpath-ng: full JSONPath spec support, but overkill for the simple patterns used
- jsonpath-python: lighter but still unnecessary

### Custom Implementation Approach
```python
def resolve_jsonpath(path: str, data: dict) -> Any:
    """Resolve $.field.subfield[0].name patterns."""
    if not path.startswith("$."):
        raise ValueError(f"Invalid JSONPath: {path}")
    parts = path[2:].split(".")
    current = data
    for part in parts:
        # Handle array indexing: field[0]
        if "[" in part:
            field, idx = part.split("[", 1)
            idx = int(idx.rstrip("]"))
            current = current[field][idx]
        else:
            current = current[part]
    return current
```

## 5. YAML Validation

### Decision: Pydantic models
**Rationale**: Pydantic v2 (already a LangGraph dependency) provides schema validation, type coercion, and clear error messages. The Pydantic models also serve as the internal data model for the compiled flow.
**Alternatives considered**:
- JSON Schema + jsonschema: extra dependency, less Pythonic
- Hand-written validation: more code, worse error messages

## 6. Logging

### Decision: structlog
**Rationale**: structlog provides structured JSON logging that maps directly to the `runs/<thread_id>.json` requirement. Context binding (thread_id, state_name, etc.) makes log correlation easy.
**Alternatives considered**:
- stdlib logging: no structured output without significant setup
- loguru: less structured, more opinionated formatting

## 7. CLI Framework

### Decision: Typer (as specified in spec dependencies)
**Rationale**: Typer provides a clean CLI interface with automatic help generation from type hints. Already specified in the spec.

## 8. Build Tooling

### Decision: uv + pyproject.toml
**Rationale**: uv is fast, modern, and has lockfile support. pyproject.toml is the standard Python packaging configuration.

## 9. Project Structure

### Decision: Modular packages under src/fdsx/
```
src/fdsx/
├── __init__.py
├── cli/              # Typer CLI commands
│   ├── __init__.py
│   └── main.py
├── core/             # Flow engine, compiler, state management
│   ├── __init__.py
│   ├── compiler.py   # YAML → LangGraph StateGraph
│   ├── engine.py     # Flow execution orchestration
│   ├── state.py      # State variable management
│   └── extraction.py # Output extraction (json/regex/keyword + LLM fallback)
├── models/           # Pydantic models for YAML schema
│   ├── __init__.py
│   └── flow.py
├── providers/        # CLI provider adapters
│   ├── __init__.py
│   ├── base.py
│   ├── claude.py
│   ├── opencode.py
│   ├── codex.py
│   └── system.py
├── checkpoint/       # Checkpoint management (PID lock, SqliteSaver wrapper)
│   ├── __init__.py
│   └── manager.py
├── notify/           # Webhook notification
│   ├── __init__.py
│   └── webhook.py
├── display/          # Terminal output (streaming, status lines)
│   ├── __init__.py
│   └── terminal.py
└── logging/          # Structured logging and run recording
    ├── __init__.py
    └── recorder.py
```

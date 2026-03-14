# Tasks: fdsx Framework — Phase 1 (Core Engine MVP)

**Spec**: [spec.md](spec.md)
**Plan**: [plan/impl-plan.md](plan/impl-plan.md)
**Scope**: Phase 1 only — `fdsx run` executes a linear flow of Task states with a single provider
**Primary Scenario**: Scenario 1 (Simple Task → Implementation → Review Flow)

---

## Phase 1: Setup

- [ ] T001 Initialize project with pyproject.toml, uv, and directory structure per plan in `pyproject.toml`
  - Create `pyproject.toml` with dependencies (langgraph, langgraph-checkpoint-sqlite, pyyaml, typer, httpx, structlog) and dev dependencies (pytest, pytest-asyncio, ruff, mypy)
  - Configure `[project.scripts]` entry point: `fdsx = "fdsx.cli.main:app"`
  - Use `src/` layout with `[tool.setuptools.packages.find] where = ["src"]`
  - Run `uv sync` to generate `uv.lock`

- [ ] T002 Create package `__init__.py` files for all modules in `src/fdsx/`
  - `src/fdsx/__init__.py`, `src/fdsx/cli/__init__.py`, `src/fdsx/core/__init__.py`, `src/fdsx/models/__init__.py`, `src/fdsx/providers/__init__.py`, `src/fdsx/checkpoint/__init__.py`, `src/fdsx/notify/__init__.py`, `src/fdsx/display/__init__.py`, `src/fdsx/logging/__init__.py`
  - Also create `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/fixtures/` directory

## Phase 2: Foundational

- [ ] T003 Implement Pydantic models for all state types in `src/fdsx/models/flow.py`
  - Define: `Flow`, `TaskState`, `ChoiceState`, `ChoiceRule`, `ParallelState`, `Branch`, `PassState`, `AggregateRule`, `WaitState`, `NotifyConfig`, `WebhookConfig`, `ExtractRule`, `LLMClassifyFallback`, `TaskSplitter`
  - Use discriminated union for `State` (discriminator: `type` field)
  - Implement validation rules: `start_at` must exist in `states`, all `next` references must be valid state keys, `prompt_template`/`prompt_file` mutual exclusion, `next`/`end` mutual exclusion, provider-specific field constraints (system requires `command`, forbids `prompt_template`/`prompt_file`/`model`; claude/opencode/codex require `prompt_template` or `prompt_file`, forbid `command`)
  - At least one state must have `end: true` or flow must reach termination
  - Reference: [plan/data-model.md](plan/data-model.md) for entity definitions, spec.md FR-14 for validation rules

- [ ] T004 Write unit tests for Pydantic models in `tests/unit/test_models.py`
  - Test valid flow creation with all state types
  - Test validation errors: missing `start_at` reference, invalid `next` references, `prompt_template` + `prompt_file` together, `next` + `end` together, system provider with `prompt_template`, claude provider without `prompt_template`/`prompt_file`
  - Test discriminated union dispatch (type=task → TaskState, type=choice → ChoiceState, etc.)

- [ ] T005 Implement variable resolution in `src/fdsx/core/variables.py`
  - `resolve_template(template: str, variables: dict) -> str`: custom safe substitution for `{variable}` patterns. Only replace registered variable names; preserve unknown `{...}` patterns as literals
  - Support dot access (`{review.decision}`) and index access (`{reviews[0].summary}`)
  - `resolve_jsonpath(path: str, data: dict) -> Any`: resolve `$.field.subfield[0].name` patterns (~30 LOC, no library)
  - `set_jsonpath(path: str, data: dict, value: Any) -> dict`: set value at JSONPath location
  - `analyze_variable_references(flow: Flow) -> list[str]`: static analysis — trace reachable states from `start_at`, check that `{variable}` references in `prompt_template` correspond to a `result_path` set by a preceding state on at least one reachable path. Return list of error messages for unreachable references
  - Reference: [plan/contracts/yaml-schema.md](plan/contracts/yaml-schema.md) for variable reference contract, research.md section 4 for implementation approach

- [ ] T006 Write unit tests for variable resolution in `tests/unit/test_variables.py`
  - Test `resolve_template`: simple variable, dot access, index access, unknown pattern preserved, missing variable preserved
  - Test `resolve_jsonpath`: simple field, nested field, array indexing, invalid path
  - Test `set_jsonpath`: set new field, set nested field
  - Test `analyze_variable_references`: valid flow (no errors), unreachable variable reference detected

## Phase 3: US1 — Linear Flow Execution (Scenario 1)

**Story Goal**: Define a 3-step flow of Plan → Implement → Review in YAML and execute it from the CLI. Each state invokes a provider CLI, saves results to variables, and transitions to the next state.

**Independent Test Criteria**:
- `fdsx validate` accepts a valid linear YAML flow and rejects invalid ones
- `fdsx run` executes a linear flow with `system` provider (echo commands) end-to-end
- State transitions are displayed in the terminal
- Final flow result is output as JSON

- [ ] T007 [US1] Implement YAML loader and validator in `src/fdsx/core/loader.py`
  - `load_flow(path: Path) -> Flow`: parse YAML file → Pydantic model validation → return `Flow`
  - Run variable reference static analysis (from `variables.py`) as part of validation
  - Check provider CLI existence on PATH for all providers used in the flow
  - Return clear error messages with context on validation failure
  - Resolve `prompt_file` paths relative to the YAML file location; check file existence

- [ ] T008 [US1] Write unit tests for YAML loader in `tests/unit/test_loader.py`
  - Test loading a valid simple flow YAML
  - Test validation error: malformed YAML
  - Test validation error: schema violation (missing required fields)
  - Test validation error: unreachable variable reference
  - Test `prompt_file` resolution relative to YAML location

- [ ] T009 [US1] Implement provider base interface in `src/fdsx/providers/base.py`
  - Define `Provider` protocol/ABC with method: `execute(prompt: str, model: str | None, timeout: int | None) -> ProviderResult`
  - Define `ProviderResult` dataclass: `exit_code: int`, `stdout: str`, `stderr: str`
  - `check_cli_exists(command: str) -> bool`: check if CLI is on PATH
  - `get_provider(name: str) -> Provider`: factory function returning the correct provider adapter

- [ ] T010 [US1] Implement system provider in `src/fdsx/providers/system.py`
  - Execute shell commands via `subprocess.Popen` with line-buffered stdout
  - Capture stdout and stderr
  - Handle timeout via `subprocess.TimeoutExpired` (if `timeout_seconds` is set)
  - Return `ProviderResult` with exit code, stdout, stderr
  - Non-zero exit codes are treated as failures

- [ ] T011 [US1] Implement claude provider in `src/fdsx/providers/claude.py`
  - Construct command: `claude -p "{prompt}" --model {model}`
  - Execute via `subprocess.Popen` with line-buffered stdout
  - Stream output lines to a callback (for display layer)
  - Handle timeout
  - Return `ProviderResult`

- [ ] T012 [US1] Implement flow compiler in `src/fdsx/core/compiler.py`
  - `compile_flow(flow: Flow) -> CompiledGraph`: compile Flow → LangGraph `StateGraph`
  - Generate `TypedDict` state schema dynamically based on all `result_path` fields
  - Task state → LangGraph node function (calls provider, stores result at `result_path`)
  - Choice state → `add_conditional_edges` with routing function based on `choices` rules
  - Linear `next` → `add_edge`
  - `end: true` → edge to `END`
  - Set `start_at` as graph entry point
  - Reference: impl-plan.md "Key Design Decisions" section 1 for mapping table, section 2 for state schema

- [ ] T013 [US1] Implement execution engine in `src/fdsx/core/engine.py`
  - `run_flow(flow_path: Path, inputs: dict[str, str] | None, thread_id: str | None) -> dict`: high-level orchestration
  - Load flow → compile → execute graph with LangGraph `.invoke()`
  - Generate UUID thread ID if not provided
  - Pass input variables into initial state
  - Return final state variables as result dict
  - Handle errors: wrap LangGraph exceptions with user-friendly messages

- [ ] T014 [US1] Implement basic terminal display in `src/fdsx/display/terminal.py`
  - `display_state_start(state_name: str, state_type: str, provider: str | None)`: print `[HH:MM:SS] ▶ state_name (type/provider/model)`
  - `display_state_complete(state_name: str, duration_seconds: float)`: print `[HH:MM:SS] ✓ state_name completed (Xs)`
  - `display_state_error(state_name: str, error: str)`: print `[HH:MM:SS] ✗ state_name failed`
  - `display_output_line(line: str)`: stream LLM output line to terminal (line-buffered)
  - Output to stderr (stdout reserved for final JSON result per CLI contract)
  - Reference: [plan/contracts/cli.md](plan/contracts/cli.md) for terminal output format

- [ ] T015 [US1] Implement CLI commands in `src/fdsx/cli/main.py`
  - Create Typer app with commands:
    - `fdsx run <workflow.yaml> [--thread-id TEXT] [--input KEY=VALUE (repeatable)]`: load, validate, compile, execute flow. Print thread ID at start. Print final JSON result to stdout on completion. Exit codes: 0=success, 1=flow error, 2=validation error
    - `fdsx validate <workflow.yaml>`: validate only, print errors to stderr. Exit codes: 0=valid, 2=validation errors
  - Parse `--input` as `key=value` pairs into dict
  - Reference: [plan/contracts/cli.md](plan/contracts/cli.md) for full CLI contract

- [ ] T016 [US1] Create test fixture YAML files in `tests/fixtures/`
  - `tests/fixtures/simple_flow.yaml`: 3-state linear flow (plan → implement → review) using `system` provider with echo commands
  - `tests/fixtures/choice_flow.yaml`: linear flow with a Choice state branching on a variable value (using system provider)
  - `tests/fixtures/invalid_flows/missing_start_at.yaml`: flow where `start_at` references non-existent state
  - `tests/fixtures/invalid_flows/bad_next_ref.yaml`: flow where `next` references non-existent state
  - `tests/fixtures/invalid_flows/mutual_exclusive.yaml`: flow with both `prompt_template` and `prompt_file`

- [ ] T017 [US1] Write integration test for linear flow execution in `tests/integration/test_linear_flow.py`
  - Test end-to-end: load `simple_flow.yaml` → compile → execute → verify all 3 states ran
  - Verify state variables are set correctly after each state
  - Verify final result dict contains all expected variables
  - Use `system` provider (echo commands) so no real LLM is needed

- [ ] T018 [US1] Write integration test for choice flow in `tests/integration/test_choice_flow.py`
  - Test: load `choice_flow.yaml` → execute → verify correct branch taken based on variable value
  - Test: default branch is taken when no condition matches

## Phase 4: Polish & Cross-Cutting

- [ ] T019 Configure ruff and mypy in `pyproject.toml`
  - Add `[tool.ruff]` section with src directory, target Python 3.10
  - Add `[tool.mypy]` section with strict mode for `src/fdsx/`
  - Ensure `uv run ruff check src/ tests/` passes
  - Ensure `uv run mypy src/fdsx/` passes

- [ ] T020 Verify full Scenario 1 flow from CLI in `tests/integration/test_cli_e2e.py`
  - Test `fdsx validate` with valid flow → exit code 0
  - Test `fdsx validate` with invalid flow → exit code 2, error message on stderr
  - Test `fdsx run` with `simple_flow.yaml` → exit code 0, JSON output on stdout
  - Test `fdsx run --input task="hello"` passes input variable to flow
  - Use `subprocess.run` to invoke the actual CLI entry point

---

## Dependencies

```
T001 → T002 → T003 → T004
                T005 → T006
T004 + T006 → T007 → T008
              T009 → T010, T011
T007 + T010 + T011 → T012 → T013
              T014 (independent after T002)
T013 + T014 + T015 → T016 → T017, T018
T017 + T018 → T019 → T020
```

**Critical path**: T001 → T002 → T003 → T005 → T007 → T009 → T012 → T013 → T015 → T017 → T020

## Implementation Strategy

- **MVP**: Complete through T017 (linear flow execution with system provider). This enables `fdsx run` on a simple YAML flow.
- **Incremental delivery**: Each task produces a testable artifact. After T015, the CLI is functional for basic flows.
- **Testing**: Unit tests (T004, T006, T008) validate individual components. Integration tests (T017, T018) validate end-to-end behavior. CLI e2e test (T020) validates the full user-facing experience.
- **No real LLMs needed**: All tests use the `system` provider (echo/cat commands) so no API keys or LLM access is required during development.

## Summary

| Metric | Value |
|---|---|
| Total tasks | 20 |
| Setup tasks | 2 (T001-T002) |
| Foundational tasks | 4 (T003-T006) |
| US1 tasks | 12 (T007-T018) |
| Polish tasks | 2 (T019-T020) |
| Unit test tasks | 3 (T004, T006, T008) |
| Integration test tasks | 3 (T017, T018, T020) |

## Suggested takt Usage

```bash
# Phase 1: Setup
takt run code "Initialize fdsx project: pyproject.toml with dependencies, uv sync, create src/fdsx/ directory structure with all __init__.py files"

# Phase 2: Foundational — Models
takt run code "Implement Pydantic models for fdsx flow definition in src/fdsx/models/flow.py and unit tests in tests/unit/test_models.py"

# Phase 2: Foundational — Variables
takt run code "Implement variable resolution (template substitution, JSONPath, static analysis) in src/fdsx/core/variables.py and unit tests in tests/unit/test_variables.py"

# Phase 3: US1 — Loader + Providers
takt run code "Implement YAML loader in src/fdsx/core/loader.py, provider base in src/fdsx/providers/base.py, system provider in src/fdsx/providers/system.py, claude provider in src/fdsx/providers/claude.py, and unit tests in tests/unit/test_loader.py"

# Phase 3: US1 — Compiler + Engine + Display + CLI
takt run code "Implement flow compiler in src/fdsx/core/compiler.py, engine in src/fdsx/core/engine.py, terminal display in src/fdsx/display/terminal.py, and CLI in src/fdsx/cli/main.py"

# Phase 3: US1 — Integration Tests
takt run code "Create test fixture YAMLs in tests/fixtures/, write integration tests in tests/integration/test_linear_flow.py and test_choice_flow.py"

# Phase 4: Polish
takt run code "Configure ruff and mypy in pyproject.toml, write CLI e2e test in tests/integration/test_cli_e2e.py, ensure all checks pass"
```

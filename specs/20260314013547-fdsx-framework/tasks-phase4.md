# Tasks: fdsx Framework — Phase 4 (Batch Tasks + Polish + Publish)

**Spec**: [spec.md](spec.md)
**Plan**: [plan/impl-plan.md](plan/impl-plan.md)
**Scope**: Phase 4 — Batch task execution, structured logging, parallel display improvements, PyPI packaging, edge case hardening
**Primary Scenarios**: All Scenarios 1-5 (comprehensive e2e validation before publish)
**Prerequisite**: All Phase 1-3 tasks (T001–T053) completed

---

## Phase 11: US6 — Structured Logging (FR-11)

**Story Goal**: Record per-state input/output/duration to a JSON run log at `runs/<thread_id>.json`. On resume, append to the existing log. Structured to enable future Web UI visualization.

**Independent Test Criteria**:
- `runs/<thread_id>.json` is created on flow execution with correct schema
- Each state entry records name, type, started_at, completed_at, duration_seconds, status, output_preview, variables_set
- Parallel state entries include branch-level details (index, provider, status, duration)
- On resume, new state entries are appended to the existing log file
- final_variables section contains the complete state dict at flow completion

- [x] T054 [US6] Implement run log recorder in `src/fdsx/logging/recorder.py`
  - `RunRecorder` class:
    - `__init__(thread_id: str, flow_name: str, flow_version: str | None = None)`: initialize log structure with thread_id, flow_name, flow_version, started_at (ISO 8601), status="running", states=[]
    - `record_state_start(state_name: str, state_type: str) -> None`: append new state entry with name, type, started_at
    - `record_state_complete(state_name: str, status: str, output: str, variables_set: list[str], branches: list[dict] | None = None) -> None`: update the state entry with completed_at, duration_seconds (computed), status, output_preview (first 500 chars), variables_set, branches (for parallel states)
    - `record_state_error(state_name: str, error: str) -> None`: update the state entry with status="error", error message
    - `finalize(final_variables: dict, status: str = "completed") -> None`: set completed_at, status, final_variables on the log
    - `save(base_dir: Path | None = None) -> Path`: write JSON to `runs/<thread_id>.json` relative to CWD (or base_dir). Create `runs/` directory if not exists. If file already exists (resume), load existing log, append new state entries, update completed_at/status/final_variables
    - `to_dict() -> dict`: return the full log as a dict
  - Reference: contracts/cli.md "Run Log Format" for exact JSON schema

- [x] T055 [P] [US6] Write unit tests for run log recorder in `tests/unit/test_recorder.py`
  - Test `RunRecorder` initialization: correct fields, started_at is ISO 8601
  - Test `record_state_start` + `record_state_complete`: state entry has all required fields
  - Test `output_preview`: output longer than 500 chars is truncated
  - Test `record_state_error`: state entry has status="error" and error message
  - Test `finalize`: sets completed_at, status, final_variables
  - Test `save`: creates `runs/` directory and writes JSON file
  - Test `save` on resume: existing log file is loaded, new states appended, metadata updated
  - Test parallel state recording: branches list is included in state entry

- [x] T056 [US6] Integrate run log recorder into engine in `src/fdsx/core/engine.py`
  - Import `RunRecorder` and create instance at start of `run_flow`
  - Call `record_state_start` before each state execution (hook into compiler or engine event flow)
  - Call `record_state_complete` after each state succeeds
  - Call `record_state_error` when a state fails
  - Call `finalize` + `save` at flow completion (both success and error)
  - In `resume_flow`: load existing log and append new state entries
  - Pass `RunRecorder` through compiler node functions via state dict or closure
  - Ensure parallel branches record branch-level details

## Phase 12: US7 — Batch Task Execution (FR-13)

**Story Goal**: Accept a task file via `--tasks <file>`, use an LLM (specified by `task_splitter`) to split it into individual tasks, prompt the user for confirmation, then execute the workflow sequentially for each task with independent thread IDs. Handle failures with continue/stop prompts.

**Independent Test Criteria**:
- `fdsx run workflow.yaml --tasks tasks.md` reads the task file and invokes the task_splitter LLM
- The split task list is displayed to the user for confirmation (approve/reject)
- Each task executes the full workflow with an independent thread_id
- If a task fails, the user is prompted to continue or stop
- A results summary is displayed at the end
- `--input` and `--tasks` are mutually exclusive (validation error if both provided)

- [ ] T057 [US7] Implement batch task splitter in `src/fdsx/core/batch.py`
  - `split_tasks(task_content: str, flow: Flow, task_splitter: TaskSplitter) -> list[str]`: invoke the task_splitter LLM to split the task file content into individual task descriptions
    - Build a prompt that includes the task file content and the workflow definition (state names, input variables) to guide the LLM on appropriate granularity
    - Call the provider specified by `task_splitter.provider` with `task_splitter.model`
    - Parse the LLM response: expect one task per line (or numbered list), strip empty lines
    - Return list of task description strings
  - `display_task_list(tasks: list[str]) -> bool`: display the split tasks in numbered format to stderr, prompt user for confirmation (approve/reject). Return True if approved, False if rejected
  - `display_batch_summary(results: list[dict]) -> None`: display a summary table of all task results to stderr (task index, description preview, status, thread_id)
  - Reference: spec.md FR-13 for batch execution behavior

- [ ] T058 [P] [US7] Write unit tests for batch task splitter in `tests/unit/test_batch.py`
  - Test `split_tasks`: mock provider returns numbered task list → parses correctly into list of strings
  - Test `split_tasks`: mock provider returns empty response → returns empty list
  - Test `display_task_list`: mock stdin "y" → returns True
  - Test `display_task_list`: mock stdin "n" → returns False
  - Test `display_batch_summary`: verify output format includes task index, description, status, thread_id

- [ ] T059 [US7] Implement batch execution orchestrator in `src/fdsx/core/engine.py`
  - `run_batch(workflow_path: Path, tasks_file: Path, base_dir: Path | None = None) -> list[dict]`: orchestrate batch execution
    - Load and validate the flow YAML
    - Verify `task_splitter` is defined in the flow; raise error if missing
    - Read the tasks file content
    - Call `split_tasks` to get the task list
    - Call `display_task_list` for user confirmation; abort if rejected
    - For each task: generate independent thread_id, call `run_flow` with `{"task": task_description}` as input
    - On task failure: display error, prompt user to continue or stop remaining tasks
    - Return list of `{task_index, task_description, thread_id, status, error}` dicts
    - Call `display_batch_summary` at the end
  - Reference: spec.md FR-13, clarification "approve/reject only"

- [ ] T060 [US7] Add `--tasks` option to CLI and validate mutual exclusion in `src/fdsx/cli/main.py`
  - Add `--tasks` option to the `run` command: `tasks_file: Path | None = typer.Option(None, "--tasks", help="Batch task file path")`
  - Validate `--input` and `--tasks` mutual exclusion: if both provided, print error and exit with code 2
  - When `--tasks` is provided: call `engine.run_batch(workflow, tasks_file, base_dir)` instead of `engine.run_flow`
  - Print batch results summary JSON to stdout on completion
  - Exit code: 0 if all tasks succeed, 1 if any task failed

- [ ] T061 [US7] Create batch execution test fixtures in `tests/fixtures/`
  - `tests/fixtures/batch_flow.yaml`: simple 2-state flow (Plan → Implement) using system provider with `task_splitter: {provider: system, model: default}` and system command that echoes task descriptions
  - `tests/fixtures/sample_tasks.md`: sample task file with 3 task descriptions for testing batch splitting

- [ ] T062 [US7] Write integration test for batch execution in `tests/integration/test_batch.py`
  - Test full batch flow: mock the task_splitter provider to return 3 tasks → approve → all 3 execute with independent thread_ids
  - Test batch rejection: mock stdin to reject task list → verify no tasks executed
  - Test batch failure handling: mock one task to fail → prompt to continue → remaining tasks execute
  - Test `--input` + `--tasks` mutual exclusion: verify exit code 2 and error message
  - Test missing `task_splitter`: flow without task_splitter field + `--tasks` option → clear error message

## Phase 13: Edge Case Hardening

**Story Goal**: Add exponential backoff for retries and ensure timeout handling is robust across all retry paths.

- [ ] T063 Implement exponential backoff for retry loops in `src/fdsx/core/compiler.py`
  - Modify the retry loop in `_create_task_node` to add exponential backoff delay between retries: `time.sleep(min(2 ** attempt, 30))` (1s, 2s, 4s, 8s... capped at 30s)
  - Apply the same backoff to the retry loop in `_create_branch_executor` for parallel branch retries
  - Add `import time` at the top of the file
  - Ensure the first attempt has no delay (backoff starts from the second attempt)

- [ ] T064 [P] Write unit test for exponential backoff in `tests/unit/test_backoff.py`
  - Test: mock `time.sleep` and verify backoff delays for 3 retries → sleep(1), sleep(2), sleep(4)
  - Test: verify first attempt has no delay
  - Test: verify backoff is capped at 30 seconds for high retry counts
  - Test: verify timeout_seconds triggers subprocess.TimeoutExpired which is caught and retried with backoff

## Phase 14: Parallel Display Improvements

**Story Goal**: Enhance terminal output during parallel execution with real-time status line updates and post-completion branch output display.

- [ ] T065 Implement parallel status line updates in `src/fdsx/display/terminal.py`
  - `display_branch_start(state_name: str, branch_index: int, provider: str, model: str | None) -> None`: print `  [branch-N] provider/model  ⏳ running...` to stderr
  - `display_branch_complete(state_name: str, branch_index: int, provider: str, duration: float) -> None`: print `  [branch-N] provider/model  ✓ completed (Xs)` to stderr
  - `display_branch_failed(state_name: str, branch_index: int, provider: str) -> None`: print `  [branch-N] provider/model  ✗ failed` to stderr
  - `display_parallel_results(state_name: str, branch_results: list[dict]) -> None`: after all branches complete, display each branch's output with header `--- branch-N (provider/model) ---`
  - Reference: contracts/cli.md "Parallel Execution Status" for exact format

- [ ] T066 Integrate parallel display callbacks into compiler in `src/fdsx/core/compiler.py`
  - In `_create_branch_executor`: call `display_branch_start` before provider execution
  - On branch success: call `display_branch_complete` with duration
  - On branch failure: call `display_branch_failed`
  - In fan-in collector: call `display_parallel_results` with all branch outputs
  - Import display functions from `src/fdsx/display/terminal.py`

## Phase 15: PyPI Packaging (FR-12)

**Story Goal**: Finalize pyproject.toml, create README with quickstart, and ensure `pip install fdsx` works.

- [ ] T067 Finalize pyproject.toml for PyPI publishing in `pyproject.toml`
  - Add/verify required PyPI fields: `description`, `authors`, `license`, `readme`, `classifiers`, `urls` (homepage, repository)
  - Verify `[project.scripts]` entry point: `fdsx = "fdsx.cli.main:app"`
  - Verify all runtime dependencies are correctly listed
  - Add `[build-system]` section if missing (setuptools or hatchling)
  - Test: `uv build` produces a valid wheel and sdist

- [ ] T068 Write README with quickstart in `README.md`
  - Include: project overview (what fdsx is), installation (`pip install fdsx`), quickstart (create a simple YAML flow + run it), feature overview (state types, parallel execution, checkpointing, batch tasks), CLI reference table, example workflow YAML
  - Keep concise and practical — focus on getting users running in under 5 minutes
  - Reference: spec.md Overview and Scenarios for content

## Phase 16: Polish & Cross-Cutting

- [ ] T069 Verify ruff and mypy pass with Phase 4 code in `pyproject.toml`
  - Run `uv run ruff check src/ tests/` — fix any lint issues in new files
  - Run `uv run mypy src/fdsx/` — fix any type errors in new files
  - Ensure all Phase 1-3 tests still pass: `uv run pytest tests/`

- [ ] T070 Write CLI e2e test for batch task scenario in `tests/integration/test_cli_e2e_phase4.py`
  - Test `fdsx run workflow.yaml --tasks tasks.md` → mock task_splitter → approve → all tasks execute → exit code 0
  - Test `fdsx run workflow.yaml --input task=foo --tasks tasks.md` → exit code 2, mutual exclusion error
  - Test `fdsx run workflow.yaml --tasks tasks.md` with flow missing task_splitter → exit code 2
  - Use `subprocess.run` to invoke the actual CLI entry point

- [ ] T071 Write comprehensive e2e test suite for all scenarios in `tests/integration/test_e2e_scenarios.py`
  - **Scenario 1**: Simple linear flow (Plan → Implement → Review) with system provider → verify state transitions and final JSON output
  - **Scenario 2**: Parallel review + majority vote → verify parallel execution, aggregation result, choice routing
  - **Scenario 3**: Wait state + webhook notification → mock stdin for selection, mock httpx for webhook → verify routing
  - **Scenario 4**: Checkpoint/resume → run flow, interrupt, resume → verify completion and state restoration
  - **Scenario 5**: Extraction + Choice routing → verify json/regex/keyword extraction drives correct branch
  - Each scenario uses dedicated fixture YAMLs from `tests/fixtures/`
  - Use `subprocess.run` for CLI invocation where applicable, direct engine calls for finer control

- [ ] T072 Verify structured logging output in e2e tests in `tests/integration/test_e2e_scenarios.py`
  - After each scenario run, verify `runs/<thread_id>.json` exists and conforms to the Run Log Format schema
  - Verify state entries match the executed states (name, type, status)
  - Verify `final_variables` contains expected keys
  - Verify resume appends to existing log (Scenario 4 specifically)

---

## Dependencies

```
Phase 1-3 (T001-T053) completed
  ↓
T054 → T055 (run log recorder + tests)
T054 → T056 (integrate recorder into engine)
  ↓
T057 → T058 (batch splitter + tests)
T057 + T056 → T059 (batch orchestrator — needs engine + logging)
T059 → T060 (CLI --tasks option)
T060 → T061 → T062 (batch fixtures + integration test)
  ↓
T063 → T064 (exponential backoff + tests — independent)
T065 → T066 (parallel display + integration — independent)
  ↓
T067 (PyPI packaging — independent)
T068 (README — independent)
  ↓
T056 + T062 + T064 + T066 → T069 → T070 → T071 → T072
```

**Critical path**: T054 → T056 → T059 → T060 → T061 → T062 → T069 → T071 → T072

**Parallel opportunities**:
- T054 (structured logging) and T057 (batch splitter) and T063 (backoff) and T065 (parallel display) can all start in parallel
- T055 (recorder tests) can run in parallel with T056 (engine integration)
- T058 (batch tests) can run in parallel with T059 (batch orchestrator)
- T067 (PyPI) and T068 (README) are independent and can run at any time
- T064 (backoff tests) is independent of batch/logging work

## Implementation Strategy

- **MVP**: Complete through T062 (batch execution + structured logging). This enables the full Phase 4 batch workflow with observable execution.
- **Incremental delivery**: T054-T056 deliver structured logging. T057-T062 add batch execution. T063-T064 harden retries. T065-T066 improve parallel UX. T067-T068 prepare for publish. T069-T072 validate everything.
- **Key integration point**: T056 is the critical integration task — wiring RunRecorder into the engine's state execution lifecycle. Each node function needs recorder callbacks.
- **No real LLMs needed**: Batch task splitter tests mock the LLM response. All other tests use `system` provider.
- **E2E coverage**: T071 is the capstone task — verifying all 5 scenarios work end-to-end before publishing.

## Summary

| Metric | Value |
|---|---|
| Total tasks | 19 (T054-T072) |
| US6 tasks (Structured Logging) | 3 (T054-T056) |
| US7 tasks (Batch Execution) | 6 (T057-T062) |
| Edge Case Hardening tasks | 2 (T063-T064) |
| Parallel Display tasks | 2 (T065-T066) |
| PyPI Packaging tasks | 2 (T067-T068) |
| Polish tasks | 4 (T069-T072) |
| Unit test tasks | 3 (T055, T058, T064) |
| Integration test tasks | 4 (T062, T070, T071, T072) |
| Parallelizable tasks | 2 (T055, T058) |

## Suggested takt Usage

```bash
# Phase 11: US6 — Structured logging recorder + tests
takt run code "Implement RunRecorder (per-state input/output/duration, JSON run log to runs/<thread_id>.json, resume appends) in src/fdsx/logging/recorder.py and unit tests in tests/unit/test_recorder.py"

# Phase 11: US6 — Integrate logging into engine
takt run code "Integrate RunRecorder into run_flow and resume_flow in src/fdsx/core/engine.py, recording state start/complete/error and parallel branch details"

# Phase 12: US7 — Batch task splitter + tests
takt run code "Implement batch task splitter (LLM invocation, task list display, confirmation prompt, summary) in src/fdsx/core/batch.py and unit tests in tests/unit/test_batch.py"

# Phase 12: US7 — Batch orchestrator + CLI integration
takt run code "Implement run_batch orchestrator in src/fdsx/core/engine.py, add --tasks option with --input mutual exclusion in src/fdsx/cli/main.py"

# Phase 12: US7 — Batch integration tests
takt run code "Create batch_flow.yaml and sample_tasks.md fixtures in tests/fixtures/, write integration tests for batch execution in tests/integration/test_batch.py"

# Phase 13: Edge case hardening
takt run code "Add exponential backoff (2^attempt, capped 30s) to retry loops in src/fdsx/core/compiler.py, write unit tests in tests/unit/test_backoff.py"

# Phase 14: Parallel display improvements
takt run code "Implement branch start/complete/failed/results display functions in src/fdsx/display/terminal.py, integrate into parallel branch executor in src/fdsx/core/compiler.py"

# Phase 15: PyPI packaging + README
takt run code "Finalize pyproject.toml for PyPI, write README.md with quickstart, installation, feature overview, CLI reference, and example workflow"

# Phase 16: Polish + e2e tests
takt run code "Verify ruff/mypy pass, write CLI e2e tests for batch in tests/integration/test_cli_e2e_phase4.py, comprehensive e2e suite for all Scenarios 1-5 in tests/integration/test_e2e_scenarios.py with structured log verification"
```

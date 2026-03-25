# Tasks: Codebase Robustness Refactor

**Spec**: `specs/20260325090000-codebase-robustness-refactor/spec.md`
**Plan**: `specs/20260325090000-codebase-robustness-refactor/plan.md`
**Branch**: `feat/codebase-robustness-refactor` (from `feat/phase-1-core-engine-mvp`)

---

## Phase 1: Extract Shared Leaf Modules (FR-1.1, FR-1.2)

**Goal**: Extract `parse_jsonpath` and `get_next_states` into dedicated leaf modules, eliminating duplication across `models/flow.py`, `core/variables.py`.

**Independently testable**: After this phase, `grep -rn "def get_next_states\|def _parse_path_segments\|def _parse_jsonpath" src/` should return exactly 2 definitions (one in `paths.py`, one in `graph_utils.py`). All existing tests pass.

### Tasks

- [x] T001 Write unit tests for `parse_jsonpath` in `tests/unit/test_paths.py` — cover dot notation, bracket notation, mixed, empty string, quoted keys, integer indices
- [x] T002 Create `src/fdsx/core/paths.py` with `parse_jsonpath(path: str) -> list[str | int]` extracted from `src/fdsx/core/variables.py:118-156`. This is a leaf module with no imports from `flow.py` or `variables.py`
- [x] T003 Update `src/fdsx/models/flow.py` to import `parse_jsonpath` from `fdsx.core.paths` and remove the inline `_parse_path_segments` function (lines 8-42). Update all call sites in validators (`validate_extract_no_reserved_keys`, `validate_extract_path_no_overlap`)
- [x] T004 Update `src/fdsx/core/variables.py` to import `parse_jsonpath` from `fdsx.core.paths` and remove the inline `_parse_jsonpath` function (lines 118-156). Update all call sites (`resolve_jsonpath`, `set_jsonpath`, `_is_var_satisfied`)
- [x] T005 Write unit tests for `get_next_states` in `tests/unit/test_graph_utils.py` — cover all 5 state types (Task, Choice, Parallel, Pass, Wait), with/without `$END` sentinel, `next=None` + `end=True`
- [x] T006 Create `src/fdsx/core/graph_utils.py` with `get_next_states(state: State, include_end_sentinel: bool = False) -> set[str]`. Canonical implementation handling all state types. Leaf module — import State types from `fdsx.models.flow`
- [x] T007 Update `src/fdsx/models/flow.py` validators `validate_all_next_references` (line 483) and `validate_termination` (line 516) to import and use `get_next_states` from `fdsx.core.graph_utils`. Remove both inline `get_next_states` inner functions
- [x] T008 Update `src/fdsx/core/variables.py` function `analyze_variable_references` (line 266) to import and use `get_next_states` from `fdsx.core.graph_utils`. Remove the inline `get_next_states` inner function (line 277)
- [x] T009 Run full test suite (`pytest tests/`) and verify zero regressions. Verify with grep that no duplicate definitions remain

---

## Phase 2: Extract Shared Execution Loop (FR-1.3)

**Goal**: Eliminate the ~80-line retry/execute/extract duplication between `_create_task_node` and `_create_branch_executor` in `compiler.py`.

**Independently testable**: After this phase, the retry/execute/extract logic exists in exactly one function. Both task nodes and branch executors use it. All existing tests pass.

### Tasks

- [x] T010 Write unit tests for the shared execution function in `tests/unit/test_execution.py` — test retry with exponential backoff (verify sleep durations), system provider vs LLM provider dispatch, timeout handling (TimeoutExpired), extraction success, extraction failure after retries. Use mock providers
- [x] T011 Create `src/fdsx/core/execution.py` with `ExecutionConfig` dataclass and `execute_with_retry(config, state_dict, provider, stream_logger) -> ExecutionResult` function. Extract retry loop, backoff, system vs LLM dispatch, and extraction logic from `src/fdsx/core/compiler.py:632-678`
- [x] T012 Refactor `_create_task_node` in `src/fdsx/core/compiler.py` (line 586) to call `execute_with_retry`. Keep task-specific logic: iteration tracking, result path setting, result_file handling, error raising, recorder calls
- [x] T013 Refactor `_create_branch_executor` in `src/fdsx/core/compiler.py` (line 776) to call `execute_with_retry`. Keep branch-specific logic: branch_index lookup, branch result dict construction, error capture (no raise)
- [x] T014 Run full test suite (`pytest tests/`) and verify zero regressions

---

## Phase 3: Atomic Lock Files (FR-5)

**Goal**: Fix the TOCTOU race condition in `CheckpointManager.acquire_lock()` using `O_CREAT|O_EXCL`.

**Independently testable**: After this phase, two concurrent processes attempting to lock the same thread ID will never both succeed. Stale locks are auto-recovered.

### Tasks

- [x] T015 Write integration tests for lock atomicity in `tests/integration/test_lock_atomicity.py` — test concurrent lock acquisition (use `multiprocessing.Process` to race two acquires on same thread ID, assert exactly one succeeds), stale lock auto-recovery (write dead PID, verify acquire succeeds with warning), release idempotency (release when no lock held, no error)
- [x] T016 Modify `CheckpointManager.acquire_lock()` in `src/fdsx/checkpoint/manager.py` (lines 88-117) to use `os.open(path, O_CREAT | O_EXCL | O_WRONLY, 0o600)` for atomic creation. On `FileExistsError`: read PID, check alive with `os.kill(pid, 0)`, if dead remove stale lock with `logger.warning()` and single retry. Add `import logging` and `logger = logging.getLogger(__name__)` if not present
- [x] T017 Run full test suite (`pytest tests/`) and verify zero regressions + new lock tests pass

---

## Phase 4: Decompose engine.py and compiler.py (FR-2, FR-3, FR-1.4)

**Goal**: Split the two oversized modules (engine.py: 1022 lines, compiler.py: 1262 lines) into focused packages with re-export facades.

**Independently testable**: After this phase, `from fdsx.core.engine import run_flow` and `from fdsx.core.compiler import compile_flow` still work. No module exceeds ~400 lines. All existing tests pass without import changes.

### Tasks

#### 4a: Decompose engine.py

- [ ] T018 Create `src/fdsx/core/engine/` package directory. Create `src/fdsx/core/engine/validate.py` with `FlowValidationError` class and `validate_flow()` function extracted from `src/fdsx/core/engine.py` (lines 36-39, 619-629)
- [ ] T019 Create `src/fdsx/core/engine/results.py` with `_extract_results`, `_sanitize_state_for_log`, `_calc_elapsed`, `_find_failed_state` extracted from `src/fdsx/core/engine.py` (lines 224-289)
- [ ] T020 Create `src/fdsx/core/engine/interrupts.py` with shared interrupt-handling loop extracted from both `run_flow` (lines 158-185) and `resume_flow` (lines 559-580). Implement as `handle_interrupts(graph, config, stream_mode="values") -> dict[str, Any]` that encapsulates the while-loop pattern: get_state → find interrupt → display_wait_prompt → stream Command(resume=...)
- [ ] T021 Create `src/fdsx/core/engine/run.py` with `run_flow()` extracted from `src/fdsx/core/engine.py` (lines 42-221). Import `handle_interrupts` from `interrupts.py`, result helpers from `results.py`, `FlowValidationError` from `validate.py`
- [ ] T022 Create `src/fdsx/core/engine/resume.py` with `resume_flow()` extracted from `src/fdsx/core/engine.py` (lines 400-616). Import `handle_interrupts` from `interrupts.py`, result helpers from `results.py`
- [ ] T023 Create `src/fdsx/core/engine/batch.py` with `run_batch()` extracted from `src/fdsx/core/engine.py` (lines 292-397). Import `run_flow` from `run.py`, `FlowValidationError` from `validate.py`
- [ ] T024 Create `src/fdsx/core/engine/tasks_dir.py` with `run_tasks_dir()`, `load_tasks_dir()`, `_filter_actionable_entries()`, `_update_task_status()`, `_workflow_persist_id()` extracted from `src/fdsx/core/engine.py` (lines 632-1022)
- [ ] T025 Write `src/fdsx/core/engine/__init__.py` as a silent re-export facade — re-export all public names: `run_flow`, `resume_flow`, `run_batch`, `run_tasks_dir`, `load_tasks_dir`, `validate_flow`, `FlowValidationError`. Delete the original `src/fdsx/core/engine.py` file
- [ ] T026 Run full test suite (`pytest tests/`) and verify all imports still work, zero regressions

#### 4b: Decompose compiler.py

- [ ] T027 Create `src/fdsx/core/compiler/` package directory. Create `src/fdsx/core/compiler/helpers.py` with `_top_level_key`, `_parallel_branch_reducer`, `_merge_provider_options`, `_extract_result_paths`, `_set_next_state_meta`, `_check_max_iterations`, `_get_next_state` extracted from `src/fdsx/core/compiler.py`
- [ ] T028 Create `src/fdsx/core/compiler/routing.py` with `_create_routing_function` and `_evaluate_condition` extracted from `src/fdsx/core/compiler.py` (lines 1225-1262). Also move `_resolve_jsonpath` (line 1242) here
- [ ] T029 Create `src/fdsx/core/compiler/aggregation.py` with `_aggregate` function extracted from `src/fdsx/core/compiler.py` (lines 1063-1101)
- [ ] T030 Move `src/fdsx/core/execution.py` to `src/fdsx/core/compiler/execution.py`. Update imports in compiler modules
- [ ] T031 Create `src/fdsx/core/compiler/nodes.py` with `_create_task_node`, `_create_choice_node`, `_create_pass_node`, `_create_wait_notify_node`, `_create_wait_interrupt_node` extracted from `src/fdsx/core/compiler.py`. Import `execute_with_retry` from `execution.py`, helpers from `helpers.py`
- [ ] T032 Create `src/fdsx/core/compiler/parallel.py` with `_create_dispatch_node`, `_create_branch_executor`, `_create_fan_out`, `_create_collector_node` extracted from `src/fdsx/core/compiler.py`. Import `execute_with_retry` from `execution.py`, `_aggregate` from `aggregation.py`
- [ ] T033 Create `src/fdsx/core/compiler/compile.py` with `compile_flow()`, `CompiledGraph`, `FlowState`, `_build_state_schema`, `_wrap_with_hooks`, `_collect_state_hooks` logic extracted from `src/fdsx/core/compiler.py` (lines 44-431). Import node creators from `nodes.py`, `parallel.py`, routing from `routing.py`
- [ ] T034 Write `src/fdsx/core/compiler/__init__.py` as a silent re-export facade — re-export `compile_flow`, `CompiledGraph`. Delete the original `src/fdsx/core/compiler.py` file
- [ ] T035 Update any test files that import internal compiler/engine functions to use new paths (only if re-exports don't cover them). Run full test suite (`pytest tests/`) and verify zero regressions

---

## Phase 5: Signal Handling (FR-4)

**Goal**: Register SIGINT/SIGTERM handlers during flow execution for graceful subprocess cleanup.

**Independently testable**: After this phase, sending SIGINT to a running fdsx process leaves no orphaned child processes and no stale lock files. Checkpoint state is preserved.

### Tasks

- [ ] T036 Write integration tests for signal handling in `tests/integration/test_signal_handling.py` — test SIGINT cleanup (spawn fdsx with system provider running `sleep 60`, send SIGINT after 2s, verify no orphan processes and lock cleaned up), test checkpoint preservation after SIGINT, test "workflow interrupted" message output. Use `subprocess.Popen` + `os.kill(pid, signal.SIGINT)`. Add `pytest-timeout` marker as safety net
- [ ] T037 Create `src/fdsx/core/engine/signals.py` with `SignalHandler` context manager. `__enter__`: save previous SIGINT/SIGTERM handlers via `signal.signal()`, register custom handler. `__exit__`: restore previous handlers. Handler logic: propagate signal to active subprocess → wait 5s → SIGKILL escalation → release lock → print message → `sys.exit(128 + signum)`
- [ ] T038 Integrate signal handler into `src/fdsx/core/engine/run.py` — wrap the `graph.stream()` execution in `SignalHandler` context. Pass `checkpoint_manager` and `thread_id` to the handler
- [ ] T039 Integrate signal handler into `src/fdsx/core/engine/resume.py` — wrap the `graph.stream()` execution in `SignalHandler` context. Pass `checkpoint_manager` and `thread_id` to the handler
- [ ] T040 Wire subprocess registration in `src/fdsx/providers/base.py` — add optional `on_process_start: Callable[[subprocess.Popen], None] | None = None` parameter to `_run_subprocess`. Call it after `Popen()` creation. Thread the callback from `SignalHandler.set_active_process` through the provider execute chain
- [ ] T041 Run full test suite (`pytest tests/`) and verify zero regressions + signal handling tests pass

---

## Phase 6: Verification & Cleanup

**Goal**: Final validation that all spec success criteria are met.

### Tasks

- [ ] T042 Run full test suite (`pytest tests/ -v`) — all unit and integration tests pass
- [ ] T043 Verify no source file exceeds ~400 lines: `find src/fdsx -name "*.py" -exec wc -l {} + | sort -rn | head -10`
- [ ] T044 Verify no duplicated functions: `grep -rn "def get_next_states\|def _parse_path_segments\|def _parse_jsonpath" src/` returns exactly 2 definitions (one each in `paths.py` and `graph_utils.py`)
- [ ] T045 Verify backward compatibility: `python -c "from fdsx.core.engine import run_flow, resume_flow, run_batch, validate_flow; from fdsx.core.compiler import compile_flow, CompiledGraph; print('OK')"` succeeds
- [ ] T046 Run linting and type checks: `ruff check src/ tests/` and `mypy src/fdsx/` pass clean
- [ ] T047 Manual smoke test: run a sample workflow with Ctrl+C during provider execution. Verify no orphan processes, lock cleaned up, checkpoint preserved

---

## Dependencies

```
T001-T009 (Phase 1: leaf modules)
    ↓
T010-T014 (Phase 2: shared execution)
    ↓
T015-T017 (Phase 3: atomic locks)
    ↓
T018-T035 (Phase 4: decomposition)
    ↓
T036-T041 (Phase 5: signal handling)
    ↓
T042-T047 (Phase 6: verification)
```

All phases are strictly sequential. Within each phase, tasks are ordered for TDD (test → implementation → verification).

## Parallel Opportunities Within Phases

- **Phase 1**: T001+T002 (paths) can run in parallel with T005+T006 (graph_utils) since they touch different files. T003-T004 and T007-T008 must follow their respective implementations.
- **Phase 4a vs 4b**: Engine decomposition (T018-T026) and compiler decomposition (T027-T035) could theoretically run in parallel, but the shared `execution.py` move (T030) creates a dependency. Recommend sequential.
- **Phase 5**: T038 and T039 (integration into run.py / resume.py) are [P] parallelizable.

## Implementation Strategy

**MVP**: Phase 1 + Phase 2 + Phase 3 (consolidate duplicated logic + fix lock race). These deliver the highest-impact improvements with the lowest risk.

**Full scope**: All 6 phases. Phase 4-5 deliver navigability and signal handling but carry higher risk (module restructuring, OS-level behavior).

**Estimated task count**: 47 tasks across 6 phases.

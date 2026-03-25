# Tasks: Test Trophy Refactor

## Feature Overview

Restructure the fdsx test suite to follow the test trophy pattern: remove trivial unit tests, create `tests/e2e/` and migrate CLI tests, minimize timing delays, and document guidelines.

**Tech Stack**: Python 3.10+, pytest, fdsx framework
**PR Strategy**: Single atomic PR

---

## Phase 1: Setup

- [x] T001 Verify baseline test suite passes by running `python -m pytest tests/ -v` and recording current test count and execution time

---

## Phase 2: Remove Trivial Unit Tests (FR-1)

- [x] T002 [FR1] Remove ~41 trivial field-assignment tests from `tests/unit/test_models.py` per the removal list in `specs/20260325112856-test-trophy-refactor/research.md`, preserving all validation error, edge case, and dispatch tests
- [x] T003 [FR1] Remove `test_thread_id_is_string` from `tests/unit/test_thread_id.py`
- [x] T004 [FR1] Remove trivial default/assignment tests from `tests/unit/test_provider_options.py` (`test_claude_options_defaults`, `test_claude_options_inactivity_timeout_default/custom/zero_disables`, and equivalent codex/opencode tests)
- [x] T005 [FR1] Remove `test_default_status_pending` and `test_thread_id_and_error_optional` from `tests/unit/test_task_model.py`
- [x] T006 [FR1] Delete any test files left entirely empty after trivial test removal
- [x] T007 [FR1] Verify test suite passes after removals by running `python -m pytest tests/unit/ -v`

---

## Phase 3: Create `tests/e2e/` and Migrate CLI Tests (FR-2)

### Directory Setup

- [ ] T008 [FR2] Create `tests/e2e/` directory with `__init__.py`
- [ ] T009 [FR2] Copy `tests/integration/cli_test_utils.py` to `tests/e2e/cli_test_utils.py`
- [ ] T010 [FR2] Create `tests/e2e/conftest.py` with any shared fixtures needed by CLI tests (inspect existing `tests/integration/conftest.py` for relevant fixtures)

### Move and Rename CLI Test Files

- [ ] T011 [P] [FR2] Move `tests/integration/test_cli_e2e.py` to `tests/e2e/test_cli_validation_and_run.py`, updating imports from `tests.integration.cli_test_utils` to `tests.e2e.cli_test_utils`
- [ ] T012 [P] [FR2] Move `tests/integration/test_cli_e2e_phase2.py` to `tests/e2e/test_cli_flow_types.py`, updating imports
- [ ] T013 [P] [FR2] Move `tests/integration/test_cli_e2e_phase3.py` to `tests/e2e/test_cli_wait_and_resume.py`, updating imports
- [ ] T014 [P] [FR2] Move `tests/integration/test_cli_e2e_phase4.py` to `tests/e2e/test_cli_batch_tasks.py`, updating imports
- [ ] T015 [P] [FR2] Move `tests/integration/test_thread_id_format.py` to `tests/e2e/test_cli_thread_id.py`, updating imports
- [ ] T016 [P] [FR2] Move `tests/integration/test_workflow_name_display.py` to `tests/e2e/test_cli_workflow_name.py`, updating imports
- [ ] T017 [P] [FR2] Move `tests/integration/test_batch.py` to `tests/e2e/test_cli_batch_split.py`, updating imports
- [ ] T018 [P] [FR2] Move `tests/integration/test_signal_handling.py` to `tests/e2e/test_cli_signal_handling.py`, updating imports

### Split `test_e2e_phase6.py`

- [ ] T019 [FR2] Split `TestBackwardCompat` from `tests/integration/test_e2e_phase6.py` into `tests/e2e/test_batch_backward_compat.py` with only required imports
- [ ] T020 [P] [FR2] Split `TestErrorMessages` from `tests/integration/test_e2e_phase6.py` into `tests/e2e/test_batch_error_messages.py` with only required imports
- [ ] T021 [P] [FR2] Split `TestEdgeCases` from `tests/integration/test_e2e_phase6.py` into `tests/e2e/test_batch_edge_cases.py` with only required imports
- [ ] T022 [P] [FR2] Split `TestFullPipelineE2E`, `TestHelpText`, and `TestSecuritySanitization` from `tests/integration/test_e2e_phase6.py` into `tests/e2e/test_batch_full_pipeline.py` with only required imports

### Rename Integration File

- [ ] T023 [FR2] Rename `tests/integration/test_e2e_scenarios.py` to `tests/integration/test_scenario_flows.py`, updating any internal references

### Cleanup

- [ ] T024 [FR2] Delete original files from `tests/integration/` that were moved to `tests/e2e/` (test_cli_e2e.py, test_cli_e2e_phase2.py, test_cli_e2e_phase3.py, test_cli_e2e_phase4.py, test_thread_id_format.py, test_workflow_name_display.py, test_batch.py, test_signal_handling.py, test_e2e_phase6.py)
- [ ] T025 [FR2] Delete `tests/integration/cli_test_utils.py` if no remaining integration tests import it
- [ ] T026 [FR2] Verify full test suite passes after migration by running `python -m pytest tests/ -v`

---

## Phase 4: Minimize Timing Delays (FR-5)

- [ ] T027 [P] [FR5] Reduce `time.sleep(999)` to `time.sleep(5)` in `tests/integration/test_inactivity_timeout.py`
- [ ] T028 [P] [FR5] Reduce `time.sleep(999)` to `time.sleep(5)` and `sleep 60` to `sleep 5` in `tests/unit/test_subprocess_completion.py`
- [ ] T029 [P] [FR5] Reduce `time.sleep(999)` to `time.sleep(5)` in `tests/integration/test_codex_completion.py`
- [ ] T030 [P] [FR5] Reduce `sleep 9973` to `sleep 30` and minimize `_STARTUP_WAIT` in `tests/e2e/test_cli_signal_handling.py`
- [ ] T031 [FR5] Verify timing-dependent tests still pass by running `python -m pytest tests/integration/test_inactivity_timeout.py tests/unit/test_subprocess_completion.py tests/integration/test_codex_completion.py tests/e2e/test_cli_signal_handling.py -v`

---

## Phase 5: Document Test Trophy Guidelines (FR-3, FR-4)

- [ ] T032 [FR4] Create `AGENTS.md` at repo root with test trophy guidelines covering directory structure, what belongs at each level, anti-patterns to avoid, and naming conventions (content specified in plan.md Task 4.1)
- [ ] T033 [FR4] Create `CLAUDE.md` as a symbolic link to `AGENTS.md` via `ln -s AGENTS.md CLAUDE.md`
- [ ] T034 [FR3] Add integration test feature-centeredness assessment note to `AGENTS.md` confirming all integration tests are already feature-centered per research.md findings

---

## Phase 6: Final Verification

- [ ] T035 Run full test suite with `python -m pytest tests/ -v` and verify: all tests pass, no phase-based file names remain, no `run_fdsx()` tests in `tests/integration/`, no trivial tests remain
- [ ] T036 Compare post-refactor test execution time against baseline (T001) to validate 10% improvement target

---

## Dependencies

```
T001 (baseline) → T002-T006 (removals) → T007 (verify)
T007 → T008-T010 (e2e setup) → T011-T025 (moves/splits/cleanup) → T026 (verify)
T026 → T027-T030 (timing) → T031 (verify)
T031 → T032-T034 (docs)
T034 → T035-T036 (final verify)
```

**Parallel opportunities**:
- T002-T005 can run in parallel (different test files)
- T011-T018 can run in parallel (independent file moves)
- T019-T022 can run in parallel (independent splits from same source)
- T027-T030 can run in parallel (independent timing fixes)

---

## Implementation Strategy

1. **MVP**: Phase 1 (trivial test removal) + Phase 2 (e2e migration) deliver the most visible improvement
2. **Incremental delivery**: Each phase produces a verifiable, independently testable state
3. **Single PR**: All changes committed together as one atomic PR per plan requirements

---

## Summary

| Phase | Tasks | Parallel |
|---|---|---|
| Setup | 1 (T001) | — |
| FR-1: Remove Trivial Tests | 6 (T002-T007) | T002-T005 |
| FR-2: E2E Migration | 19 (T008-T026) | T011-T018, T019-T022 |
| FR-5: Timing Fixes | 5 (T027-T031) | T027-T030 |
| FR-3/FR-4: Documentation | 3 (T032-T034) | — |
| Final Verification | 2 (T035-T036) | — |
| **Total** | **36 tasks** | |

---

## Suggested Execution Commands

```bash
# Phase 1: Setup
takt run coder "Run baseline test suite: python -m pytest tests/ -v, record test count and execution time"

# Phase 2: Remove trivial tests
takt run coder "Remove trivial unit tests from tests/unit/test_models.py, test_thread_id.py, test_provider_options.py, test_task_model.py per specs/20260325112856-test-trophy-refactor/research.md removal lists. Delete empty files. Verify with pytest."

# Phase 3: Create e2e and migrate
takt run coder "Create tests/e2e/ directory, move CLI tests from tests/integration/ per specs/20260325112856-test-trophy-refactor/plan.md Phase 2 mapping, split test_e2e_phase6.py, rename test_e2e_scenarios.py, update imports, delete originals, verify with pytest."

# Phase 4: Timing fixes
takt run coder "Minimize subprocess sleep durations per specs/20260325112856-test-trophy-refactor/plan.md Phase 3 table, verify timing tests still pass."

# Phase 5: Documentation
takt run coder "Create AGENTS.md with test trophy guidelines per specs/20260325112856-test-trophy-refactor/plan.md Phase 4, create CLAUDE.md symlink."

# Phase 6: Final verification
takt run coder "Run full test suite, verify no phase-based names remain, compare execution time to baseline."
```

# Tasks: fdsx UX & Observability Improvements

**Spec**: [spec.md](spec.md)
**Plan**: [plan/impl-plan.md](plan/impl-plan.md)
**Created**: 2026-03-23

## Overview

5 phases, TDD approach (tests before implementation), coarse granularity (1 task per phase with tests).

**Total tasks**: 10 (5 test tasks + 5 implementation tasks)
**Estimated parallelism**: Phases 1-2 can run in parallel. Phase 3-4 depend on Phase 1. Phase 5 depends on all.

---

## Phase 1: Thread ID Format + Run Directory

**Goal**: Replace UUID-based thread IDs with human-readable `YYYY-MM-DD-HHmmss-<6char>` format.

- [x] T001 Write tests for thread ID generation and run directory naming in `tests/unit/test_thread_id.py` and `tests/integration/test_thread_id_format.py`. Tests should verify: (a) `generate_thread_id()` returns a string matching pattern `YYYY-MM-DD-HHmmss-[a-f0-9]{6}`, (b) 100 consecutive calls produce unique IDs, (c) the timestamp portion matches the current local time, (d) integration test runs `fdsx run` with a simple system-provider workflow and asserts the run directory under `.fdsx/runs/` matches the new format (not UUID), and (e) `fdsx resume --thread-id <new-format-id>` resolves to the correct directory. All tests should initially fail (red phase).

- [x] T002 Implement thread ID format change. Create `src/fdsx/core/thread_id.py` with `generate_thread_id()` using `datetime.now().strftime("%Y-%m-%d-%H%M%S") + "-" + secrets.token_hex(3)`. Replace all `uuid_utils.uuid7()` calls with `generate_thread_id()` in `src/fdsx/cli/main.py` (line 184), `src/fdsx/core/engine.py` (lines 70 and 343). Verify `src/fdsx/logging/recorder.py` THREAD_ID_PATTERN already accepts the new format. Run T001 tests to confirm they pass (green phase).

## Phase 2: Workflow Name Display

**Goal**: Use `flow.name` from YAML everywhere instead of filesystem-derived display names.

- [x] T003 [P] Write tests for workflow name display in `tests/unit/test_selector_name.py` and `tests/integration/test_workflow_name_display.py`. Tests should verify: (a) `discover_workflows()` returns `flow.name` as `display_name` instead of directory name or file stem, (b) when two workflows have the same `name`, `pick_workflow_manually()` shows the filepath alongside the name for disambiguation (e.g., "Plan-Review (workflows/ci/workflow.yaml)"), (c) `fdsx validate workflow.yaml` output includes the flow name (not the file path), (d) `_build_workflow_selection_prompt()` uses flow names in the prompt. Create test fixture workflows in `tests/fixtures/` with duplicate names in different directories. All tests should initially fail.

- [x] T004 [P] Implement workflow name display changes. Modify `src/fdsx/core/selector.py`: in `discover_workflows()`, change `display_name = entry.name` (line 75) to `display_name = flow.name` and `display_name = fp.stem` (line 120) to `display_name = flow.name`. In `pick_workflow_manually()`, detect duplicate display_names in the workflows list and append ` ({filepath})` when duplicates exist. Modify `src/fdsx/cli/main.py` validate command (line 225) to load the flow and show `f"Flow '{flow.name}' is valid."` instead of using the path. Run T003 tests to confirm they pass.

## Phase 3: Iteration-Numbered Log Files

**Goal**: Change log file naming from `{state}.log` (append) to `{state}_{iteration}.log` (per-iteration).

- [x] T005 Write tests for iteration-numbered log files in `tests/unit/test_stream_logger_iteration.py` and `tests/integration/test_iteration_logs.py`. Tests should verify: (a) `StreamLogger` with `iteration=1` creates `state_1.log` (not `state.log`), (b) `StreamLogger` with `iteration=2` creates `state_2.log` as a separate file, (c) parallel branch logs use pattern `{state}_branch{N}_{iteration}.log`, (d) retry output within the same iteration appends to the same file, (e) integration test runs a workflow with a Choice loop (system provider that alternates REJECTED/APPROVED) and asserts `plan_1.log`, `plan_2.log`, `implement_1.log`, `implement_2.log` exist in the run directory under `logs/`. Test that `_state_iterations` dict in FlowState is correctly incremented. All tests should initially fail.

- [x] T006 Implement iteration-numbered log files. Modify `src/fdsx/logging/stream_logger.py`: add `iteration: int` parameter to `__init__()`, change `_write_to_file()` to use `f"{self.state_name}_{self.iteration}{LOG_FILE_SUFFIX}"`. Modify `src/fdsx/core/compiler.py`: where `StreamLogger` is instantiated, read and increment `_state_iterations[state_name]` from the FlowState dict before creating the logger, pass the iteration number. For parallel branches, use `{state}_branch{N}_{iteration}` as the state_name parameter. Modify `src/fdsx/core/engine.py`: add `_state_iterations: {}` to `initial_state` dict (line ~121). Ensure `_state_iterations` is excluded from result extraction (already handled by `_sanitize_state_for_log` which strips `_`-prefixed keys). Run T005 tests to confirm they pass.

## Phase 4: Per-State max_iterations

**Goal**: Add optional `max_iterations` field to states that fails the flow when limit is exceeded.

- [x] T007 Write tests for per-state max_iterations in `tests/unit/test_max_iterations.py` and `tests/integration/test_max_iterations_flow.py`. Tests should verify: (a) Pydantic model accepts `max_iterations: 3` on TaskState, ChoiceState, ParallelState, PassState, WaitState, (b) `max_iterations: 0` raises validation error, (c) `max_iterations: -1` raises validation error, (d) `max_iterations: None` (default) is accepted, (e) compiler node function raises error with message "State 'plan' reached max_iterations limit (3)" when entry count exceeds limit, (f) integration test: workflow with `plan` state having `max_iterations: 2` in a Choice loop, verify flow fails after 2 entries to `plan` with the correct error message, (g) `max_iterations` and `max_loop` coexist — whichever triggers first takes effect. All tests should initially fail.

- [x] T008 Implement per-state max_iterations. Modify `src/fdsx/models/flow.py`: add `max_iterations: int | None = Field(default=None, ge=1, description="Max times this state can be entered")` to TaskState, ChoiceState, ParallelState, PassState, WaitState. Modify `src/fdsx/core/compiler.py`: in each state's node function (before execution logic), read `_state_iterations[state_name]` from state, compare against the state definition's `max_iterations` (if set), raise `RuntimeError(f"State '{state_name}' reached max_iterations limit ({max_iterations})")` if exceeded. Modify `src/fdsx/core/engine.py`: ensure the RuntimeError from max_iterations is caught and reported with the same flow as other execution errors (checkpoint saved, resume command displayed). Run T007 tests to confirm they pass.

## Phase 5: Polish + Existing Test Updates

**Goal**: Fix all broken existing tests and add edge case coverage.

- [x] T009 Run full test suite (`pytest tests/`) and identify all failures caused by the changes in Phases 1-4. Document each failing test and the reason (UUID format assertion, log filename assertion, display_name assertion, etc.).

- [x] T010 Fix all identified test failures: (a) update tests asserting UUID thread_id format to accept the new `YYYY-MM-DD-HHmmss-abc123` format, (b) update tests asserting `display_name` equals directory name or file stem to assert `flow.name` instead, (c) update tests asserting log filenames like `state.log` to assert `state_1.log`, (d) verify resume integration tests work with new thread_id format, (e) verify batch mode tests work correctly (each task still gets its own directory with new format), (f) add edge case tests: state names containing underscores (e.g., `my_state_1.log` vs `my_state_1_1.log` — ensure no ambiguity in parsing), max_iterations interaction with resume (iteration count restored from checkpoint). Run full test suite to confirm all tests pass.

---

## Dependencies

```
Phase 1 (Thread ID) ──────────┐
                               ├──> Phase 3 (Iteration Logs) ──> Phase 5 (Polish)
Phase 2 (Workflow Name) ──┐    │
         [independent]    ├────┘
                          └──> Phase 4 (max_iterations) ──────> Phase 5 (Polish)
```

- **Phase 1 & 2**: Independent, can run in parallel
- **Phase 3**: Depends on Phase 1 (needs `_state_iterations` in FlowState and new run directory)
- **Phase 4**: Depends on Phase 3 (needs `_state_iterations` tracking already in place)
- **Phase 5**: Depends on all phases (test fixup)

## Parallel Execution

| Parallel Group | Tasks | Condition |
|---|---|---|
| Group A | T001+T002, T003+T004 | Phases 1 & 2 in parallel |
| Group B | T005+T006 | After Phase 1 complete |
| Group C | T007+T008 | After Phase 3 complete |
| Group D | T009+T010 | After all phases complete |

## Implementation Strategy

1. **MVP**: Phase 1 (Thread ID) — most visible change, affects every `fdsx run`
2. **Quick win**: Phase 2 (Workflow Name) — isolated change in selector.py
3. **Core feature**: Phase 3+4 (Iteration Logs + max_iterations) — main functionality
4. **Cleanup**: Phase 5 — ensure nothing is broken

# Tasks: UX & Reliability Improvements

**Feature**: UX & Reliability Improvements
**Spec**: `specs/20260320120000-ux-reliability-improvements/spec.md`
**Plan**: `specs/20260320120000-ux-reliability-improvements/plan.md`
**Branch**: `feat/phase-1-config-system-task-model`
**Generated**: 2026-03-20

---

## Phase 1: Bug Fixes (Template Rendering + PassState Validation)

**Goal**: Fix the two bugs first — smallest scope, independent of other changes.
**Test criteria**: `pytest tests/unit/test_variables.py -x` passes with new test cases for list/dict template rendering and PassState variable recognition.

- [x] T001 TDD: Write tests for `resolve_template` with list/dict values expecting JSON output in `tests/unit/test_variables.py`
- [x] T002 Fix template rendering in `src/fdsx/core/variables.py` — use `json.dumps(value, indent=2, ensure_ascii=False)` when value is `list` or `dict` in `resolve_template` and `resolve_template_shell_safe`
- [x] T003 TDD: Write tests for `get_result_paths` with PassState having `parameters` and `aggregate.result_path` in `tests/unit/test_variables.py`
- [x] T004 Add `elif isinstance(state, PassState)` branch in `get_result_paths()` in `src/fdsx/core/variables.py` to register each key in `state.parameters` and `state.aggregate.result_path`
- [x] T005 Write integration test for `analyze_variable_references` with a flow using PassState parameters referenced by subsequent states in `tests/unit/test_variables.py`

---

## Phase 2: Spinner & Non-TTY Fallback

**Goal**: Add animated spinner with non-TTY fallback to display module. Uses stdlib `threading.Thread` daemon + ANSI braille chars on stderr — zero new dependencies.
**Test criteria**: `pytest tests/unit/test_spinner.py -x` passes with spinner start/stop/update, context manager, and non-TTY fallback tests.

- [x] T006 TDD: Write spinner unit tests covering start/stop/update, context manager, non-TTY fallback in `tests/unit/test_spinner.py`
- [x] T007 Implement `Spinner` class in `src/fdsx/display/terminal.py` with daemon thread, braille animation (`SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"`), `\r` overwrite on stderr
- [x] T008 Implement `is_interactive()` helper function in `src/fdsx/display/terminal.py` wrapping `sys.stderr.isatty()`
- [x] T009 Implement non-TTY mode in `Spinner` — print plain log lines instead of animation when `is_interactive()` returns False

---

## Phase 3: Spinner Integration (Split + Workflow Selection)

**Goal**: Wire spinner into `fdsx split` and `fdsx run` workflow auto-selection.
**Test criteria**: `pytest tests/integration/test_split_spinner.py -x` passes; spinner starts/stops correctly during split and auto-selection.

- [x] T010 [US1] Wrap LLM call in `split_tasks_to_groups` (`src/fdsx/core/batch.py`) and `split` CLI command (`src/fdsx/cli/main.py`) with Spinner
- [x] T011 [US1] Wrap workflow auto-selection loop in `run_tasks_dir` (`src/fdsx/core/engine.py`) with Spinner showing progress (e.g., "Assigned workflow for 3/5 tasks...")
- [x] T012 [US1] Write integration tests for spinner during split and auto-selection operations in `tests/integration/test_split_spinner.py`

---

## Phase 4: Interactive Workflow CUI & Persistence

**Goal**: Replace the existing basic confirm prompt with a numbered-list interactive CUI and persist workflow assignments.
**Test criteria**: `pytest tests/unit/test_workflow_cui.py tests/integration/test_workflow_persistence.py -x` passes; CUI displays table, accepts number input to change workflow, confirms/cancels, auto-confirms in non-TTY.

- [x] T013 [US2] TDD: Write CUI unit tests covering table display, number input to change workflow, confirm ('c'), cancel ('q'), non-TTY auto-confirm in `tests/unit/test_workflow_cui.py`
- [x] T014 [US2] Implement `confirm_workflow_assignments_interactive()` in `src/fdsx/display/terminal.py` — numbered table, type number to change workflow, 'c' to confirm, 'q' to cancel
- [x] T015 [US2] Implement unassigned task handling — tasks where auto-selection failed show "(unassigned)"; block confirm until all assigned in `src/fdsx/display/terminal.py`
- [x] T016 [US2] Replace `_display_batch_workflow_confirm` + raw `input()` calls in `run_tasks_dir` (`src/fdsx/core/engine.py`) with new CUI function
- [x] T017 [US2] Implement workflow persistence — on CUI confirmation, write `workflow` field to each task YAML via `save_task_file()` in `src/fdsx/core/engine.py`
- [x] T018 [US2] Verify tasks with `entry.workflow` already set skip auto-selection in `run_tasks_dir` (`src/fdsx/core/engine.py`) — add test if missing
- [x] T019 [US2] Implement non-TTY auto-confirm — in non-TTY mode, CUI auto-confirms and persists without interaction in `src/fdsx/display/terminal.py`
- [x] T020 [US2] Write end-to-end integration tests: auto-select → CUI confirm → verify YAML has workflow field → re-run skips selection in `tests/integration/test_workflow_persistence.py`

---

## Phase 5: Resume Command on Error & Interrupt

**Goal**: Display the resume command on all errors and SIGINT interrupts.
**Test criteria**: `pytest tests/unit/test_resume_display.py tests/integration/test_resume_interrupt.py -x` passes; resume command displayed on errors and Ctrl+C.

- [x] T021 [US3] TDD: Write tests for `display_resume_command` output format for single-flow, tasks-dir, and with extra args in `tests/unit/test_resume_display.py`
- [x] T022 [US3] Implement `display_resume_command()` in `src/fdsx/display/terminal.py` with box-formatted output showing the full resume command
- [x] T023 [US3] In `src/fdsx/cli/main.py` `run()`, catch exceptions from engine calls and display resume command before re-raising
- [x] T024 [US3] In `src/fdsx/cli/main.py` `run()`, catch `KeyboardInterrupt` from engine calls and display resume command
- [x] T025 [US3] For tasks-dir mode, display `fdsx run --tasks-dir <path>` instead of `fdsx resume --thread-id` (tasks-dir has built-in resume) in `src/fdsx/cli/main.py`
- [x] T026 [US3] Write integration tests for error and SIGINT scenarios displaying correct resume commands in `tests/integration/test_resume_interrupt.py`

---

## Phase 6: Polish & Edge Cases

**Goal**: Ensure all features work together, edge cases handled.
**Test criteria**: Full test suite passes (`pytest tests/ -x`); help text updated; edge cases covered.

- [ ] T027 Update `--help` output for `run` and `split` commands to mention spinner and CUI behavior in `src/fdsx/cli/main.py`
- [ ] T028 Handle edge cases: empty tasks dir, all tasks completed, spinner during zero-task split, CUI with single task — in relevant source files
- [ ] T029 Write end-to-end test: split (with spinner) → auto-select (with spinner) → CUI confirm → persist → re-run (skip selection) → error → resume command displayed in `tests/integration/`

---

## Dependency Graph

```
Phase 1 (Bug Fixes) ──────────────────────────────────────┐
  │                                                        │
Phase 2 (Spinner & Non-TTY) ← independent of Phase 1,     │
  │                            but ordered for incremental │
  │                            delivery                    │
  │                                                        │
Phase 3 (Spinner Integration) ← depends on Phase 2        │
  │                              (Spinner class)           │
  │                                                        │
Phase 4 (CUI & Persistence) ← depends on Phase 2          │
  │                            (is_interactive helper)     │
  │                                                        │
Phase 5 (Resume Command) ← independent, but ordered       │
  │                         after CUI for clean integration│
  │                                                        │
Phase 6 (Polish) ← depends on all above ──────────────────┘
```

**Story completion order**: Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6

---

## Implementation Strategy

1. **MVP**: Phase 1 (Bug Fixes) — immediately improves reliability with zero UX changes
2. **Core UX**: Phase 2 + Phase 3 — spinner feedback eliminates "frozen" perception
3. **Power UX**: Phase 4 — interactive CUI + persistence for workflow management
4. **Resilience**: Phase 5 — resume command on errors/interrupts
5. **Complete**: Phase 6 — polish, edge cases, end-to-end validation

---

## Suggested takt Usage

```bash
# Phase 1: Bug Fixes
takt run implement "Phase 1: Fix template rendering for list/dict values and PassState variable recognition in src/fdsx/core/variables.py with TDD tests in tests/unit/test_variables.py"

# Phase 2: Spinner & Non-TTY
takt run implement "Phase 2: Implement Spinner class with daemon thread braille animation and is_interactive() helper in src/fdsx/display/terminal.py with TDD tests in tests/unit/test_spinner.py"

# Phase 3: Spinner Integration
takt run implement "Phase 3: Wire Spinner into fdsx split (src/fdsx/core/batch.py, src/fdsx/cli/main.py) and fdsx run workflow auto-selection (src/fdsx/core/engine.py) with integration tests"

# Phase 4: CUI & Persistence
takt run implement "Phase 4: Implement numbered-list CUI for workflow approval in src/fdsx/display/terminal.py, wire into engine.py, add workflow persistence via save_task_file(), with TDD tests"

# Phase 5: Resume Command
takt run implement "Phase 5: Implement display_resume_command() in src/fdsx/display/terminal.py, add error/SIGINT handling in src/fdsx/cli/main.py with TDD tests"

# Phase 6: Polish
takt run implement "Phase 6: Update help text in cli/main.py, handle edge cases (empty tasks dir, single task CUI, zero-task split), write end-to-end integration test"
```

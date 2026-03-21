# Tasks: Workflow Enhancements Bundle

**Feature**: Workflow Enhancements Bundle (FR-1 through FR-5)
**Spec**: `specs/20260321120000-workflow-enhancements/spec.md`
**Plan**: `specs/20260321120000-workflow-enhancements/plan.md`
**Branch**: `feat/phase-1-config-system-task-model`

---

## Phase 1: Setup

**Goal**: No dedicated setup phase needed — this feature modifies existing infrastructure with zero new dependencies.

---

## Phase 2: Foundational — Runs Directory Relocation (FR-4)

**Goal**: Move run storage from `runs/` to `.fdsx/runs/` with directory-based layout. This is foundational because FR-2 (streaming logs) depends on the new directory structure.

**Independent Test Criteria**: Run `fdsx run` and verify run data is written to `.fdsx/runs/<thread-id>/run.json`. Verify `fdsx resume` reads from the new location. Verify old `runs/` is no longer used.

- [x] T001 Update RunRecorder to directory-based layout under `.fdsx/runs/<thread-id>/run.json` in `src/fdsx/logging/recorder.py` (TDD: `tests/unit/test_recorder.py`)
- [x] T002 Update `resume_flow` to read run data from `.fdsx/runs/<thread-id>/run.json` in `src/fdsx/core/engine.py` (TDD: `tests/unit/test_engine.py` or `tests/integration/test_resume_interrupt.py`)
- [x] T003 [P] Update `CheckpointManager.list_threads` to scan `.fdsx/runs/` directories in `src/fdsx/checkpoint/manager.py` (TDD: `tests/unit/test_checkpoint.py`)
- [x] T004 [P] Update `.gitignore` to add `.fdsx/runs/` and remove old `runs/` entry

---

## Phase 3: Simplified Completion Output (FR-1)

**Goal**: Replace raw JSON dump with human-readable completion message. Suppress JSON from terminal output in all modes.

**Independent Test Criteria**: Run a workflow and see `✓ Workflow 'name' completed successfully in Xs` on stderr. On failure, see `✗ Workflow 'name' failed at state 'X' — error`. No JSON printed to stdout or stderr.

- [ ] T005 Add `display_completion_summary()` function to `src/fdsx/display/terminal.py` with success and failure formatting (TDD: `tests/unit/test_terminal.py`)
- [ ] T006 Wire `display_completion_summary()` into `run_flow()` in `src/fdsx/core/engine.py` — call on success and failure paths, calculate elapsed time from recorder timestamps (TDD: `tests/unit/test_engine.py`)
- [ ] T007 Suppress `typer.echo(json.dumps(...))` for all modes (single-flow, batch, tasks-dir) in `src/fdsx/cli/main.py` (TDD: integration test verifying no JSON on terminal)

---

## Phase 4: Live Agent Output Streaming + Per-State Logs (FR-2)

**Goal**: Stream provider output in real-time with `[StateName]` labels to stderr; write per-state log files to `.fdsx/runs/<thread-id>/logs/`.

**Independent Test Criteria**: Run a workflow and see labeled, real-time output like `[Planner] ...` on stderr. Verify per-state `.log` files exist under `.fdsx/runs/<thread-id>/logs/`. No log file created for states with no output.

- [ ] T008 Refactor `_run_subprocess` in `src/fdsx/providers/base.py` to add `stderr_callback` parameter with line-by-line stderr streaming (TDD: `tests/unit/test_subprocess_stdin.py`)
- [ ] T009 Add `stderr_callback` parameter to `execute()` in ProviderBase protocol and all providers: `src/fdsx/providers/base.py`, `claude.py`, `opencode.py`, `codex.py`, `system.py` (TDD: unit tests per provider)
- [ ] T010 Create `StreamLogger` class in `src/fdsx/logging/stream_logger.py` with `on_stdout(line)`/`on_stderr(line)` callbacks, `[state_name]` prefixing to stderr, per-state log file writing, lazy file creation, and ANSI passthrough (TDD: `tests/unit/test_stream_logger.py`)
- [ ] T011 Wire StreamLogger into compiler in `src/fdsx/core/compiler.py` — create StreamLogger instances in `_create_task_node` and `_create_branch_executor`, pass callbacks as `output_callback`/`stderr_callback` (TDD: integration test verifying log files and prefixed output)
- [ ] T012 [P] Verify parallel branch labeling works correctly — each branch's StreamLogger uses its own state name, output is interleaved with distinct labels (TDD: `tests/integration/test_parallel_flow.py`)

---

## Phase 5: Completed Tasks Directory (FR-3)

**Goal**: Move successful task files to `tasks/completed/`; scan both directories for next index.

**Independent Test Criteria**: After batch run, completed task files are in `tasks/completed/`. Failed tasks remain in `tasks/`. New task index scans both directories. Collision raises error, move failure logs warning but doesn't abort.

- [ ] T013 Add `move_task_to_completed()` utility in `src/fdsx/core/batch.py` — auto-create `tasks/completed/`, collision detection, warning on failure (TDD: `tests/unit/test_batch.py`)
- [ ] T014 Wire `move_task_to_completed()` into `run_tasks_dir` loop in `src/fdsx/core/engine.py` — move file only when ALL entries have status="completed" (TDD: `tests/integration/test_tasks_dir.py`)
- [ ] T015 Update task index scanning in `src/fdsx/core/batch.py` to scan both `tasks/` and `tasks/completed/` for max existing index in `write_task_files` (TDD: `tests/unit/test_batch.py`)

---

## Phase 6: Hooks System — Models & Config (FR-5, part 1)

**Goal**: Define hook data models and add hooks fields to Flow, state types, and FdsxConfig.

**Independent Test Criteria**: Hook models validate correctly. YAML with `hooks` field parses without error on Flow and all state types. Config merging handles hooks with list concatenation.

- [ ] T016 Define `HookEntry` and `HookConfig` Pydantic models in `src/fdsx/models/flow.py` (TDD: `tests/unit/test_models.py`)
- [ ] T017 [P] Add `hooks: HookConfig | None = None` field to `Flow`, `TaskState`, `ChoiceState`, `ParallelState`, `PassState`, `WaitState` in `src/fdsx/models/flow.py` (TDD: `tests/unit/test_models.py`)
- [ ] T018 [P] Add `hooks: HookConfig | None = None` to `FdsxConfig` in `src/fdsx/core/config.py` with correct `_deep_merge` handling for hook list concatenation (TDD: `tests/unit/test_config.py`)

---

## Phase 7: Hooks System — Executor & Collector (FR-5, part 2)

**Goal**: Implement hook execution engine and multi-level hook collection.

**Independent Test Criteria**: Hook commands execute with correct positional args and env vars. Abort-on-failure stops execution. Warn-on-failure logs and continues. Hooks collected in correct order: global → project → flow → state.

- [ ] T019 Create hook executor `execute_hooks()` in `src/fdsx/core/hooks.py` — iterate hooks, set env vars (FDSX_STATE_NAME, FDSX_STATUS, FDSX_DATA_PATH, FDSX_THREAD_ID, FDSX_FLOW_NAME), run subprocess, handle abort/warn failure policies (TDD: `tests/unit/test_hooks.py`)
- [ ] T020 Add hook data file generation in `src/fdsx/core/hooks.py` — write per-state JSON to `.fdsx/runs/<thread-id>/hooks/<state-name>/input.json` and `output.json` with `0o600`/`0o700` permissions (TDD: `tests/unit/test_hooks.py`)
- [ ] T021 Create hook collector `collect_hooks()` in `src/fdsx/core/hooks.py` — merge hooks across global → project → flow → state levels, return concatenated list (TDD: `tests/unit/test_hooks.py`)

---

## Phase 8: Hooks System — Wiring (FR-5, part 3)

**Goal**: Wire hooks into the compiler via wrapper/decorator pattern.

**Independent Test Criteria**: End-to-end flow with hooks configured at multiple levels fires hooks in correct order. Abort-hook failure halts workflow. Warn-hook failure continues. ParallelState hooks wrap dispatch/collector, not individual branches.

- [ ] T022 Create `_wrap_with_hooks()` in `src/fdsx/core/compiler.py` — wrapper function that calls `execute_hooks(on_start)` before node execution and `execute_hooks(on_complete)` after (TDD: `tests/integration/test_hooks_integration.py`)
- [ ] T023 Apply `_wrap_with_hooks()` in `compile_flow()` for all node types in `src/fdsx/core/compiler.py` — use `collect_hooks()` at compile time, handle ParallelState dispatch/collector wrapping (TDD: `tests/integration/test_hooks_integration.py`)

---

## Phase 9: Polish & Cross-Cutting Concerns

**Goal**: Final integration verification and cleanup.

- [ ] T024 Run full test suite (`uv run pytest tests/ -x`) and fix any regressions across all phases
- [ ] T025 Manual smoke test: run a multi-state workflow and verify all features work together — completion message, streaming labels, log files, run directory layout
- [ ] T026 [P] Verify hook + streaming interaction: hooks fire correctly around states that produce streaming output

---

## Dependencies

```
Phase 2 (FR-4: Runs Relocation)
  └── Phase 3 (FR-1: Completion Output) — depends on new run directory for JSON writes
  └── Phase 4 (FR-2: Streaming) — depends on new run directory for log files
        └── Phase 8 (FR-5 wiring) — hooks wrap nodes that now have streaming

Phase 5 (FR-3: Completed Tasks) — independent, can run after Phase 2

Phase 6 (FR-5 models) → Phase 7 (FR-5 executor) → Phase 8 (FR-5 wiring)
```

## Parallel Execution Opportunities

Within each phase, tasks marked `[P]` can run in parallel with the preceding task:
- **Phase 2**: T003 and T004 can run in parallel after T002
- **Phase 4**: T012 can run in parallel with T011
- **Phase 5**: All 3 tasks are sequential (dependency chain)
- **Phase 6**: T017 and T018 can run in parallel after T016

## Implementation Strategy

1. **MVP**: Phase 2 + Phase 3 (runs relocation + completion output) — immediate usability improvement
2. **Incremental**: Phase 4 (streaming) adds real-time visibility
3. **Independent**: Phase 5 (completed tasks) can be done at any point after Phase 2
4. **Complex last**: Phases 6-8 (hooks) build incrementally on each other

## Suggested takt Usage

```bash
# Phase 2: Runs Directory Relocation
takt run coder "Implement FR-4: Update RunRecorder, resume_flow, and CheckpointManager to use .fdsx/runs/ directory-based layout (T001-T004)"

# Phase 3: Simplified Completion Output
takt run coder "Implement FR-1: Add display_completion_summary, wire into engine, suppress JSON output (T005-T007)"

# Phase 4: Live Agent Output Streaming
takt run coder "Implement FR-2: Add stderr streaming to providers, create StreamLogger, wire into compiler (T008-T012)"

# Phase 5: Completed Tasks Directory
takt run coder "Implement FR-3: Add move_task_to_completed, wire into run_tasks_dir, update index scanning (T013-T015)"

# Phase 6: Hooks Models & Config
takt run coder "Implement FR-5 models: Define HookEntry/HookConfig, add hooks field to Flow/states/config (T016-T018)"

# Phase 7: Hooks Executor & Collector
takt run coder "Implement FR-5 executor: Create execute_hooks, hook data files, collect_hooks (T019-T021)"

# Phase 8: Hooks Wiring
takt run coder "Implement FR-5 wiring: Create _wrap_with_hooks, apply in compile_flow for all node types (T022-T023)"

# Phase 9: Polish
takt run coder "Run full test suite, manual smoke test, verify hook+streaming interaction (T024-T026)"
```

## Summary

- **Total tasks**: 26
- **Phase 2 (FR-4 Runs Relocation)**: 4 tasks
- **Phase 3 (FR-1 Completion Output)**: 3 tasks
- **Phase 4 (FR-2 Streaming)**: 5 tasks
- **Phase 5 (FR-3 Completed Tasks)**: 3 tasks
- **Phase 6 (FR-5 Models)**: 3 tasks
- **Phase 7 (FR-5 Executor)**: 3 tasks
- **Phase 8 (FR-5 Wiring)**: 2 tasks
- **Phase 9 (Polish)**: 3 tasks

# Tasks: Split Source Tracking & Global Task Variables

**Feature**: Split Source Tracking & Global Task Variables
**Spec**: `specs/20260324000402-split-source-global-vars/spec.md`
**Plan**: `specs/20260324000402-split-source-global-vars/plan.md`
**Generated**: 2026-03-24

---

## Phase 1: TaskFile Model — Add `source` Field

**Goal**: Add an optional `source` field to the `TaskFile` Pydantic model.

- [x] T001 Add unit tests for TaskFile `source` field construction in `tests/unit/test_models.py`
- [x] T002 Add `source: str | None = Field(default=None)` to `TaskFile` model in `src/fdsx/models/task.py`

## Phase 2: TaskFile Serialization — Write `source` to YAML

**Goal**: When saving a TaskFile with `source` set, include it in the YAML output. Omit when `None`.

- [ ] T003 Add serialization tests for `save_task_file` with/without `source` in `tests/unit/test_models.py`
- [ ] T004 Update `save_task_file` to conditionally include `source` in serialized dict in `src/fdsx/models/task.py`

## Phase 3: TaskFile Deserialization — Read `source` from YAML

**Goal**: When loading a task YAML that contains a `source` field, populate `TaskFile.source`.

- [ ] T005 Add deserialization tests for `load_task_file` with/without `source` in `tests/unit/test_models.py`
- [ ] T006 Update `load_task_file` to extract `source` from raw dict in both flat and list branches in `src/fdsx/models/task.py`

## Phase 4: Split Command — Inject Source Path

**Goal**: `fdsx split <file>` records the verbatim input path as `source` in each generated task YAML.

- [ ] T007 Add integration test for split with source tracking in `tests/integration/test_split.py`
- [ ] T008 Update `write_task_files` to accept and assign `source`, and pass source from split CLI in `src/fdsx/core/batch.py` and `src/fdsx/cli/main.py`

## Phase 5: Static Analysis — Global Variable Recognition

**Goal**: `{task}` and `{source}` in non-start states no longer trigger static analysis warnings.

- [ ] T009 Add tests for global variable recognition (`{task}`, `{source}`, `{unknown_var}`) in `tests/unit/test_models.py` or `tests/unit/test_variables.py`
- [ ] T010 Define `GLOBAL_TASK_VARS = {"task", "source"}` and seed into `analyze_variable_references` in `src/fdsx/core/variables.py`

## Phase 6: Execution — Source Auto-Injection

**Goal**: `{source}` resolves in all workflow states during task execution.

- [ ] T011 Add integration tests for source injection during execution in `tests/integration/test_tasks_dir.py` and `tests/integration/test_batch.py`
- [ ] T012 Add `task_inputs["source"] = task_file.source or ""` in `run_batch` and `run_tasks_dir` in `src/fdsx/core/engine.py`

## Phase 7: Update Existing Tests

**Goal**: Verify all existing tests pass with the changes; fix any broken assertions.

- [ ] T013 Run full test suite and fix any tests broken by the new `source` field

---

## Dependencies

```
Phase 1 (T001-T002) ── model exists
    │
    ├──▶ Phase 2 (T003-T004) ── serialization
    │        │
    │        └──▶ Phase 3 (T005-T006) ── deserialization
    │                 │
    │                 └──▶ Phase 4 (T007-T008) ── split command
    │                          │
    │                          └──▶ Phase 6 (T011-T012) ── execution injection
    │
    └──▶ Phase 5 (T009-T010) ── static analysis (independent of Phases 2-4)

Phase 7 (T013) ── after all other phases complete
```

**Key observations**:
- Phase 5 (static analysis) is independent of Phases 2-4 and can run in parallel after Phase 1
- Within each phase, test tasks must complete before implementation tasks (TDD)
- Phase 7 is a final validation gate

## Parallel Execution Opportunities

| Tasks | Can Parallel? | Reason |
|-------|--------------|--------|
| T001, T009 | No | T009 tests variable analysis, unrelated to model, but both could start after repo setup |
| T003, T009 | Yes | Different files: `test_models.py` vs `test_variables.py` |
| T004, T010 | Yes | Different files: `task.py` vs `variables.py` |
| T007, T011 | No | T011 depends on Phase 4 (source must exist in YAML first) |

## Implementation Strategy

1. **MVP**: Phases 1-4 deliver the core value — `fdsx split` records source and task files round-trip it
2. **Incremental**: Phase 5 (static analysis) and Phase 6 (execution injection) extend the value to runtime
3. **Validation**: Phase 7 ensures nothing is broken

## Task Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| Phase 1 | T001-T002 | TaskFile model |
| Phase 2 | T003-T004 | Serialization |
| Phase 3 | T005-T006 | Deserialization |
| Phase 4 | T007-T008 | Split command |
| Phase 5 | T009-T010 | Static analysis |
| Phase 6 | T011-T012 | Execution injection |
| Phase 7 | T013 | Test validation |
| **Total** | **13 tasks** | |

## Suggested Execution Commands

```bash
# Phase 1: TaskFile Model
takt run code "Phase 1: Add source field to TaskFile model (T001-T002)"

# Phase 2: Serialization
takt run code "Phase 2: Update save_task_file to write source to YAML (T003-T004)"

# Phase 3: Deserialization
takt run code "Phase 3: Update load_task_file to read source from YAML (T005-T006)"

# Phase 4: Split Command
takt run code "Phase 4: Pass source path through split pipeline (T007-T008)"

# Phase 5: Static Analysis (can run in parallel with Phases 2-4)
takt run code "Phase 5: Add GLOBAL_TASK_VARS and update analyze_variable_references (T009-T010)"

# Phase 6: Execution Injection
takt run code "Phase 6: Inject source into task_inputs at execution sites (T011-T012)"

# Phase 7: Validation
takt run code "Phase 7: Run full test suite and fix any broken tests (T013)"
```

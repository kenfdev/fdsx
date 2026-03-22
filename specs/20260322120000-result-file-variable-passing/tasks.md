# Tasks: File-Based Variable Passing (`result_file`)

**Feature**: File-Based Variable Passing (`result_file`)
**Spec**: [spec.md](./spec.md)
**Plan**: [plan.md](./plan.md)
**Created**: 2026-03-22

---

## Phase 1: Model + Helper (TDD)

> FR-1 (field definition), FR-2 (file writing helper). Write tests first, then implement.

- [x] T001 Write TDD tests for `write_result_to_file()` helper in `tests/unit/test_result_file.py`
  - String value → creates `<varname>.md` with string content
  - Dict value → creates `<varname>.json` with JSON content
  - List value → creates `<varname>.json` with JSON content
  - `data/` directory created automatically if missing
  - File overwritten on second call with same varname
  - Returns absolute file path as string
  - UTF-8 encoding for non-ASCII content

- [x] T002 Implement `write_result_to_file()` in `src/fdsx/core/variables.py`
  - Signature: `write_result_to_file(varname: str, value: Any, run_dir: Path) -> str`
  - Creates `<run_dir>/data/` if needed
  - Writes `.md` for str, `.json` for dict/list
  - Returns `str(file_path.resolve())`

- [x] T003 Write TDD tests for `result_file` model field validation in `tests/unit/test_result_file.py`
  - `TaskState` accepts `result_file: "$.plan_ref"`
  - `ParallelState` accepts `result_file: "$.reviews_ref"`
  - Rejects missing `$.` prefix
  - Rejects nested path `$.foo.bar`
  - Defaults to `None` when not set
  - `result_file` and `result_path` can coexist with different variable names

- [x] T004 Add `result_file` field to `TaskState` and `ParallelState` in `src/fdsx/models/flow.py`
  - Add `result_file: str | None = Field(default=None, ...)`
  - Add `@field_validator` or `@model_validator` to require `$.` prefix and reject nested paths

- [x] T005 Run `pytest tests/unit/test_result_file.py` — verify T001 + T003 tests pass

## Phase 2: Engine + Static Analysis (TDD)

> FR-5 (static analysis), `_meta.run_dir` propagation. Tests first, then implement.

- [x] T006 Write TDD tests for `_meta.run_dir` propagation in `tests/unit/test_result_file.py`
  - `initial_state["_meta"]["run_dir"]` is set in `run_flow` (mock compile_flow)
  - `run_dir` value matches `<_runs_base>/runs/<thread_id>`

- [x] T007 Add `run_dir` to `_meta` in `src/fdsx/core/engine.py`
  - In `run_flow()`: add `"run_dir": str(run_dir)` to `_meta` dict
  - In `resume_flow()`: compute and add `run_dir` to state dict if not already present
  - Compute: `run_dir = _runs_base / RUNS_DIR_NAME / thread_id`

- [x] T008 Write TDD tests for static analysis recognizing `result_file` in `tests/unit/test_result_file.py`
  - `get_result_paths()` includes `result_file` variable in returned set
  - `analyze_variable_references()` does not flag `{reviews_ref}` as undefined when upstream state has `result_file: $.reviews_ref`

- [x] T009 Update static analysis in `src/fdsx/core/variables.py`
  - In `get_result_paths()`: add `result_file` handling for TaskState and ParallelState
  - If `state.result_file` is set and starts with `$.`, strip prefix and add to `result_paths`

- [x] T010 Run `pytest tests/unit/test_result_file.py` — verify T006 + T008 tests pass

## Phase 3: Compiler Wiring (TDD)

> FR-2 (file writing on completion), FR-3 (usage modes). Tests first, then implement.

- [x] T011 Write TDD tests for task node `result_file` wiring in `tests/unit/test_result_file.py`
  - Task with `result_file` only → file written, path stored in variable, no raw data in state
  - Task with both `result_path` and `result_file` → both set correctly
  - Task without `result_file` → no file I/O (existing behavior preserved)
  - Verify `write_result_to_file` called with correct arguments

- [x] T012 Wire `result_file` in task node function in `src/fdsx/core/compiler.py`
  - In `_create_task_node()`, after `result_path` handling: call `write_result_to_file()` and store path via `set_jsonpath`
  - Handle file-only mode (result_file set, result_path not used for file)

- [x] T013 Write TDD tests for parallel collector node `result_file` wiring in `tests/unit/test_result_file.py`
  - Parallel with `result_file` → clean_results written to file, path stored
  - Parallel with both `result_path` and `result_file` → both set
  - Parallel without `result_file` → unchanged behavior

- [x] T014 Wire `result_file` in parallel collector node in `src/fdsx/core/compiler.py`
  - In `_create_parallel_collector_node()`, after `set_jsonpath(state.result_path, ...)`: call `write_result_to_file()` and store path

- [x] T015 Register `result_file` keys in `_build_state_schema()` in `src/fdsx/core/compiler.py`
  - After result_path registration for TaskState and ParallelState: register `result_file` top-level key via `_top_level_key()`

- [x] T016 Register `result_file` in `_extract_result_paths()` in `src/fdsx/core/compiler.py`
  - Add `result_file` to paths list for TaskState and ParallelState

- [x] T017 Run `pytest tests/unit/` — verify T011 + T013 tests pass, no regressions

## Phase 4: Integration Test

> FR-6 (test coverage), end-to-end validation with system provider.

- [x] T018 Write integration test with system provider in `tests/integration/test_result_file.py`
  - Two-state workflow: echo → read file. Assert file exists at `<run_dir>/data/output_ref.md`, content correct, variable contains absolute path
  - Parallel state with `result_file`: two echo branches, assert `.json` file created, variable contains path

- [x] T019 Write regression test for existing workflows in `tests/integration/test_result_file.py`
  - Run workflow without `result_file`, verify no `data/` directory is created

- [x] T020 Run full test suite `pytest tests/` — verify no regressions

---

## Dependencies

```
T001 → T002 (tests before helper impl)
T003 → T004 (tests before model field)
T002, T004 → T005 (impl before verification)
T006 → T007 (tests before engine change)
T008 → T009 (tests before static analysis change)
T007, T009 → T010 (impl before verification)
T011 → T012 (tests before task node wiring)
T013 → T014 (tests before parallel node wiring)
T012, T014 → T015, T016 (wiring before schema/extract registration)
T015, T016 → T017 (registration before verification)
T017 → T018, T019 (unit tests pass before integration)
T018, T019 → T020 (integration before full regression)
```

## Parallel Execution

| Phase | Parallelizable tasks |
|---|---|
| Phase 1 | T001 + T003 (independent test groups), T002 + T004 (independent impl files) |
| Phase 2 | T006 + T008 (independent test groups), T007 + T009 (independent impl files) |
| Phase 3 | T011 + T013 (independent test groups), T015 + T016 (independent compiler registrations) |
| Phase 4 | T018 + T019 (independent integration tests) |

## Implementation Strategy

- **MVP**: Phase 1 (Model + Helper) — establishes the core `write_result_to_file()` and model validation
- **Full scope**: All 4 phases (20 tasks total)
- **Incremental delivery**: Each phase is a complete, testable increment. Phase 1-2 can be merged independently of Phase 3-4.

## Summary

| Metric | Value |
|---|---|
| Total tasks | 20 |
| Test tasks | 8 (T001, T003, T006, T008, T011, T013, T018, T019) |
| Implementation tasks | 8 (T002, T004, T007, T009, T012, T014, T015, T016) |
| Verification tasks | 4 (T005, T010, T017, T020) |
| Parallel opportunities | Phase 1 (2+2), Phase 2 (2+2), Phase 3 (2+2), Phase 4 (2) |

## Suggested takt usage

```bash
# Phase 1: Model + Helper (TDD)
takt run coder "Write TDD tests for write_result_to_file() helper — string→.md, dict→.json, list→.json, auto-create data/ dir, overwrite, return absolute path, UTF-8 — in tests/unit/test_result_file.py"
takt run coder "Implement write_result_to_file(varname, value, run_dir) in src/fdsx/core/variables.py — creates <run_dir>/data/<varname>.<ext>, .md for str, .json for dict/list, returns absolute path"
takt run coder "Write TDD tests for result_file model field validation — TaskState/ParallelState accept $.varname, reject missing $. prefix, reject nested paths, default None, coexist with result_path — in tests/unit/test_result_file.py"
takt run coder "Add result_file: str | None field to TaskState and ParallelState in src/fdsx/models/flow.py with field_validator requiring $. prefix and rejecting nested paths"
takt run coder "Run pytest tests/unit/test_result_file.py and verify all Phase 1 tests pass"

# Phase 2: Engine + Static Analysis (TDD)
takt run coder "Write TDD tests for _meta.run_dir propagation — verify run_flow sets _meta.run_dir matching <_runs_base>/runs/<thread_id> — in tests/unit/test_result_file.py"
takt run coder "Add run_dir to _meta dict in engine.py run_flow() and resume_flow() — compute as _runs_base / RUNS_DIR_NAME / thread_id"
takt run coder "Write TDD tests for static analysis recognizing result_file — get_result_paths includes result_file vars, analyze_variable_references does not flag them as undefined — in tests/unit/test_result_file.py"
takt run coder "Update get_result_paths() in src/fdsx/core/variables.py to include result_file handling for TaskState and ParallelState"
takt run coder "Run pytest tests/unit/test_result_file.py and verify all Phase 2 tests pass"

# Phase 3: Compiler Wiring (TDD)
takt run coder "Write TDD tests for task node result_file wiring — file-only mode, both mode, no-file mode, verify write_result_to_file args — in tests/unit/test_result_file.py"
takt run coder "Wire result_file in _create_task_node() in src/fdsx/core/compiler.py — call write_result_to_file after result_path, store path via set_jsonpath"
takt run coder "Write TDD tests for parallel collector node result_file wiring — file written, both mode, unchanged without result_file — in tests/unit/test_result_file.py"
takt run coder "Wire result_file in _create_parallel_collector_node() in src/fdsx/core/compiler.py — call write_result_to_file, store path via set_jsonpath"
takt run coder "Register result_file keys in _build_state_schema() in src/fdsx/core/compiler.py for TaskState and ParallelState"
takt run coder "Register result_file in _extract_result_paths() in src/fdsx/core/compiler.py for TaskState and ParallelState"
takt run coder "Run pytest tests/unit/ and verify all Phase 3 tests pass with no regressions"

# Phase 4: Integration Test
takt run coder "Write integration test with system provider in tests/integration/test_result_file.py — two-state echo workflow with result_file, parallel state with result_file"
takt run coder "Write regression test in tests/integration/test_result_file.py — workflow without result_file creates no data/ directory"
takt run coder "Run pytest tests/ full suite and verify no regressions"
```

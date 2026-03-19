# Tasks: Batch Task Persistence & Workflow Auto-Selection

## Phase 1: Config System & Task File Model

- [x] T1: Config model — Create `FdsxConfig`, `TaskSplitterConfig`, `WorkflowSelectorConfig` pydantic models with validation in `src/fdsx/core/config.py` (NEW)
- [x] T2: Config loader — Implement `load_config()`: resolve XDG path, load global + project YAML, merge, return `FdsxConfig` in `src/fdsx/core/config.py`
- [x] T3: Config defaults — Built-in defaults when no config file exists (provider: claude, model: claude-sonnet-4-6) in `src/fdsx/core/config.py`
- [x] T4: TaskEntry model — Create `TaskEntry` pydantic model with status enum validation in `src/fdsx/models/task.py` (NEW)
- [x] T5: TaskFile model — Create `TaskFile` model supporting single-task (flat) and multi-task (list) formats in `src/fdsx/models/task.py`
- [x] T6: TaskFile I/O — `load_task_file(path)` and `save_task_file(path, task_file)` functions
- [x] T7: Unit tests — TDD tests for config loading (global, project, merge, defaults) and task file parsing/serialization in `tests/unit/test_config.py` (NEW) and `tests/unit/test_task_model.py` (NEW)

## Phase 2: Flow Model Changes

- [x] T8: Add description field — Add required `description: str` to `Flow` model in `src/fdsx/models/flow.py`
- [x] T9: Remove task_splitter — Remove `task_splitter` field from `Flow`. Handle gracefully if present in YAML (custom error) in `src/fdsx/models/flow.py`
- [x] T10: Update loader errors — Customize validation error for missing `description` with actionable guidance in `src/fdsx/core/loader.py`
- [x] T11: Update example workflow — Add `description` to `examples/workflows/plan-implement-review.yaml`
- [x] T12: Update tests — Fix broken tests from model changes, add tests for new validation in `tests/unit/test_models.py` and `tests/unit/test_loader.py`

## Phase 3: Split Command

- [x] T13: Refactor split prompt — Update `_build_task_split_prompt()` to request JSON with dependency groups in `src/fdsx/core/batch.py`
- [x] T14: JSON parser — New `_parse_structured_tasks()` replacing `_parse_task_list()`, returns `list[list[TaskEntry]]` in `src/fdsx/core/batch.py`
- [x] T15: File writer — New `write_task_files(groups, tasks_dir)` — creates numbered YAML files from groups in `src/fdsx/core/batch.py`
- [x] T16: Split CLI command — Add `split` command to typer app with `task-file` arg and `--force` flag in `src/fdsx/cli/main.py`
- [x] T17: Non-empty dir guard — Check `.fdsx/tasks/` is empty, error or clear with `--force`
- [x] T18: Config integration — Split command reads task_splitter config from `load_config()` instead of flow
- [x] T19: Unit tests — TDD: JSON parsing, file writing, non-empty dir guard in `tests/unit/test_batch.py`
- [x] T20: Integration test — End-to-end split with mock provider, verify file output in `tests/integration/test_split.py` (NEW)

## Phase 4: Tasks-Dir Run Mode

- [x] T21: Tasks-dir loader — Read and sort task files from directory, parse each as `TaskFile` in `src/fdsx/core/engine.py`
- [x] T22: Status filter — Skip `completed` entries; mark `failed`/`running` as retriable in `src/fdsx/core/engine.py`
- [x] T23: Status persistence — After each task execution, update status + thread_id/error in YAML file in `src/fdsx/core/engine.py`
- [x] T24: Per-entry tracking — For multi-task files, track and update per-entry status in `src/fdsx/core/engine.py`
- [x] T25: Run orchestrator — `run_tasks_dir()` — iterate files, execute entries, handle errors, display summary in `src/fdsx/core/engine.py`
- [x] T26: CLI integration — Add `--tasks-dir` option, mutual exclusivity with `--tasks`/`--input`, make `workflow` argument optional in `src/fdsx/cli/main.py`
- [x] T27: Resume test — Integration test: run partial batch, simulate crash, resume and verify skip/retry logic in `tests/integration/test_tasks_dir.py` (NEW)
- [x] T28: Summary display — Update batch summary to show skipped (completed), retried, new tasks in `src/fdsx/core/batch.py`

## Phase 5: Workflow Auto-Selection

- [x] T29: Workflow discovery — `discover_workflows(dir)` — glob `*.yaml` files, load each, return list of `(path, description)` in `src/fdsx/core/selector.py` (NEW)
- [x] T30: Selection prompt — Build LLM prompt with task description + workflow descriptions, request workflow filename in `src/fdsx/core/selector.py`
- [x] T31: Select function — `select_workflow(task_desc, workflows, config)` — single workflow = auto, multiple = LLM call in `src/fdsx/core/selector.py`
- [x] T32: Confirm mode UX — Present selected workflow, prompt for approval. On rejection, show full list for manual pick in `src/fdsx/cli/main.py`
- [x] T33: Auto mode — `--auto-workflow` flag and `auto_workflow` config — skip confirmation in `src/fdsx/cli/main.py`
- [x] T34: CLI flags — Add `--auto-workflow` / `--confirm-workflow` flags, CLI overrides config in `src/fdsx/cli/main.py`
- [x] T35: Integration with run — Wire selector into `run_tasks_dir()` for per-task workflow selection in `src/fdsx/core/engine.py`
- [x] T36: FR-6.3 batch confirm — In confirm mode with tasks-dir, present all workflow selections before execution
- [x] T37: Unit tests — TDD: discovery, selection logic, single-workflow shortcut, no-workflows error in `tests/unit/test_selector.py` (NEW)
- [x] T38: Integration test — End-to-end auto-selection with mock provider in `tests/integration/test_auto_select.py` (NEW)

## Phase 6: Polish & Backward Compatibility

- [x] T39: Backward compat — Ensure `--tasks` (in-memory batch) still works. Reads task_splitter from config instead of flow in `src/fdsx/core/engine.py`
- [x] T40: Error messages — Clear errors for: no workflows found, invalid task file, no config, missing description
- [x] T41: Help text — Update all command help text and `--help` output in `src/fdsx/cli/main.py`
- [x] T42: Edge cases — Empty tasks dir, all tasks completed (no-op), single task file, invalid YAML task files
- [x] T43: End-to-end test — Full flow: split → edit → run → crash → resume → complete

# Tasks: Fix Split Command and Improve Task Granularity

**Feature**: Fix `fdsx split` emptiness check and rewrite task split prompt for feature-level granularity
**Spec**: [spec.md](./spec.md)
**Plan**: [plan.md](./plan.md)

---

## Phase 1: Fix Emptiness Check (Scenario 1, 2, 4)

**Goal**: Allow `fdsx split` to succeed when only the `completed/` subdirectory exists, while preserving `--force` behavior.

**Independent test criteria**: After this phase, `fdsx split` works without `--force` when `completed/` is the only entry in `.fdsx/tasks/`. Pending `.yaml` files still block split. `--force` clears pending `.yaml` but preserves `completed/`. Task numbering continues from highest index across both directories.

- [x] T001 Write integration tests for split succeeding with only `completed/` dir in `tests/integration/test_split.py`
  - `test_split_command_succeeds_with_only_completed_dir`: Create `.fdsx/tasks/completed/` with a YAML inside, no pending tasks. Run split without `--force`. Verify exit code 0 and new task files created.
  - `test_split_command_preserves_completed_dir_on_normal_split`: After successful split with `completed/` present, verify `completed/` dir and contents are untouched.
  - `test_split_command_numbering_continues_from_completed`: `completed/` has `001-old.yaml`, `002-old.yaml`. New split starts from 003.
  - `test_split_command_still_blocks_with_pending_yaml`: Tasks dir has a pending `.yaml` file (not in `completed/`). Verify split without `--force` still fails.

- [x] T002 Implement emptiness check fix in `src/fdsx/cli/main.py`
  - Modify emptiness check (~line 292): replace `any(tasks_dir.iterdir())` with a check filtering out the `completed` subdirectory
  - Import `COMPLETED_SUBDIR` from `fdsx.core.batch`
  - New check: `any(entry for entry in tasks_dir.iterdir() if entry.name != COMPLETED_SUBDIR)`
  - Run T001 tests to verify

- [x] T003 Write integration test for `--force` preserving `completed/` dir in `tests/integration/test_split.py`
  - `test_split_command_force_preserves_completed_dir`: Create tasks dir with pending `.yaml` AND `completed/` subdir. Run split with `--force`. Verify pending `.yaml` removed, `completed/` preserved with contents intact.

---

## Phase 2: Rewrite Task Split Prompt (Scenario 3)

**Goal**: Produce feature-level tasks with structured sub-steps instead of micro-tasks.

**Independent test criteria**: After this phase, the split prompt instructs feature-level grouping, includes sub-steps instruction, anti-examples, and a few-shot example. Integration test verifies the output structure.

- [x] T004 Write unit tests for prompt content verification in `tests/unit/test_batch.py`
  - `test_build_task_split_prompt_contains_feature_level_instruction`: Verify prompt contains instruction to group related steps into feature-level tasks.
  - `test_build_task_split_prompt_contains_substeps_instruction`: Verify prompt instructs numbered sub-steps within descriptions.
  - `test_build_task_split_prompt_contains_anti_examples`: Verify prompt includes anti-examples of micro-tasks.
  - `test_build_task_split_prompt_contains_few_shot_example`: Verify prompt includes a few-shot example.

- [x] T005 Rewrite `_build_task_split_prompt()` in `src/fdsx/core/batch.py`
  - Rewrite the prompt (~lines 106-154):
    - Change core instruction from "split into individual, self-contained task descriptions" to "group related work into feature-level tasks"
    - Add instruction: each task description should include numbered sub-steps
    - Add few-shot example showing BAD (5 micro-tasks) vs GOOD (1 feature-level task with 5 numbered sub-steps)
    - Add anti-pattern guidance: "Do NOT create tasks for single file operations, single commands, or trivially small changes"
    - Keep the JSON output format specification unchanged
  - Run T004 tests to verify

- [x] T006 Write integration test for feature-level output in `tests/integration/test_split.py`
  - `test_split_produces_feature_level_tasks`: Use mock provider returning a feature-level response (single task with sub-steps in description). Verify the output contains fewer files than micro-split. Verify task description contains numbered steps.

---

## Dependencies

```
Phase 1 (T001 → T002 → T003)  ──→  Phase 2 (T004 → T005 → T006)
                                     │
                                     └─ Phase 2 can start after Phase 1 is verified
```

- T001 must pass before T002 (TDD: write tests first)
- T002 must pass before T003 (emptiness fix enables force test)
- T004 must pass before T005 (TDD: write tests first)
- T005 must pass before T006 (prompt must exist for integration test)
- Phase 2 is independent of Phase 1 at the code level (different files), but logically follows

## Parallel Opportunities

- **Within Phase 1**: T001 and T003 test authoring can be done in parallel (different test concerns), but T002 implementation must wait for T001.
- **Cross-phase**: T004 (unit tests for prompt) can start as soon as Phase 1 implementation (T002) is underway, since they touch different files.

## Implementation Strategy

1. **MVP**: Phase 1 alone delivers immediate value — developers no longer need `--force` after completed runs
2. **Full delivery**: Phase 2 improves task quality, reducing AI cost per batch run
3. **Files modified**: `src/fdsx/cli/main.py`, `src/fdsx/core/batch.py`
4. **Files extended**: `tests/integration/test_split.py`, `tests/unit/test_batch.py`
5. **No new files, no schema changes, no new dependencies**

## Suggested takt Usage

```bash
# Phase 1: Fix emptiness check
takt run coder "Phase 1: Write integration tests for split with completed dir (T001) in tests/integration/test_split.py"
takt run coder "Phase 1: Implement emptiness check fix (T002) in src/fdsx/cli/main.py"
takt run coder "Phase 1: Write force-preserves-completed test (T003) in tests/integration/test_split.py"

# Phase 2: Rewrite split prompt
takt run coder "Phase 2: Write unit tests for prompt content (T004) in tests/unit/test_batch.py"
takt run coder "Phase 2: Rewrite _build_task_split_prompt (T005) in src/fdsx/core/batch.py"
takt run coder "Phase 2: Write integration test for feature-level output (T006) in tests/integration/test_split.py"
```

## Summary

| Phase | Tasks | Files |
|---|---|---|
| 1: Emptiness Check Fix | T001–T003 | `cli/main.py`, `tests/integration/test_split.py` |
| 2: Prompt Rewrite | T004–T006 | `core/batch.py`, `tests/unit/test_batch.py`, `tests/integration/test_split.py` |
| **Total** | **6 tasks** | **4 files** |

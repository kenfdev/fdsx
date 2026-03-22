# Tasks: Fix Realtime Streaming Output

**Feature**: Fix subprocess stdout/stderr buffering and StreamLogger flush for real-time output delivery
**Spec**: [spec.md](./spec.md)
**Plan**: [impl-plan.md](./impl-plan.md)

---

## Phase 1: TDD — Write Regression Test (RED)

**Goal**: Write a test that fails with the current buffered implementation and will pass after the fix.

**Independent test criteria**: After this phase, a regression test exists that proves stdout/stderr lines are delivered incrementally (not batched at process exit). The test currently FAILS (RED) — confirming the buffering issue is real.

- [x] T001 Create TDD regression test for realtime stdout/stderr delivery in `tests/unit/test_subprocess_realtime_streaming.py`
  - `test_stdout_lines_delivered_before_process_exits`: Spawn subprocess printing 3 lines with `sleep 0.3` between each. Record `time.time()` in `output_callback` per line and after `_run_subprocess()` returns. Assert at least one callback timestamp is >0.2s before completion timestamp.
  - `test_stderr_lines_delivered_before_process_exits`: Same approach for stderr via `stderr_callback`.

- [x] T002 Run regression test to confirm RED (fails before fix) via `uv run pytest tests/unit/test_subprocess_realtime_streaming.py -v`
  - Expected: FAIL — callbacks fire only after process exits due to Python's iterator read-ahead buffering

---

## Phase 2: Fix Subprocess Buffering (GREEN)

**Goal**: Replace buffered `for line in` iterator with unbuffered `readline()` loop in both stdout and stderr paths.

**Independent test criteria**: After this phase, the TDD regression test PASSES (GREEN). Stdout and stderr lines are delivered to callbacks immediately as each line is produced by the subprocess.

- [x] T003 Fix stdout reading in `_run_subprocess()` in `src/fdsx/providers/base.py` (lines 146–154)
  - Replace `for line in process.stdout:` with `while True: line = process.stdout.readline(); if not line: break; line = line.rstrip("\n")`

- [x] T004 Fix stderr reading in `_read_stderr()` in `src/fdsx/providers/base.py` (lines 134–138)
  - Replace `for raw_line in process.stderr:` with `while True: raw_line = process.stderr.readline(); if not raw_line: break; line = raw_line.rstrip("\n")`

- [x] T005 Run regression test to confirm GREEN (passes after fix) via `uv run pytest tests/unit/test_subprocess_realtime_streaming.py -v`
  - Expected: PASS

---

## Phase 3: Fix StreamLogger Stderr Flush

**Goal**: Ensure terminal output appears immediately after each StreamLogger print by flushing stderr.

**Independent test criteria**: After this phase, `sys.stderr.flush()` is called after every `print()` in StreamLogger's `on_stdout()` and `on_stderr()`, ensuring no Python-level stderr buffering delays terminal output.

- [ ] T006 [P] Add `sys.stderr.flush()` after `print()` in `on_stdout()` in `src/fdsx/logging/stream_logger.py` (line 59)

- [ ] T007 [P] Add `sys.stderr.flush()` after `print()` in `on_stderr()` in `src/fdsx/logging/stream_logger.py` (line 70)

---

## Phase 4: Verification

**Goal**: Confirm all existing tests pass and no regressions introduced.

**Independent test criteria**: Full test suite passes, including the new streaming regression test and all existing subprocess/streaming tests.

- [ ] T008 Run full test suite including streaming-specific tests via `uv run pytest tests/ -v`
  - Expected: All tests pass unchanged (existing + new streaming regression test)

---

## Dependencies

```
Phase 1 (T001 → T002)  ──→  Phase 2 (T003 → T004 → T005)  ──→  Phase 4 (T008)
                                                                      ↑
Phase 3 (T006, T007)  ────────────────────────────────────────────────┘
```

- T001 must complete before T002 (TDD: write test, then run to confirm RED)
- T002 must confirm RED before T003–T004 (TDD discipline)
- T003 and T004 can be done together (same file, different functions) but are sequential for safety
- T005 must run after T003+T004 (confirm GREEN)
- T006 and T007 are parallelizable (different methods in same file, no cross-dependency)
- Phase 3 is independent of Phase 2 at the code level (different files) — can start in parallel
- T008 runs after all implementation phases complete

## Parallel Opportunities

- **T006 + T007**: Both add `sys.stderr.flush()` to different methods in `stream_logger.py` — can be done in one pass
- **Phase 3 vs Phase 2**: Phase 3 touches `stream_logger.py` while Phase 2 touches `base.py` — independent files, can be done concurrently

## Implementation Strategy

1. **MVP**: Phases 1–2 alone deliver the core value — real-time stdout/stderr delivery from subprocesses
2. **Full delivery**: Phase 3 adds terminal flush to eliminate the last-mile buffering delay
3. **Files modified**: `src/fdsx/providers/base.py`, `src/fdsx/logging/stream_logger.py`
4. **Files created**: `tests/unit/test_subprocess_realtime_streaming.py`
5. **No new dependencies, no schema changes, stdlib only**

## Suggested takt Usage

```bash
# Phase 1: TDD regression test (RED)
takt run coder "Phase 1: Create TDD regression test for realtime stdout/stderr delivery (T001) in tests/unit/test_subprocess_realtime_streaming.py"
takt run coder "Phase 1: Run regression test to confirm RED (T002) via uv run pytest tests/unit/test_subprocess_realtime_streaming.py -v"

# Phase 2: Fix subprocess buffering (GREEN)
takt run coder "Phase 2: Fix stdout readline loop (T003) and stderr readline loop (T004) in src/fdsx/providers/base.py"
takt run coder "Phase 2: Run regression test to confirm GREEN (T005) via uv run pytest tests/unit/test_subprocess_realtime_streaming.py -v"

# Phase 3: StreamLogger flush
takt run coder "Phase 3: Add sys.stderr.flush() in on_stdout (T006) and on_stderr (T007) in src/fdsx/logging/stream_logger.py"

# Phase 4: Verification
takt run coder "Phase 4: Run full test suite (T008) via uv run pytest tests/ -v"
```

## Summary

| Phase | Tasks | Files |
|---|---|---|
| 1: TDD Regression Test (RED) | T001–T002 | `tests/unit/test_subprocess_realtime_streaming.py` (new) |
| 2: Fix Subprocess Buffering (GREEN) | T003–T005 | `src/fdsx/providers/base.py` |
| 3: StreamLogger Flush | T006–T007 | `src/fdsx/logging/stream_logger.py` |
| 4: Verification | T008 | — |
| **Total** | **8 tasks** | **3 files** |

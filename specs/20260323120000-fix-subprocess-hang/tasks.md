# Tasks: Fix Claude CLI Subprocess Hang

**Feature:** Fix Claude CLI subprocess hang after stream completion
**Spec:** `specs/20260323120000-fix-subprocess-hang/spec.md`
**Plan:** `specs/20260323120000-fix-subprocess-hang/plan.md`

---

## Phase 1: Setup

No setup tasks required. No new dependencies — uses Python stdlib only (`threading`, `subprocess`, `logging`).

---

## Phase 2: Foundational — Refactor stdout to daemon thread

**Goal:** Move stdout reading from the main thread into a daemon thread, mirroring the existing stderr pattern. Pure refactor — no behavioral change. This unblocks the main thread to monitor the completion event in later phases.

**Independent test criteria:** All existing tests pass. New tests verify stdout/stderr collection, exit codes, callbacks, and timeout behavior with the threaded reader.

- [x] T001 Write TDD tests for stdout-in-thread refactor in `tests/unit/test_subprocess_completion.py`
  - Process with stdout output returns correct `ProviderResult.stdout`
  - Process with multi-line stdout returns all lines joined by newline
  - Process with stderr returns correct `ProviderResult.stderr`
  - Process with both stdout and stderr returns both correctly
  - Process with exit code 0 and non-zero exit codes
  - `output_callback` is called for each stdout line
  - `stderr_callback` is called for each stderr line
  - Timeout still works (process killed after timeout, exit code 124)
  - Uses real subprocesses (`python -c "..."`)

- [x] T002 Refactor `_run_subprocess` to move stdout reading into daemon thread in `src/fdsx/providers/base.py`
  - Create `_read_stdout` thread function (mirrors `_read_stderr`)
  - Start `stdout_thread` as daemon thread
  - Main thread: `stdout_thread.join()` then `stderr_thread.join(5)` then `process.wait()`
  - No new parameters — structural refactor only
  - Verify: T001 tests pass and no regressions in existing test suite

---

## Phase 3: Completion Event + Termination Cascade (FR-1, FR-2, FR-5, FR-6)

**Goal:** Add `completion_event` parameter to `_run_subprocess`. When the event fires, initiate a graceful escalating termination cascade: wait for voluntary exit → SIGTERM → wait → SIGKILL. Debug logging on forced termination. Residual buffer content discarded after termination.

**Independent test criteria:** Completion event triggers termination cascade. Process that exits voluntarily is not force-killed. SIGTERM-resistant process is force-killed. Without completion event, behavior is identical to current. Timeout interaction is correct.

- [x] T003 Write TDD tests for completion event and termination cascade in `tests/unit/test_subprocess_completion.py`
  - Completion event set during execution → process terminated, result returned
  - Process exits voluntarily within 5s after completion event → no forced termination
  - Process ignores SIGTERM → force-killed after second 5s wait
  - Completion event not provided → current behavior (wait for stdout EOF)
  - Completion event + timeout: timeout fires first → timeout behavior wins
  - Completion event + timeout: completion fires first → timeout cancelled effectively
  - Response data collected before completion event is preserved in result
  - Debug log emitted when process is terminated (not when it exits voluntarily)
  - Uses real subprocesses (hanging, SIGTERM-resistant, clean-exit scripts)

- [x] T004 Implement completion event support and termination cascade in `src/fdsx/providers/base.py`
  - Add `completion_event: threading.Event | None = None` parameter to `_run_subprocess`
  - Main thread enters wait loop: check `completion_event` and `stdout_thread.is_alive()`
  - On completion event: run termination cascade (wait 5s → SIGTERM → wait 5s → SIGKILL)
  - Track `killed_by_completion` to avoid timeout error message
  - After cascade: join stdout/stderr threads with short timeout, return result
  - Debug logging via existing `logger` when forced termination occurs
  - Verify: T003 tests pass and no regressions

---

## Phase 4: Wire Claude Provider (FR-3)

**Goal:** Claude provider creates a `threading.Event`, sets it when the `result` NDJSON event is parsed, and passes it to `_run_subprocess`. This completes the end-to-end fix.

**Independent test criteria:** `_make_stream_callback` sets the event on `result` event. Event is set exactly once. Non-result events do not set it. `execute()` creates Event only when `output_callback` is provided.

- [x] T005 Write TDD tests for Claude provider completion signal in `tests/unit/test_subprocess_completion.py`
  - `_make_stream_callback` with completion_event: event is set when `result` event parsed
  - Event is set exactly once (multiple result events don't cause issues)
  - Event is NOT set for non-result events (content_block_delta, etc.)
  - `ClaudeProvider.execute` with `output_callback`: creates Event and passes to `_run_subprocess`
  - `ClaudeProvider.execute` without `output_callback`: no Event passed (no stream mode)

- [x] T006 Wire completion event in Claude provider in `src/fdsx/providers/claude.py`
  - `_make_stream_callback` gains `completion_event: threading.Event` parameter
  - In `stream_callback`, after `_EVENT_RESULT` handling: `completion_event.set()`
  - `execute()`: when `output_callback` is provided, create `threading.Event()`, pass to both `_make_stream_callback` and `_run_subprocess`
  - Verify: T005 tests pass and no regressions

---

## Phase 5: Integration Tests + Regression Verification (FR-4)

**Goal:** End-to-end validation with real subprocesses. Verify non-Claude providers are unaffected.

**Independent test criteria:** Simulated hanging provider completes within ~15s. Clean-exit provider has no extra latency. No-signal provider uses current behavior. All existing provider tests pass unchanged.

- [ ] T007 Write integration tests for completion signal with real subprocesses in `tests/integration/test_subprocess_completion.py`
  - Simulated hanging provider: subprocess emits NDJSON with `result` event then hangs → step completes within ~15s
  - Clean exit provider: subprocess emits `result` and exits immediately → no extra latency
  - No completion signal: subprocess without stream protocol → current behavior (waits for exit)
  - Uses `_run_subprocess` directly with crafted Python scripts

- [ ] T008 Verify full test suite passes with no regressions by running `pytest tests/ -v`
  - All existing provider tests (system, opencode, codex) pass unchanged
  - No modifications to existing test files

---

## Dependencies

```
T001 → T002 (tests before implementation)
T002 → T003 (refactor must be complete before adding completion event)
T003 → T004 (tests before implementation)
T004 → T005 (completion event must exist before Claude wiring)
T005 → T006 (tests before implementation)
T006 → T007 (Claude wiring must be complete for integration tests)
T007 → T008 (integration tests before final regression check)
```

All tasks are sequential — this is a single-developer, single-file-chain fix.

---

## Parallel Execution Opportunities

Limited parallelism due to the sequential nature of this fix:
- T001 and T003 could theoretically be written in parallel (both are test files), but T003 depends on the refactored API from T002
- T005 tests could be written while T004 is being implemented (different files), but T005 tests need the `completion_event` parameter from T004

**Recommendation:** Execute sequentially as planned. The fix is small and focused enough that parallelism overhead would exceed benefit.

---

## Implementation Strategy

- **MVP:** Phase 2 + Phase 3 (T001-T004) — subprocess can be terminated via completion event
- **Complete fix:** Add Phase 4 (T005-T006) — Claude provider wired up, fix is operational
- **Validated:** Add Phase 5 (T007-T008) — integration tests confirm end-to-end behavior

---

## Summary

| Metric | Value |
|---|---|
| Total tasks | 8 |
| Phase 2 (Foundational) | 2 tasks |
| Phase 3 (Completion Event) | 2 tasks |
| Phase 4 (Claude Wiring) | 2 tasks |
| Phase 5 (Integration) | 2 tasks |
| Files created | `tests/unit/test_subprocess_completion.py`, `tests/integration/test_subprocess_completion.py` |
| Files modified | `src/fdsx/providers/base.py`, `src/fdsx/providers/claude.py` |

---

## Suggested takt Usage

```bash
# Phase 2: Foundational refactor
takt run coder "Implement Phase 2 (T001-T002): Refactor _run_subprocess stdout reading into a daemon thread with TDD tests. See specs/20260323120000-fix-subprocess-hang/tasks.md"

# Phase 3: Completion event + termination cascade
takt run coder "Implement Phase 3 (T003-T004): Add completion_event parameter and termination cascade to _run_subprocess with TDD tests. See specs/20260323120000-fix-subprocess-hang/tasks.md"

# Phase 4: Wire Claude provider
takt run coder "Implement Phase 4 (T005-T006): Wire completion event into Claude provider _make_stream_callback and execute() with TDD tests. See specs/20260323120000-fix-subprocess-hang/tasks.md"

# Phase 5: Integration tests + regression
takt run coder "Implement Phase 5 (T007-T008): Write integration tests for completion signal and verify full test suite passes. See specs/20260323120000-fix-subprocess-hang/tasks.md"
```

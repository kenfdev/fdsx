# Tasks: Provider Hang Protection

**Spec**: [spec.md](spec.md)
**Plan**: [plan.md](plan.md)

## Phase 1: Setup

_No setup tasks needed — all changes extend existing files. No new dependencies._

## Phase 2: Foundational — Inactivity Timeout in `_run_subprocess` (FR-2, FR-4)

The core inactivity watchdog mechanism in `base.py`. All providers benefit automatically once this is in place.

**Goal**: Any provider subprocess that goes silent for longer than the configured inactivity threshold is killed with a clear error.

**Independent test criteria**: Run `python -m pytest tests/integration/test_inactivity_timeout.py -v` — all 6 tests pass.

- [x] T001 Write integration tests for inactivity timeout in `tests/integration/test_inactivity_timeout.py`
  - `test_process_killed_after_inactivity_period` — process outputs once then goes silent; killed after threshold with exit_code=124 and "inactivity timeout" in stderr
  - `test_active_process_not_killed` — process outputs continuously beyond threshold; completes normally
  - `test_startup_hang_killed` — process never produces any output; killed after threshold
  - `test_inactivity_timeout_disabled_with_zero` — inactivity_timeout=0; process is NOT killed by inactivity
  - `test_stderr_resets_inactivity_timer` — process outputs on stderr only; timer resets, not killed
  - `test_inactivity_timeout_error_distinguishable_from_explicit_timeout` — different stderr message text for inactivity vs explicit timeout
  - Use short thresholds (2-3s) and follow existing `test_subprocess_completion.py` pattern (real subprocess via `sys.executable`)

- [x] T002 Implement inactivity watchdog in `src/fdsx/providers/base.py`
  - Add `DEFAULT_INACTIVITY_TIMEOUT = 300` constant
  - Add `inactivity_timeout: int | None = None` parameter to `_run_subprocess`
  - Add shared `_last_activity` timestamp protected by `threading.Lock`, initialized to `time.monotonic()` at process launch
  - Modify `_read_stdout` to update `_last_activity` on each line
  - Modify `_read_stderr` to update `_last_activity` on each line
  - Add `_inactivity_watchdog` daemon thread: checks every 1s if idle time exceeds threshold, checks `_suppressed` event before acting, runs termination cascade (SIGTERM → 5s → SIGKILL), sets `killed_by_inactivity` flag
  - In result path: if `killed_by_inactivity`, return `ProviderResult(exit_code=124, stdout="", stderr="Process killed due to inactivity timeout after {N} seconds (no output received)")`
  - When `completion_event` fires: set `_suppressed` event to prevent inactivity watchdog from killing

- [x] T003 Verify Phase 2 tests pass: `python -m pytest tests/integration/test_inactivity_timeout.py -v`

## Phase 3: Codex completion_event (FR-1)

Add structured-stream completion detection to the Codex provider, matching the existing Claude provider pattern.

**Goal**: Codex subprocess that hangs after emitting a terminal streaming event is killed by the completion_event termination cascade.

**Independent test criteria**: Run `python -m pytest tests/unit/test_codex_stream_parser.py tests/integration/test_codex_completion.py -v` — all new tests pass.

- [x] T004 Write tests for Codex completion_event in `tests/unit/test_codex_stream_parser.py` (extend) and `tests/integration/test_codex_completion.py` (new)
  - Unit tests (extend `tests/unit/test_codex_stream_parser.py`):
    - `test_completion_event_set_on_agent_message_completed` — `item.completed` + `agent_message` → event set
    - `test_completion_event_set_on_turn_failed` — `turn.failed` → event set
    - `test_completion_event_set_on_error` — `error` → event set
    - `test_completion_event_not_set_on_non_terminal` — `item.started` or `reasoning` completed → event NOT set
  - Integration test (new `tests/integration/test_codex_completion.py`):
    - `test_codex_hanging_process_killed_by_completion_event` — subprocess emits JSONL with terminal event then hangs; verify termination cascade kills it within ~15s and output preserved

- [x] T005 Implement Codex completion_event in `src/fdsx/providers/codex.py`
  - Add `completion_event: threading.Event | None = None` parameter to `_make_stream_callback`
  - In `stream_callback`: set `completion_event` when event is terminal (`item.completed` + `agent_message`, `turn.failed`, `error`)
  - In `execute()` when `output_callback is not None`: create `completion_event = threading.Event()`, pass to both `_make_stream_callback` and `_run_subprocess`

- [x] T006 Verify Phase 3 tests pass: `python -m pytest tests/unit/test_codex_stream_parser.py tests/integration/test_codex_completion.py -v`

## Phase 4: Per-Provider Inactivity Timeout Configuration (FR-3, FR-5, FR-6)

Expose `inactivity_timeout` in each provider's options model and wire it through to `_run_subprocess`.

**Goal**: Users can configure or disable inactivity timeout per-provider via YAML options.

**Independent test criteria**: Run `python -m pytest tests/unit/test_provider_options.py -v` — all new option tests pass.

- [ ] T007 Write tests for provider inactivity_timeout options in `tests/unit/test_provider_options.py` (extend)
  - `test_codex_options_inactivity_timeout_default` — `CodexOptions()` has `inactivity_timeout=None`
  - `test_codex_options_inactivity_timeout_custom` — `CodexOptions(inactivity_timeout=600)` accepted
  - `test_codex_options_inactivity_timeout_zero_disables` — `CodexOptions(inactivity_timeout=0)` accepted
  - Same three tests for `ClaudeOptions` and `OpenCodeOptions`
  - `test_provider_passes_inactivity_timeout_to_run_subprocess` — mock `_run_subprocess`, verify each provider passes the resolved `inactivity_timeout`

- [ ] T008 Add `inactivity_timeout` to provider options models in `src/fdsx/providers/codex.py`, `src/fdsx/providers/claude.py`, `src/fdsx/providers/opencode.py`
  - Add `inactivity_timeout: int | None = None` field to each Options model
  - In each `execute()`: resolve effective timeout (`options.inactivity_timeout if not None else DEFAULT_INACTIVITY_TIMEOUT`; 0 means disabled → pass 0)
  - Pass `inactivity_timeout=effective_value` to `_run_subprocess`
  - Import `DEFAULT_INACTIVITY_TIMEOUT` from `base`

- [ ] T009 Verify Phase 4 tests pass: `python -m pytest tests/unit/test_provider_options.py -v`

## Phase 5: Integration Verification

End-to-end scenario tests validating the interaction between completion_event and inactivity timeout, plus full regression.

**Goal**: Both protection mechanisms work correctly together without interference.

**Independent test criteria**: `python -m pytest tests/ -v` — full suite passes with zero regressions.

- [ ] T010 Write end-to-end scenario tests in `tests/integration/test_inactivity_timeout.py` (extend)
  - `test_completion_event_suppresses_inactivity_timeout` — process with both mechanisms: completion_event fires, inactivity timeout does NOT produce error
  - `test_inactivity_timeout_with_explicit_timeout` — both inactivity and explicit timeout set, inactivity fires first; verify inactivity error (not explicit timeout error)

- [ ] T011 Full test suite regression: `python -m pytest tests/ -v` — all existing and new tests pass

## Dependencies

```
T001 → T002 → T003 (Phase 2: inactivity timeout core)
T004 → T005 → T006 (Phase 3: Codex completion_event)
T007 → T008 → T009 (Phase 4: per-provider config)
T010 → T011          (Phase 5: integration verification)

Phase 2 must complete before Phase 5 (T010 depends on inactivity watchdog)
Phase 3 must complete before Phase 5 (T010 depends on completion_event)
Phase 4 must complete before Phase 5 (full regression needs all features)

Phases 2, 3, 4 can run in parallel (independent code areas)
```

## Parallel Execution Opportunities

- **T001 and T004 and T007** can run in parallel (all are test-writing tasks in different files)
- **T002 and T005** can run in parallel after their test tasks complete (different source files, though T005 depends on `_run_subprocess` completion_event support which already exists)
- **T008** can run in parallel with T002/T005 (different source files)

## Implementation Strategy

1. **MVP**: Phase 2 (T001-T003) — inactivity timeout protects all providers immediately
2. **Incremental**: Phase 3 (T004-T006) — adds structured completion detection for Codex
3. **Configuration**: Phase 4 (T007-T009) — exposes per-provider tuning
4. **Validation**: Phase 5 (T010-T011) — confirms all mechanisms work together

## Suggested takt Usage

```bash
# Phase 2: Inactivity timeout core
takt run coder "Phase 2: Write inactivity timeout integration tests (T001) per plan.md Step 1.1"
takt run coder "Phase 2: Implement inactivity watchdog in base.py (T002) per plan.md Step 1.2"
takt run coder "Phase 2: Run and verify inactivity timeout tests pass (T003) per plan.md Step 1.3"

# Phase 3: Codex completion_event
takt run coder "Phase 3: Write Codex completion_event tests (T004) per plan.md Step 2.1"
takt run coder "Phase 3: Implement Codex completion_event (T005) per plan.md Step 2.2"
takt run coder "Phase 3: Run and verify Codex completion_event tests pass (T006) per plan.md Step 2.3"

# Phase 4: Per-provider config
takt run coder "Phase 4: Write provider options inactivity_timeout tests (T007) per plan.md Step 3.1"
takt run coder "Phase 4: Add inactivity_timeout to provider options models (T008) per plan.md Step 3.2"
takt run coder "Phase 4: Run and verify provider options tests pass (T009) per plan.md Step 3.3"

# Phase 5: Integration verification
takt run coder "Phase 5: Write end-to-end scenario tests for interaction between mechanisms (T010) per plan.md Step 4.1"
takt run coder "Phase 5: Run full test suite regression (T011) per plan.md Step 4.2"
```

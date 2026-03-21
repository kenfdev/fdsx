# Tasks: UUIDv7 Run IDs & Realtime Provider Streaming

**Feature:** UUIDv7 Run IDs & Realtime Provider Streaming
**Spec:** `specs/20260321133916-uuidv7-realtime-streaming/spec.md`
**Plan:** `specs/20260321133916-uuidv7-realtime-streaming/impl-plan.md`
**Generated:** 2026-03-21

---

## Phase 1: UUIDv7 Run IDs (FR-1.1–FR-1.6)

**Goal:** Replace UUIDv4 with UUIDv7 for time-sortable run IDs.
**Test criteria:** Generated IDs are valid UUIDv7, sort chronologically, and pass existing `THREAD_ID_PATTERN` validation. All existing tests pass without modification.

- [x] T001 Add `uuid-utils` dependency to `pyproject.toml`
- [x] T002 Write TDD tests for UUIDv7 generation in `tests/unit/test_uuidv7.py` — valid UUID format, version nibble = 7, sequential sort correctness, THREAD_ID_PATTERN acceptance
- [x] T003 Replace `uuid.uuid4()` with `uuid_utils.uuid7()` at all 4 generation points in `src/fdsx/cli/main.py` and `src/fdsx/core/engine.py`
- [x] T004 Verify full test suite passes with UUIDv7 IDs — no regressions in CheckpointManager, RunRecorder, resume_flow

---

## Phase 2: Claude Provider JSON Streaming (FR-2.1–FR-2.9)

**Goal:** Enable realtime token-level streaming from Claude CLI via `stream-json` format.
**Test criteria:** Stream parser correctly extracts text_delta, thinking_delta, tool_use events. ProviderResult.stdout matches expected final text from `result` event. Malformed JSON lines are skipped gracefully.

- [x] T005 Record real Claude stream-json fixture — run `claude -p "Say hello and list 3 items" --output-format stream-json --verbose --include-partial-messages` and save to `tests/fixtures/claude_stream.ndjson`
- [x] T006 Write TDD tests for Claude stream parser in `tests/unit/test_claude_stream_parser.py` — text_delta, thinking_delta, tool_use content_block_start, result event stdout extraction, malformed JSON skip, missing result fallback, non-streaming event ignore
- [x] T007 Implement Claude stream line parser `_make_stream_callback()` in `src/fdsx/providers/claude.py` — closure that parses each JSON line and dispatches to output_callback
- [x] T008 Wire streaming flags in Claude provider `execute()` in `src/fdsx/providers/claude.py` — add `--output-format stream-json --verbose --include-partial-messages` when output_callback provided, return ProviderResult with stdout from result event
- [x] T009 Write integration test for Claude streaming end-to-end in `tests/integration/test_claude_streaming.py` — mocked subprocess replaying fixture, verify callbacks and ProviderResult.stdout

---

## Phase 3: Codex Provider JSONL Streaming (FR-4.1–FR-4.10)

**Goal:** Enable event-level streaming from Codex CLI via `--json` JSONL format.
**Test criteria:** Stream parser correctly extracts agent_message, reasoning, command_execution, file_change, mcp_tool_call events. ProviderResult.stdout matches concatenated agent_message texts. Malformed JSON and turn.failed handled gracefully.

- [x] T010 Record real Codex JSONL fixture — run `codex exec --json "Say hello"` and save to `tests/fixtures/codex_stream.jsonl`
- [x] T011 Write TDD tests for Codex stream parser in `tests/unit/test_codex_stream_parser.py` — agent_message completed, reasoning completed, command_execution started, file_change started, mcp_tool_call started, turn.failed warning, malformed JSON skip, multiple agent_message concatenation, partial collection on crash
- [x] T012 Implement Codex stream line parser `_make_stream_callback()` in `src/fdsx/providers/codex.py`
- [x] T013 Wire streaming flags in Codex provider `execute()` in `src/fdsx/providers/codex.py` — add `--json` when output_callback provided, reconstruct stdout from accumulated agent_message texts
- [x] T014 Write integration test for Codex streaming end-to-end in `tests/integration/test_codex_streaming.py` — mocked subprocess replaying fixture

---

## Phase 4: StreamLogger Quiet Mode (FR-5.1–FR-5.4)

**Goal:** Add `--quiet` flag that suppresses stderr streaming output while keeping log file writes and completion summary.
**Test criteria:** `quiet=True` suppresses stderr print but log file content is identical to non-quiet mode. CLI `--quiet` flag wires through engine/compiler to StreamLogger.

- [x] T015 Write TDD tests for StreamLogger quiet mode in `tests/unit/test_stream_logger.py` — quiet=False default behavior, quiet=True no stderr print, quiet=True log file content matches
- [x] T016 Add `quiet: bool = False` parameter to StreamLogger `__init__` in `src/fdsx/logging/stream_logger.py` — guard `print()` calls with `if not self.quiet`
- [x] T017 Add `--quiet` flag to CLI `run` command in `src/fdsx/cli/main.py` — pass quiet value through to engine
- [x] T018 Wire quiet flag through compiler to StreamLogger — accept `quiet` in `src/fdsx/core/compiler.py` `compile_flow()` and `src/fdsx/core/engine.py` `run_flow()`, pass to StreamLogger construction
- [x] T019 Write integration test for quiet mode end-to-end in `tests/integration/test_quiet_mode.py` — `--quiet` suppresses stderr, log files written, completion summary still prints

---

## Phase 5: Verification & Polish

**Goal:** Ensure no regressions across the full test suite and verify with real providers.

- [ ] T020 Run full test suite, fix any regressions
- [ ] T021 Verify existing integration tests still pass — linear, choice, parallel, checkpoint, batch flows
- [ ] T022 Manual verification with real providers (if available) — Claude streaming, Codex streaming, quiet mode

---

## Dependencies

```
Phase 1 (UUIDv7) ──────────────────────────────────────┐
                                                        ├── Phase 5 (Verification)
Phase 2 (Claude Streaming) ─────────────────────────────┤
                                                        │
Phase 3 (Codex Streaming) ──────────────────────────────┤
                                                        │
Phase 4 (Quiet Mode) ──────────────────────────────────-┘
```

- **Phase 1** is independent — can start immediately
- **Phases 2, 3, 4** are independent of each other — can be worked in parallel
- **Phase 5** depends on all previous phases completing

Within each phase, tasks are sequential (TDD: tests → implementation → integration test).

---

## Parallel Execution Opportunities

- **Cross-phase**: Phases 1–4 can be executed in parallel (different files, no dependencies)
- **Within Phase 2**: T005 (fixture recording) is a prerequisite for T006–T009
- **Within Phase 3**: T010 (fixture recording) is a prerequisite for T011–T014
- **Within Phase 4**: T015 (tests) → T016–T018 (implementation) → T019 (integration)

---

## Implementation Strategy

1. **MVP**: Phase 1 (UUIDv7) — smallest, self-contained, immediate user value
2. **High-value next**: Phase 2 (Claude streaming) — most impactful streaming improvement (token-level)
3. **Incremental**: Phase 3 (Codex streaming) — event-level streaming, lower impact but completes provider coverage
4. **Polish**: Phase 4 (Quiet mode) — UX refinement for CI/scripted usage
5. **Verify**: Phase 5 — full regression sweep

---

## Suggested takt Usage

```bash
# Phase 1: UUIDv7 Run IDs
takt run code "Phase 1: Add uuid-utils dependency, write UUIDv7 TDD tests, replace uuid4 with uuid7 at all generation points, verify no regressions"

# Phase 2: Claude Provider JSON Streaming
takt run code "Phase 2: Record Claude stream-json fixture, write TDD tests for stream parser, implement _make_stream_callback and wire streaming flags in Claude provider, write integration test"

# Phase 3: Codex Provider JSONL Streaming
takt run code "Phase 3: Record Codex JSONL fixture, write TDD tests for Codex stream parser, implement _make_stream_callback and wire --json flag in Codex provider, write integration test"

# Phase 4: StreamLogger Quiet Mode
takt run code "Phase 4: Write TDD tests for StreamLogger quiet mode, add quiet param to StreamLogger, add --quiet CLI flag, wire quiet through compiler/engine, write integration test"

# Phase 5: Verification & Polish
takt run code "Phase 5: Run full test suite, verify all existing integration tests pass, manual verification with real providers"
```

---

## Summary

| Metric | Value |
|---|---|
| Total tasks | 22 |
| Phase 1 (UUIDv7) | 4 tasks |
| Phase 2 (Claude Streaming) | 5 tasks |
| Phase 3 (Codex Streaming) | 5 tasks |
| Phase 4 (Quiet Mode) | 5 tasks |
| Phase 5 (Verification) | 3 tasks |
| Parallel phases | Phases 1–4 (independent) |
| MVP scope | Phase 1 only |
| Format validation | All 22 tasks follow checklist format (checkbox, ID, description, file paths) |

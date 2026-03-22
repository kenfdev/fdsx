# Tasks: Fix "Argument list too long" for CLI Providers

**Feature**: Fix "Argument list too long" for CLI Providers
**Spec**: [spec.md](./spec.md)
**Plan**: [plan.md](./plan.md)
**Created**: 2026-03-21

---

## Phase 1: Setup

- [x] T001 Create test file scaffold with imports, constants, and shared fixtures in `tests/unit/test_provider_stdin_fallback.py`

## Phase 2: Tests (TDD)

> Write all unit tests before implementation. Tests should fail until Phase 3.

- [x] T002 [P] Write Claude provider stdin fallback tests (T01-T03, T10 from plan) in `tests/unit/test_provider_stdin_fallback.py`
  - T01: prompt < threshold → prompt in args, stdin_data=None
  - T02: prompt >= threshold → prompt NOT in args, stdin_data=prompt
  - T03: prompt >= threshold + flags → flags in args, prompt NOT in args, stdin_data=prompt
  - T10: prompt exactly at threshold → uses stdin (boundary case)

- [x] T003 [P] Write Codex provider stdin fallback tests (T04-T06 from plan) in `tests/unit/test_provider_stdin_fallback.py`
  - T04: prompt < threshold → prompt in args, stdin_data=None
  - T05: prompt >= threshold → prompt NOT in args, stdin_data=prompt
  - T06: prompt >= threshold + flags → flags in args, prompt NOT in args, stdin_data=prompt

- [x] T004 [P] Write OpenCode provider stdin fallback tests (T07-T09 from plan) in `tests/unit/test_provider_stdin_fallback.py`
  - T07: prompt < threshold → prompt in args, stdin_data=None
  - T08: prompt >= threshold → prompt NOT in args, stdin_data=prompt
  - T09: prompt >= threshold + model flag → `-m model` in args, prompt NOT in args, stdin_data=prompt

## Phase 3: Implementation

> Modify each provider's `execute()` method to detect oversized prompts and pipe via stdin.

- [x] T005 [P] Implement stdin fallback in ClaudeProvider.execute() in `src/fdsx/providers/claude.py`
  - Import `ARG_MAX_STDIN_THRESHOLD` from `base`
  - Check `len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD`
  - If true: build args as `["claude", "-p"]` + flags, pass `stdin_data=prompt`
  - If false: existing behavior (prompt in args)
  - Emit debug log when stdin fallback is used

- [x] T006 [P] Implement stdin fallback in CodexProvider.execute() in `src/fdsx/providers/codex.py`
  - Import `ARG_MAX_STDIN_THRESHOLD` from `base`
  - Check `len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD`
  - If true: build args as `["codex", "exec"]` + flags (no prompt), pass `stdin_data=prompt`
  - If false: existing behavior (prompt appended to args)
  - Emit debug log when stdin fallback is used

- [x] T007 [P] Implement stdin fallback in OpenCodeProvider.execute() in `src/fdsx/providers/opencode.py`
  - Import `ARG_MAX_STDIN_THRESHOLD` from `base`
  - Check `len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD`
  - If true: build args as `["opencode", "run"]` + flags (no prompt), pass `stdin_data=prompt`
  - If false: existing behavior (prompt appended to args)
  - Emit debug log when stdin fallback is used

## Phase 4: Verification

- [x] T008 Run `pytest tests/unit/test_provider_stdin_fallback.py` — all new tests pass
- [x] T009 Run `pytest tests/` — no regressions in existing tests

---

## Dependencies

```
T001 → T002, T003, T004 (scaffold before tests)
T002 → T005 (Claude tests before Claude impl)
T003 → T006 (Codex tests before Codex impl)
T004 → T007 (OpenCode tests before OpenCode impl)
T005, T006, T007 → T008 (all impl before verification)
T008 → T009 (new tests pass before full regression)
```

## Parallel Execution

| Phase | Parallelizable tasks |
|---|---|
| Phase 2 | T002, T003, T004 (independent test groups) |
| Phase 3 | T005, T006, T007 (independent provider files) |

## Implementation Strategy

- **MVP**: Phase 1-3 with Claude provider only (T001 → T002 → T005) — the most commonly used provider
- **Full scope**: All 3 providers (9 tasks total)
- **Incremental delivery**: Each provider can be implemented and verified independently

## Summary

| Metric | Value |
|---|---|
| Total tasks | 9 |
| Test tasks | 3 (grouped by provider) |
| Implementation tasks | 3 |
| Setup/Verification tasks | 3 |
| Parallel opportunities | Phase 2 (3 tasks), Phase 3 (3 tasks) |

## Suggested takt usage

```bash
# Phase 1: Setup
takt run coder "Create test file scaffold with imports, constants, and shared fixtures in tests/unit/test_provider_stdin_fallback.py"

# Phase 2: Tests (TDD) — run in parallel
takt run coder "Write Claude provider stdin fallback tests (small prompt, large prompt, large prompt with flags, boundary case) in tests/unit/test_provider_stdin_fallback.py"
takt run coder "Write Codex provider stdin fallback tests (small prompt, large prompt, large prompt with flags) in tests/unit/test_provider_stdin_fallback.py"
takt run coder "Write OpenCode provider stdin fallback tests (small prompt, large prompt, large prompt with model flag) in tests/unit/test_provider_stdin_fallback.py"

# Phase 3: Implementation — run in parallel
takt run coder "Implement stdin fallback in ClaudeProvider.execute() in src/fdsx/providers/claude.py per spec"
takt run coder "Implement stdin fallback in CodexProvider.execute() in src/fdsx/providers/codex.py per spec"
takt run coder "Implement stdin fallback in OpenCodeProvider.execute() in src/fdsx/providers/opencode.py per spec"

# Phase 4: Verification
takt run coder "Run pytest tests/unit/test_provider_stdin_fallback.py and verify all tests pass"
takt run coder "Run pytest tests/ and verify no regressions"
```

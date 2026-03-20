# Tasks: Provider Robustness — ARG_MAX Fix & Permission Configuration

**Feature**: Provider Robustness
**Spec**: [spec.md](./spec.md)
**Plan**: [impl-plan.md](./impl-plan.md)
**Branch**: `feat/phase-1-config-system-task-model`
**Total Tasks**: 19

---

## Phase 1: ARG_MAX Stdin Fallback

**Goal**: Fix large command overflow in system provider by automatically piping commands >= 128KB via stdin to `sh`.

- [x] T001 Write unit tests for `_run_subprocess` stdin fallback in `tests/unit/test_subprocess_stdin.py` — test small command uses `sh -c`, large command uses stdin piping, identical output/exit codes, shell features preserved, boundary at 131,072 bytes
- [x] T002 Implement stdin fallback in `_run_subprocess()` shell=True branch in `src/fdsx/providers/base.py` — check `len(args[0].encode('utf-8')) >= 131072`, use `cmd = ['sh']` with `stdin_data = args[0]` when exceeded, add debug log
- [x] T003 Write integration test for large command in workflow in `tests/integration/test_large_command.py` — test system provider task with command exceeding 128KB via variable interpolation completes successfully

---

## Phase 2: Provider Options Models

**Goal**: Create typed Pydantic models for each provider's configuration options with `to_cli_flags()` methods.

- [x] T004 Write unit tests for `ClaudeOptions` model and `to_cli_flags()` in `tests/unit/test_provider_options.py` — test defaults, valid/invalid enum values, flag output for permission_mode, dangerously_skip_permissions, allowed_tools, disallowed_tools, empty flags, extra fields rejected
- [x] T005 Implement `ClaudeOptions(BaseModel)` in `src/fdsx/providers/claude.py` — fields: `permission_mode` (Literal), `dangerously_skip_permissions` (bool), `allowed_tools` (list[str]), `disallowed_tools` (list[str]), `to_cli_flags()`, `extra = "forbid"`
- [x] T006 [P] Write unit tests and implement `CodexOptions(BaseModel)` in `tests/unit/test_provider_options.py` and `src/fdsx/providers/codex.py` — fields: `sandbox` (Literal), `approval_policy` (Literal), `full_auto` (bool), `dangerously_bypass_approvals_and_sandbox` (bool), `to_cli_flags()`, `extra = "forbid"`
- [x] T007 [P] Write unit tests and implement `OpenCodeOptions(BaseModel)` in `tests/unit/test_provider_options.py` and `src/fdsx/providers/opencode.py` — empty options model with `to_cli_flags()` returning `[]`, `extra = "forbid"`

---

## Phase 3: Config System Extension

**Goal**: Add `providers` section to `FdsxConfig` with deep merge support.

- [ ] T008 Write unit tests for `_deep_merge()` utility in `tests/unit/test_config.py` — test flat dict override, nested recursive merge, scalar-to-dict override, empty override preserves base, providers merge across levels
- [ ] T009 Implement `_deep_merge(base, override)` and update `load_config()` in `src/fdsx/core/config.py` — replace shallow dict merge with recursive deep merge
- [ ] T010 Write unit tests for `FdsxConfig` with `providers` section in `tests/unit/test_config.py` — test valid provider options parsed, invalid enum rejected at load, extra fields rejected, deep merge across global/project, backward compatibility without providers
- [ ] T011 Implement `ProviderConfigs(BaseModel)` and add `providers` field to `FdsxConfig` in `src/fdsx/core/config.py` — fields: `claude: ClaudeOptions | None`, `codex: CodexOptions | None`, `opencode: OpenCodeOptions | None`, `extra = "forbid"`

---

## Phase 4: Flow Model Extension

**Goal**: Add provider options to workflow YAML schema (`Flow`, `TaskState`, `Branch`).

- [ ] T012 Write unit tests for Flow model with providers field in `tests/unit/test_models.py` — test flow with/without providers, task state with provider_options, branch with provider_options, unknown provider names accepted at parse time
- [ ] T013 Implement `Flow.providers`, `TaskState.provider_options`, `Branch.provider_options` in `src/fdsx/models/flow.py` — `Flow.providers: dict[str, dict[str, Any]] | None`, `TaskState.provider_options: dict[str, Any] | None`, `Branch.provider_options: dict[str, Any] | None`

---

## Phase 5: Provider Construction & Compiler Integration

**Goal**: Wire config merging in compiler, provider construction with options, and engine config passthrough.

- [ ] T014 Write unit tests for `get_provider()` with options in `tests/unit/test_provider_options.py` — test claude/codex/opencode with and without options, system provider ignores options, unknown provider raises
- [ ] T015 Implement `get_provider(name, options=None)` and provider constructors in `src/fdsx/providers/base.py`, `src/fdsx/providers/claude.py`, `src/fdsx/providers/codex.py`, `src/fdsx/providers/opencode.py` — add `__init__(self, options)` to each provider, modify `execute()` to append `self.options.to_cli_flags()`
- [ ] T016 Write unit tests for `_merge_provider_options()` in `tests/unit/test_compiler_merge.py` — test config-only, workflow overrides config, task overrides workflow, full 4-level merge, all-None, different providers across levels
- [ ] T017 Implement `_merge_provider_options()` and update `compile_flow()` in `src/fdsx/core/compiler.py` — add merge utility, accept `config: FdsxConfig | None`, modify `_create_task_node()` and `_create_branch_executor()` to resolve and pass options to `get_provider()`
- [ ] T018 Update engine to pass config to compiler in `src/fdsx/core/engine.py` — modify `run_flow()`, `resume_flow()`, `run_batch()`, `run_tasks_dir()` to load and pass config to `compile_flow()`
- [ ] T019 Write integration tests for end-to-end workflow with provider options in `tests/integration/test_provider_options.py` — test claude with permission_mode, config + workflow merge, task-level override, unchanged workflows without options, parallel branches with mixed providers

---

## Dependencies

```
Phase 1 (T001-T003) ──── standalone, no dependencies
Phase 2 (T004-T007) ──── standalone, no dependencies
Phase 3 (T008-T011) ──── depends on Phase 2 (imports provider options models)
Phase 4 (T012-T013) ──── standalone, no dependencies
Phase 5 (T014-T019) ──── depends on Phase 2, 3, 4
```

**Parallel opportunities**:
- Phase 1 and Phase 2 can run in parallel (independent)
- Phase 4 can run in parallel with Phase 1, 2, 3 (independent)
- Within Phase 2: T006 and T007 can run in parallel with each other (after T004-T005)
- Within Phase 5: T014 and T016 can run in parallel (different test files)

---

## Implementation Strategy

1. **MVP**: Phase 1 (ARG_MAX fix) — immediately solves the crash bug, zero config changes
2. **Incremental**: Phase 2-4 build the config/model layer without touching runtime behavior
3. **Integration**: Phase 5 wires everything together and validates end-to-end
4. **Zero breaking changes**: All new fields are optional with None defaults; existing workflows unchanged

---

## Suggested takt Usage

```bash
# Phase 1: ARG_MAX Stdin Fallback
takt run coder "Phase 1: Implement ARG_MAX stdin fallback — T001-T003. Write tests first (T001), then implement stdin fallback in _run_subprocess (T002), then integration test (T003). See specs/20260320140000-provider-robustness/impl-plan.md Phase 1."

# Phase 2: Provider Options Models
takt run coder "Phase 2: Create provider options Pydantic models — T004-T007. TDD: tests first then implementation. ClaudeOptions, CodexOptions, OpenCodeOptions with to_cli_flags(). See specs/20260320140000-provider-robustness/impl-plan.md Phase 2."

# Phase 3: Config System Extension
takt run coder "Phase 3: Extend config system with providers section — T008-T011. TDD: tests first. Add _deep_merge(), ProviderConfigs, FdsxConfig.providers. See specs/20260320140000-provider-robustness/impl-plan.md Phase 3."

# Phase 4: Flow Model Extension
takt run coder "Phase 4: Add provider options to Flow/TaskState/Branch models — T012-T013. TDD: tests first. See specs/20260320140000-provider-robustness/impl-plan.md Phase 4."

# Phase 5: Provider Construction & Compiler Integration
takt run coder "Phase 5: Wire provider options through compiler and engine — T014-T019. TDD: tests first. Modify get_provider, compile_flow, engine. Integration tests. See specs/20260320140000-provider-robustness/impl-plan.md Phase 5."
```

# fdsx YAML Schema Reference

Complete field-by-field reference for fdsx workflow YAML files, derived from the Pydantic models in `src/fdsx/models/flow.py`.

## Table of Contents

- [Flow (top-level)](#flow-top-level)
- [TaskState](#taskstate)
- [ChoiceState](#choicestate)
- [ParallelState](#parallelstate)
- [PassState](#passstate)
- [WaitState](#waitstate)
- [MapState](#mapstate)
- [IteratorDef](#iteratordef)
- [IteratorTaskState](#iteratortaskstate)
- [Branch (parallel)](#branch)
- [ExtractRule](#extractrule)
- [ExtractionFallback](#extractionfallback)
- [ChoiceRule](#choicerule)
- [AggregateRule](#aggregaterule)
- [HookConfig](#hookconfig)
- [StateHookConfig](#statehookconfig)
- [RunHookConfig](#runhookconfig)
- [ProfileConfig](#profileconfig)
- [Provider Options](#provider-options)
- [Config File](#config-file)

---

## Flow (top-level)

```yaml
name: string                    # required — flow name
description: string             # required — min 1 char
start_at: string                # required — must match a key in states
states: {name: State}           # required — map of state definitions
version?: string                # optional
max_loop?: int                  # default: 10 — max loop iterations
providers?: {name: {k: v}}      # optional — workflow-level provider configs
hooks?: HookConfig              # optional — flow-level hooks (full HookConfig including workflow-scope keys)
profiles?: {name: {k: v}}       # optional — raw provider/model/extras dicts
extraction_fallback?: ExtractionFallback | false   # optional — false disables inherited fallback; omit to inherit from config
retry_escalation?: EscalationConfig | false        # optional — false disables inherited global default; omit to inherit from config
```

**State** is a discriminated union on the `type` field: `TaskState | ChoiceState | ParallelState | PassState | WaitState | MapState`.

**Profiles at workflow level** are raw YAML dicts (`{provider, model, ...extras}`), not validated `ProfileConfig` objects. Profile resolution happens pre-validation: `profile` references in tasks/branches are expanded into `provider`/`model`/`provider_options` fields before Pydantic validation runs. Workflow-level profiles override config-level profiles (full replacement per name, not deep merge).

**`extraction_fallback`** controls the global extraction fallback for the entire workflow. When set to `false`, it disables any config-level `extraction_fallback` for this workflow. When set to an `ExtractionFallback` object, it overrides the config-level fallback. When omitted (`null`/absent), the config-level `extraction_fallback` applies. See [Extraction Fallback Priority](#extraction-fallback-priority).

**`retry_escalation`** controls the retry escalation target for the entire workflow. When set to `false`, it disables any config-level `retry_escalation` for this workflow. When set to an `EscalationConfig` object (`provider` + `model`), it overrides the config-level target. When omitted (`null`/absent), the config-level `retry_escalation` applies.

**Validation:**
- `start_at` must exist in `states`
- All `next` references across all states must exist in `states`
- At least one path from `start_at` must reach termination (`end: true`)
- `task_splitter` field is rejected (removed; configure in config.yaml instead)

---

## TaskState

```yaml
type: "task"                    # literal discriminator
provider: string                # required — claude|codex|opencode|gemini|system
model?: string                  # required for LLM providers, forbidden for system
prompt_template?: string        # XOR with prompt_file; required for LLM providers
prompt_file?: string            # XOR with prompt_template; relative path
command?: string                # required for system, forbidden for LLM providers
result_path: string             # required — JSONPath for output (e.g. $.plan)
result_file?: string            # optional — top-level $.varname only (no nesting)
extract?: ExtractRule           # optional — output extraction
max_iterations?: int            # optional — >=1, max times state can be entered
retry?: int                     # default: 3
timeout_seconds?: int           # optional — per-state timeout override
provider_options?: {k: v}       # optional — per-task provider option overrides
hooks?: StateHookConfig         # optional — per-state hooks (on_state_start/on_state_end only)
next?: string                   # XOR with end — target state
end?: bool                      # XOR with next — terminate flow
```

**Profile shorthand:** Use `profile: <name>` instead of `provider`/`model`. Resolved pre-validation; XOR with explicit provider/model.

**Validation:**
- `prompt_template` and `prompt_file` are mutually exclusive
- `next` and `end` are mutually exclusive
- `result_path` and `extract.result_path` must not overlap
- `result_file` must match `$.varname` (no dots or brackets after `$.`)
- System provider: requires `command`, forbids `prompt_template`/`prompt_file`/`model`
- LLM providers: require `model` + (`prompt_template` or `prompt_file`), forbid `command`

---

## ChoiceState

```yaml
type: "choice"                  # literal discriminator
choices: [ChoiceRule]           # required — condition-transition pairs
default?: string                # optional — fallback state name
max_iterations?: int            # optional — >=1
hooks?: StateHookConfig         # optional — per-state hooks (on_state_start/on_state_end only)
```

No `next`/`end` fields — transitions are defined in `choices` and `default`.

---

## ParallelState

```yaml
type: "parallel"                # literal discriminator
branches: [Branch]              # required — parallel branch definitions
result_path: string             # required — JSONPath for results array
result_file?: string            # optional — top-level $.varname only
min_success?: int               # optional — minimum successful branches
max_iterations?: int            # optional — >=1
hooks?: StateHookConfig         # optional — per-state hooks (on_state_start/on_state_end only)
next?: string                   # XOR with end
end?: bool                      # XOR with next
```

---

## PassState

```yaml
type: "pass"                    # literal discriminator
parameters?: {k: v}             # optional — variable transformation
aggregate?: AggregateRule       # optional — parallel result aggregation
max_iterations?: int            # optional — >=1
hooks?: HookConfig              # optional — full HookConfig (on_state_start/on_state_end/on_workflow_start/on_workflow_end)
next?: string                   # XOR with end
end?: bool                      # XOR with next
```

**Note:** `PassState` is the only state type whose `hooks` field accepts the full `HookConfig` (including `on_workflow_start`/`on_workflow_end`). The engine does not invoke workflow-scope hooks at state execution time; those keys are silently ignored during state-level execution but will not cause a validation error.

---

## WaitState

```yaml
type: "wait"                    # literal discriminator
mode: "prompt"                  # only supported mode
message: string                 # required — terminal display message
choices: [string]               # required — min 1 item, user selection options
result_path: string             # required — JSONPath for selection result
notify?: NotifyConfig           # optional — webhook notification
max_iterations?: int            # optional — >=1
hooks?: StateHookConfig         # optional — per-state hooks (on_state_start/on_state_end only)
next?: string                   # XOR with end
end?: bool                      # XOR with next
```

### NotifyConfig

```yaml
notify:
  webhook:
    url: string                 # required — HTTPS (HTTP only for localhost)
    template: string            # required — message template with {variable} refs
```

---

## MapState

```yaml
type: "map"                     # literal discriminator
items_path: string              # required — JSONPath to input array
iterator: IteratorDef           # required — sub-workflow to execute for each item
result_path: string             # required — JSONPath for results array
fail_fast?: bool                # default: true — stop on first failure
max_iterations?: int            # optional — >=1, max times this state can be entered
hooks?: StateHookConfig         # optional — per-state hooks (on_state_start/on_state_end only)
next?: string                   # XOR with end — target state
end?: bool                      # XOR with next — terminate flow
```

**Variable context inside iterator:** Each iterator state receives the current array element as `{item}`. References like `{item.field}` access nested fields of the current element. Variables from preceding states in the outer flow are also available.

**Validation:**
- `next` and `end` are mutually exclusive
- `items_path` must reference a variable set by a preceding state
- Iterator states must all have `type: "task"` (no nested choice/parallel/pass/wait/map)
- Iterator state names must be unique within the iterator

---

## IteratorDef

Used inside `MapState.iterator`:

```yaml
iterator:
  states: [IteratorTaskState]   # required — min 1 item, ordered list of task states
```

**Validation:**
- All states must have `type: "task"` (non-task types are rejected)
- State names must be unique within the iterator

---

## IteratorTaskState

Used inside `IteratorDef.states`:

```yaml
type: "task"                    # literal discriminator (only "task" allowed)
name: string                    # required — state name within the iterator
provider: string                # required — claude|codex|opencode|gemini|system
model?: string                  # required for LLM providers, forbidden for system
prompt_template?: string        # XOR with prompt_file; required for LLM providers
prompt_file?: string            # XOR with prompt_template; relative path
command?: string                # required for system, forbidden for LLM providers
result_path: string             # required — JSONPath for result
result_file?: string            # optional — top-level $.varname only (no nesting)
extract?: ExtractRule           # optional — output extraction
retry?: int                     # default: 3
timeout_seconds?: int           # optional — per-state timeout override
provider_options?: {k: v}       # optional — per-task provider option overrides
```

**Differences from TaskState:** Has a required `name` field. Does not support `max_iterations`, `hooks`, `next`, or `end` (iteration order is determined by list position).

**Profile shorthand:** Use `profile: <name>` instead of `provider`/`model`. Resolved pre-validation; XOR with explicit provider/model.

**Validation:**
- `prompt_template` and `prompt_file` are mutually exclusive
- `result_path` and `extract.result_path` must not overlap
- `result_file` must match `$.varname` (no dots or brackets after `$.`)
- Same provider field validation rules as TaskState

---

## Branch

Used inside `ParallelState.branches`:

```yaml
provider: string                # required — claude|codex|opencode|gemini|system
model?: string                  # required for LLM providers
prompt_template?: string        # XOR with prompt_file
prompt_file?: string            # XOR with prompt_template
command?: string                # required for system provider
extract?: ExtractRule           # optional
retry?: int                     # default: 3
timeout_seconds?: int           # optional
provider_options?: {k: v}       # optional
```

**Profile shorthand:** Use `profile: <name>` instead of `provider`/`model`. Resolved pre-validation; XOR with explicit provider/model.

Same provider validation rules as TaskState. `extract.result_path` must not use reserved keys: `output`, `exit_code`, `error`.

---

## ExtractRule

```yaml
extract:
  strategy: [string]            # required — non-empty list of: json, regex, keyword
  pattern: string               # required — extraction pattern
  result_path: string           # required — JSONPath for extracted value
  fallback?:                    # optional — per-rule LLM classification fallback
    type: "llm_classify"
    provider: string            # required — claude|codex|opencode|gemini
    prompt: string              # required — classification prompt
```

The `fallback` field uses `LLMClassifyFallback` and supports `profile: <name>` (XOR with `provider`), resolved pre-validation. When using `profile`, the `provider` field is populated from the profile during resolution.

### Extraction Fallback Priority

When all strategies in `strategy` fail, the engine attempts fallbacks in this order:

1. **Per-rule fallback** (`extract.fallback`) — `LLMClassifyFallback` defined directly on the `ExtractRule`. Used first if present.
2. **Flow-level fallback** (`Flow.extraction_fallback`) — if set to `false`, all fallback is disabled for this workflow; if set to an `ExtractionFallback` object, it is used.
3. **Config-level fallback** (`FdsxConfig.extraction_fallback`) — applied when no flow-level override is present.
4. **No fallback** — extraction returns `null` and the state records no extracted value.

When `source_provider` is `"system"`, all LLM fallback is suppressed regardless of configuration (prevents exfiltration of local command output).

---

## ExtractionFallback

Used at **flow level** (`Flow.extraction_fallback`) and in **config files** (`FdsxConfig.extraction_fallback`). Provides a global LLM-based extraction fallback applied when a per-rule `extract.fallback` is not configured.

```yaml
extraction_fallback:
  provider?: string             # XOR with profile — claude|codex|opencode|gemini (system forbidden)
  profile?: string              # XOR with provider — resolved from profiles
  extra_instructions?: string   # optional — appended to the recovery prompt
```

**Mutual exclusion:** exactly one of `provider` or `profile` must be set. Setting both or neither raises a validation error.

**At flow level**, the value may also be the literal `false` to explicitly disable any config-level fallback for the workflow:

```yaml
extraction_fallback: false      # disables config-level extraction_fallback for this workflow
```

**Validation:**
- `provider` and `profile` are mutually exclusive (XOR) — exactly one must be provided
- `provider` must be one of the LLM providers (`claude`, `codex`, `opencode`, `gemini`); `system` is forbidden
- Uses `extra="forbid"` — unknown keys cause validation errors

---

## ChoiceRule

Used inside `ChoiceState.choices`:

```yaml
variable: string                # required — JSONPath to compare (e.g. $.status)
operator: string                # required — equals|not_equals|greater_than|less_than|contains
value: any                      # required — comparison value
next: string                    # required — target state name
```

---

## AggregateRule

Used inside `PassState.aggregate`:

```yaml
aggregate:
  source: string                # required — JSONPath to parallel results
  field: string                 # required — field name to aggregate
  strategy: string              # required — majority|all|any
  match: string                 # required — match value
  no_match: string              # required — non-match value
  result_path: string           # required — JSONPath for result
```

---

## HookConfig

Used at **flow level** (`Flow.hooks`), in **config files** (under `hooks:`), and in `PassState.hooks`. Supports all four state/workflow lifecycle events.

```yaml
hooks:
  on_state_start:               # optional — hooks run before each state execution
    - command: string           # required — shell command (min 1 char)
      on_failure: string        # default: "warn" — abort|warn
  on_state_end:                 # optional — hooks run after each state execution
    - command: string
      on_failure: string
  on_workflow_start:            # optional — hooks run once when the workflow starts (fresh runs only)
    - command: string
      on_failure: string        # note: ignored for workflow-scope hooks (always warn-only)
  on_workflow_end:              # optional — hooks run once when the workflow ends
    - command: string
      on_failure: string        # note: ignored for workflow-scope hooks (always warn-only)
```

**Legacy keys rejected:** `on_start` and `on_complete` raise a validation error. Use `on_state_start` and `on_state_end`.

**Run-scope keys rejected:** `on_run_start` and `on_run_end` raise a validation error when used in `Flow.hooks` (flow YAML). These keys are only valid in `.fdsx/config.yaml` and `~/.config/fdsx/config.yaml` under the separate `run_hooks:` key — see [RunHookConfig](#runhookconfig).

**Workflow-scope key restriction:** `on_workflow_start` and `on_workflow_end` are **only valid** at flow level, project config, and global config scope. Using them inside a state's `hooks` block raises a validation error (except in `PassState`, which uses the full `HookConfig` — see [PassState](#passstate)).

---

### State Hook Behavior (`on_state_start` / `on_state_end`)

Each command receives:

**Positional arguments:** `$1=state_name`, `$2=status`, `$3=data_path`

**Environment variables:**
- `FDSX_STATE_NAME` — current state name
- `FDSX_STATUS` — lifecycle status: `starting` (before), `completed` or `failed` (after)
- `FDSX_DATA_PATH` — path to the state data JSON file
- `FDSX_THREAD_ID` — current run thread ID
- `FDSX_FLOW_NAME` — name of the flow
- `FDSX_HOOKS` — lifecycle event name: `on_state_start` or `on_state_end`

**Failure policy:** `on_failure: abort` raises `HookAbortError` and stops the flow. `on_failure: warn` (default) logs a warning and continues.

**Hook data files:** State data is written to JSON files at `.fdsx/runs/<thread-id>/hooks/<state-name>/input.json` (before execution) and `output.json` (after execution). `FDSX_DATA_PATH` points to the relevant file.

---

### Workflow Hook Behavior (`on_workflow_start` / `on_workflow_end`)

**`on_workflow_start`** fires once per fresh workflow run, before any state executes. Skipped when resuming from a checkpoint.

**`on_workflow_end`** fires once when the workflow terminates (success, failure, or abort), including on the exception path.

Each command receives:

**Positional arguments:** none (no `$1`, `$2`, `$3`)

**Environment variables:**
- `FDSX_HOOKS` — lifecycle event name: `on_workflow_start` or `on_workflow_end`
- `FDSX_STATUS` — lifecycle status:
  - `on_workflow_start`: always `starting`
  - `on_workflow_end`: `completed`, `failed`, or `aborted`
- `FDSX_FLOW_NAME` — name of the flow
- `FDSX_THREAD_ID` — current run thread ID
- `FDSX_STATE_NAME` and `FDSX_DATA_PATH` are **not set** for workflow hooks

**Failure policy:** `on_failure` is ignored for workflow hooks. Non-zero exit codes and timeouts are always logged as warnings and never raise. Each hook has a 30-second subprocess timeout.

---

## StateHookConfig

Used by **most state types** (`TaskState`, `ChoiceState`, `ParallelState`, `WaitState`, `MapState`) for per-state hook configuration. Identical to `HookConfig` except that `on_workflow_start`, `on_workflow_end`, `on_run_start`, and `on_run_end` are explicitly forbidden.

```yaml
hooks:
  on_state_start:               # optional — hooks run before state execution
    - command: string           # required — shell command (min 1 char)
      on_failure: string        # default: "warn" — abort|warn
  on_state_end:                 # optional — hooks run after state execution
    - command: string
      on_failure: string
```

**Rejected keys:** `on_start`, `on_complete` (legacy), `on_workflow_start`, `on_workflow_end`, `on_run_start`, and `on_run_end` all raise a validation error when used inside a state's `hooks` block. Workflow-scope keys (`on_workflow_start`, `on_workflow_end`) are only valid at flow level or in config files. Run-scope keys (`on_run_start`, `on_run_end`) are only valid in config files under the `run_hooks:` key.

---

## RunHookConfig

Used by **config files only** (`FdsxConfig.run_hooks`). Fires once per CLI invocation, outside any individual workflow or state context. Cannot be used in flow YAML or state blocks.

```yaml
run_hooks:
  on_run_start:                 # optional — hooks run once at CLI invocation start
    - command: string           # required — shell command (min 1 char)
      on_failure: string        # default: "warn" — always warn-only for run hooks
  on_run_end:                   # optional — hooks run once at CLI invocation end
    - command: string
      on_failure: string        # note: on_failure is ignored; run hooks are always warn-only
```

**Scope:** `run_hooks` is a **separate top-level key** in `.fdsx/config.yaml` and `~/.config/fdsx/config.yaml`. It is distinct from `hooks:` (which contains state/workflow lifecycle events). Using `on_run_start`/`on_run_end` inside `hooks:` raises a validation error.

**`on_run_start`** fires once at the start of a `fdsx run` or `fdsx resume` CLI invocation, before any workflow or checkpoint logic executes.

**`on_run_end`** fires once when the CLI invocation exits (success, failure, or partial completion for tasks-dir runs).

Each command receives:

**Positional arguments:** none

**Environment variables:**
- `FDSX_HOOKS` — lifecycle event name: `on_run_start` or `on_run_end`
- `FDSX_STATUS` — lifecycle status:
  - `on_run_start`: always `starting`
  - `on_run_end`: `completed`, `failed`, or `partial` (tasks-dir aggregate)
- `FDSX_STATE_NAME`, `FDSX_DATA_PATH`, `FDSX_FLOW_NAME`, and `FDSX_THREAD_ID` are **not set** for run hooks

**Failure policy:** Always warn-only — `on_failure` is ignored. Non-zero exit codes and timeouts log a warning and never raise. Each hook has a 30-second subprocess timeout.

**Merging:** During global → project config deep merge, `on_run_start` and `on_run_end` lists are **concatenated** (global prepended to project), not replaced.

Uses `extra="forbid"` — unknown keys cause validation errors.

---

## ProfileConfig

```yaml
profiles:
  <name>:                       # must match: ^[a-zA-Z][a-zA-Z0-9_-]*$
    provider: string            # required — claude|codex|opencode|gemini
    model: string               # required
    # extra fields allowed (passed through as provider_options)
```

Profiles are defined at workflow level or in `.fdsx/config.yaml`. Workflow-level profiles override config-level (full replacement per name, not deep merge).

---

## Provider Options

Options set via `provider_options` on tasks/branches, or globally in config. All LLM providers share `inactivity_timeout` (default: 300s, set 0 to disable).

### Claude

```yaml
provider_options:
  permission_mode?: default|acceptEdits|bypassPermissions|dontAsk|plan|auto
  dangerously_skip_permissions?: bool   # default: false
  allowed_tools?: [string]
  disallowed_tools?: [string]
  system_prompt?: string                # mutually exclusive with append_system_prompt
  append_system_prompt?: string         # mutually exclusive with system_prompt
  inactivity_timeout?: int              # default: 300
```

**Mutual exclusion:** `system_prompt` and `append_system_prompt` cannot both be set on the same state (including after config-level merge). Setting both raises `FlowValidationError`.

### Codex

```yaml
provider_options:
  sandbox?: read-only|workspace-write|danger-full-access
  approval_policy?: untrusted|on-request|never
  full_auto?: bool                      # default: false
  dangerously_bypass_approvals_and_sandbox?: bool  # default: false
  inactivity_timeout?: int              # default: 300
```

### OpenCode

```yaml
provider_options:
  permission?: string|{k: v}           # passed via OPENCODE_CONFIG_CONTENT env var
  inactivity_timeout?: int              # default: 300
```

### Gemini

```yaml
provider_options:
  approval_mode?: default|auto_edit|yolo|plan
  yolo?: bool                           # default: false (overrides approval_mode)
  sandbox?: bool                        # default: false
  include_directories?: [string]
  extensions?: [string]
  policy?: [string]
  inactivity_timeout?: int              # default: 300
```

### Cursor

```yaml
provider_options:
  force?: bool                          # default: false — pass --force to agent CLI
  sandbox?: string                      # optional — passed as --sandbox <value>
  approve_mcps?: bool                   # default: false — pass --approve-mcps to agent CLI
  inactivity_timeout?: int              # default: 300
```

CLI binary invoked: `agent -p <prompt> --trust [--model <model>] [--force] [--sandbox <val>] [--approve-mcps]`

When `output_callback` is provided, `--output-format stream-json --stream-partial-output` flags are appended to enable streaming.

**Note:** `cursor` is registered in the provider factory (`get_provider()`) and the `CursorProvider` implementation is complete. However, `cursor` is not currently listed in `VALID_PROVIDERS` in `models/validators.py` or `_validate_provider_fields` in `models/flow.py`, so using `provider: cursor` in workflow YAML will raise a validation error at load time. It may only be used via direct programmatic invocation of `get_provider("cursor")`.

All provider option models use `extra="forbid"` — unknown keys cause validation errors.

---

## Config File

`.fdsx/config.yaml` (project-level) or `~/.config/fdsx/config.yaml` (global). Project overrides global via deep merge.

```yaml
task_splitter?:                 # absent by default; must be added to enable batch splitting
  profile?: string              # XOR with provider/model
  provider?: string             # default: claude (when task_splitter is present)
  model?: string                # default: claude-sonnet-4-6 (when task_splitter is present)
  extra_instructions?: string

workflow_selector?:
  profile?: string              # XOR with provider/model
  provider?: string             # default: claude
  model?: string                # default: claude-sonnet-4-6
  extra_instructions?: string

workflows_dir?: string          # default: .fdsx/workflows — relative, no ".."
auto_workflow?: bool            # default: false
default_tasks_dir?: string      # default: .fdsx/tasks/ — precedence: project → global → fallback

providers?:
  claude?: ClaudeOptions
  codex?: CodexOptions
  opencode?: OpenCodeOptions
  gemini?: GeminiOptions

hooks?: HookConfig              # workflow/state lifecycle hooks applied to all flows
                                # accepts: on_state_start, on_state_end, on_workflow_start, on_workflow_end
                                # does NOT accept: on_run_start, on_run_end (use run_hooks: instead)

run_hooks?:                     # run-level lifecycle hooks fired once per CLI invocation
  on_run_start?: [HookEntry]    # fires at start of fdsx run / fdsx resume
  on_run_end?: [HookEntry]      # fires at end of CLI invocation

profiles?:
  <name>: ProfileConfig

extraction_fallback?:           # absent by default — global LLM fallback when no per-rule fallback is set
  provider?: string             # XOR with profile — claude|codex|opencode|gemini (system forbidden)
  profile?: string              # XOR with provider — resolved from profiles
  extra_instructions?: string   # optional — appended to the recovery prompt

retry_escalation?:              # absent by default — global escalation target for all flows
  provider: string              # required — claude|codex|opencode|gemini (system forbidden)
  model: string                 # required — exact model string for the escalation provider
  provider_options?: {k: v}     # optional — passed to the escalation provider
```

Config uses `extra="forbid"` — unknown keys cause validation errors.

**Hook merging:** During global → project config deep merge, all six hook list keys (`on_state_start`, `on_state_end`, `on_workflow_start`, `on_workflow_end`, `on_run_start`, `on_run_end`) are **concatenated** (base + override), not replaced. This means hooks defined in global config are prepended to hooks defined in project config. Flow-level and state-level hooks are further appended at runtime in global → project → flow → state order. Run-scope hooks (`on_run_start`, `on_run_end`) only merge at global → project level; they are not present at flow or state level.

Both `workflow_selector` and `task_splitter` support `profile: <name>` (XOR with `provider`/`model`).

`profiles` defined here are merged with workflow-level profiles (workflow-level overrides config-level per name).

**`extraction_fallback`** provides a project-wide default LLM fallback invoked when extraction strategies all fail and no per-rule `extract.fallback` is configured. It is overridden per-workflow via `Flow.extraction_fallback` (which may be set to `false` to disable it entirely for that workflow). See [Extraction Fallback Priority](#extraction-fallback-priority).

**`retry_escalation`** provides a project-wide default escalation target invoked when a workflow AI task exhausts its primary-provider retries. It is overridden per-workflow via `Flow.retry_escalation` (which may be set to `false` to disable it entirely for that workflow).
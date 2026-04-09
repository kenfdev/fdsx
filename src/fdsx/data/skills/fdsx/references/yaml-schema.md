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
- [ChoiceRule](#choicerule)
- [AggregateRule](#aggregaterule)
- [HookConfig](#hookconfig)
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
hooks?: HookConfig              # optional — flow-level hooks
profiles?: {name: {k: v}}       # optional — raw provider/model/extras dicts
```

**State** is a discriminated union on the `type` field: `TaskState | ChoiceState | ParallelState | PassState | WaitState | MapState`.

**Profiles at workflow level** are raw YAML dicts (`{provider, model, ...extras}`), not validated `ProfileConfig` objects. Profile resolution happens pre-validation: `profile` references in tasks/branches are expanded into `provider`/`model`/`provider_options` fields before Pydantic validation runs. Workflow-level profiles override config-level profiles (full replacement per name, not deep merge).

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
hooks?: HookConfig              # optional — per-state hooks
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
hooks?: HookConfig              # optional
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
hooks?: HookConfig              # optional
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
hooks?: HookConfig              # optional
next?: string                   # XOR with end
end?: bool                      # XOR with next
```

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
hooks?: HookConfig              # optional
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
hooks?: HookConfig              # optional — per-state hooks
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

**Note:** Profile shorthand (`profile: <name>`) is not currently supported on iterator task states. Use explicit `provider`/`model` fields.

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
  fallback?:                    # optional — LLM classification fallback
    type: "llm_classify"
    provider: string            # required — claude|codex|opencode|gemini
    prompt: string              # required — classification prompt
```

Fallback supports `profile: <name>` (XOR with `provider`), resolved pre-validation. When using `profile`, the `provider` field is populated from the profile during resolution.

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

```yaml
hooks:
  on_start:                     # optional — hooks run before execution
    - command: string           # required — shell command (min 1 char)
      on_failure: string        # default: "warn" — abort|warn
  on_complete:                  # optional — hooks run after execution
    - command: string
      on_failure: string
```

Hooks can be set at flow level and per-state level. Each hook command receives:

**Positional arguments:** `$1=state_name`, `$2=status`, `$3=data_path`

**Environment variables:**
- `FDSX_STATE_NAME` — current state name
- `FDSX_STATUS` — lifecycle status (`starting`, `completed`, or `failed`)
- `FDSX_DATA_PATH` — path to the state data JSON file
- `FDSX_THREAD_ID` — current run thread ID
- `FDSX_FLOW_NAME` — name of the flow

**Hook data files:** Before hooks run, state data is written to JSON files at `.fdsx/runs/<thread-id>/hooks/<state-name>/input.json` (before execution) and `output.json` (after execution). The `FDSX_DATA_PATH` environment variable points to the relevant data file.

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

hooks?: HookConfig              # global hooks, concatenated with flow/state hooks

profiles?:
  <name>: ProfileConfig
```

Config uses `extra="forbid"` — unknown keys cause validation errors.
---
name: fdsx
description: >
  Expert guide for authoring, validating, and running fdsx declarative AI agent
  workflow YAML files. Use when writing fdsx workflows, editing workflow YAML,
  configuring fdsx providers (claude, cursor, codex, opencode, gemini), setting up
  profiles, adding hooks, using choice/parallel/loop/wait/pass/map/fail states,
  running fdsx CLI commands, debugging workflow validation errors, or asking
  about fdsx YAML schema. Also triggers on: "fdsx", "workflow YAML", "declarative
  agent workflow", "multi-step AI pipeline", "provider options", "checkpoint
  resume", "map state", "iterator", "extraction fallback".
---

# fdsx Workflow Authoring Guide

fdsx executes multi-step AI agent workflows defined in declarative YAML. It compiles workflow definitions into state machines, executes them by invoking LLM CLI tools (`claude`, `agent` (Cursor), `codex`, `opencode`, `gemini`) or shell commands as subprocesses, and manages checkpoint/resume across runs.

## Quick Start

Minimal workflow — two tasks in sequence:

```yaml
name: My Workflow
description: Plan then implement
start_at: plan

states:
  plan:
    type: task
    provider: claude
    model: claude-sonnet-4-6
    prompt_template: "Create a plan for: {description}"
    result_path: $.plan
    next: implement

  implement:
    type: task
    provider: claude
    model: claude-sonnet-4-6
    prompt_template: "Implement this plan: {plan}"
    result_path: $.implementation
    end: true
```

Run it: `fdsx run workflow.yaml --input description="Build a REST API"`

## YAML Schema

Read `references/yaml-schema.md` for the complete field-by-field schema reference covering all state types, provider options, profiles, hooks, and extraction rules.

## State Types

| Type | Purpose | Key Fields |
|------|---------|------------|
| `task` | Execute a provider (LLM or shell command) | `provider`, `model`, `prompt_template`, `result_path`, `result_file` |
| `choice` | Branch based on variable values | `choices` (list of rules), `default` |
| `parallel` | Execute multiple branches concurrently | `branches`, `result_path`, `result_file`, `min_success` |
| `pass` | Data transformation / aggregation | `parameters`, `aggregate` |
| `wait` | Human input via terminal prompt | `mode: prompt`, `message`, `choices`, `result_path` |
| `map` | Iterate over an array, execute sub-workflow per item | `items_path`, `iterator`, `result_path`, `fail_fast` |
| `fail` | Terminate the flow with a named error | `error`, `cause` |

States that support routing use either `next` (go to state) or `end: true` (terminate flow) — these are mutually exclusive. `choice` uses `choices`/`default` instead. `fail` supports neither `next` nor `end` (it always terminates on entry).

## Providers

| Provider | CLI Command | Requires | Options Key |
|----------|------------|----------|-------------|
| `claude` | `claude -p <prompt> --model <model>` | `model`, `prompt_template` or `prompt_file` | `effort`, `permission_mode`, `dangerously_skip_permissions`, `allowed_tools`, `disallowed_tools`, `system_prompt`, `append_system_prompt` |
| `codex` | `codex exec --model <model> <prompt>` | `model`, `prompt_template` or `prompt_file` | `reasoning_effort`, `sandbox`, `approval_policy`, `full_auto`, `dangerously_bypass_approvals_and_sandbox` |
| `opencode` | `opencode run -m <model> <prompt>` | `model`, `prompt_template` or `prompt_file` | `variant`, `permission` (passed via `OPENCODE_CONFIG_CONTENT` env var) |
| `gemini` | `gemini -p <prompt> --model <model>` | `model`, `prompt_template` or `prompt_file` | `approval_mode`, `yolo`, `sandbox`, `include_directories`, `extensions`, `policy` |
| `cursor` | `agent -p <prompt> --trust [--model <model>]` | `model`, `prompt_template` or `prompt_file` | `force`, `approve_mcps`, `sandbox` |
| `system` | `sh -c <command>` | `command` | (none) |

All LLM providers have `inactivity_timeout` (default: 300s) and a hard execution timeout (default: 1800s).

The `system` provider forbids `prompt_template`, `prompt_file`, and `model`. LLM providers forbid `command`.

## Profiles

Profiles are named provider/model bundles. Define once, reference anywhere:

```yaml
profiles:
  fast:
    provider: claude
    model: claude-haiku-4-5-20251001
  smart:
    provider: claude
    model: claude-sonnet-4-6

states:
  plan:
    type: task
    profile: smart          # resolves to provider: claude, model: claude-sonnet-4-6
    prompt_template: "..."
    result_path: $.plan
    end: true
```

`profile` and explicit `provider`/`model` are mutually exclusive (XOR). Profiles can also be defined in `.fdsx/config.yaml` and are merged (workflow-level overrides config-level).

Profile shorthand is supported on task states, parallel branches, map iterator task states, and extract fallback configurations.

## Variable Substitution

Prompts use `{variable}` syntax (single curly braces) referencing JSONPath state results:

```yaml
prompt_template: "Review this code: {implementation}"
```

Variables resolve from `$.results.<state_name>.output` or from `--input` CLI arguments. Use `result_path: $.foo` to store a state's output at `$.foo`.

Global variables automatically available in every state: `{task}` and `{source}` (injected at runtime for batch execution), and `{run_path}` (injected in all execution modes; resolves to the absolute path of the current run directory, e.g. `.fdsx/runs/<thread-id>`).

## Extraction

Extract structured data from provider output:

```yaml
extract:
  strategy: [keyword]           # tried in order: json, regex, keyword
  pattern: "APPROVED|REJECTED"
  result_path: $.decision
  fallback:                     # optional per-rule LLM classification fallback
    type: llm_classify
    provider: claude            # or use profile: <name> (XOR with provider + model)
    model: claude-sonnet-4-6
    prompt: "Classify as APPROVED or REJECTED"
```

`result_path` and `extract.result_path` must not overlap. Branch `extract.result_path` must not use reserved keys: `output`, `exit_code`, `error`.

### Global Extraction Fallback

When per-rule `fallback:` is not set, fdsx can fall back to a global extraction recovery LLM. This is configured at three levels (highest priority wins):

1. **Per-rule `fallback:`** — `LLMClassifyFallback` on the individual `extract:` block (described above).
2. **Flow-level `extraction_fallback:`** — overrides the config-level fallback for this workflow. Set to `false` to disable the inherited fallback entirely for this workflow.
3. **Config-level `extraction_fallback:`** — project-wide default in `.fdsx/config.yaml`.

```yaml
# In workflow YAML (flow level):
extraction_fallback:
  provider: claude              # or use profile: <name> (XOR with provider + model)
  model: claude-sonnet-4-6
  extra_instructions: "Always return one of: APPROVED, REJECTED"

# To disable inherited config-level fallback for this workflow:
extraction_fallback: false
```

```yaml
# Workflow that disables the inherited global fallback but keeps a per-rule override:
name: review-workflow
extraction_fallback: false        # global config-level fallback suppressed for this workflow
start_at: classify
states:
  classify:
    type: task
    provider: claude
    prompt_template: "Classify the output"
    result_path: "$.task_result"
    extract:
      strategy: [keyword]
      pattern: "APPROVED|REJECTED"
      result_path: "$.decision"
      # no fallback: — disable wins, no LLM recovery attempted
    end: true
  classify_with_recovery:
    type: task
    provider: claude
    prompt_template: "Classify the output"
    result_path: "$.task_result"
    extract:
      strategy: [keyword]
      pattern: "APPROVED|REJECTED"
      result_path: "$.decision"
      fallback:                    # per-rule fallback fires normally despite workflow disable
        provider: claude
        model: claude-sonnet-4-6
        prompt: "Classify as APPROVED or REJECTED: {output}"
    end: true
```

`ExtractionFallback` fields:
- `provider` — LLM provider (`claude`, `cursor`, `codex`, `opencode`, `gemini`; `system` is forbidden). XOR with `profile`. Must be paired with `model`.
- `model` — model string passed to the provider binary. Required when `provider` is set.
- `profile` — named profile. XOR with `provider` + `model`. Exactly one of `provider + model` or `profile` must be set.
- `extra_instructions` — optional string appended to the recovery prompt.

## CLI Commands

```
fdsx run [<workflow.yaml>] [--input KEY=VALUE] [--tasks-dir <dir>] [--thread-id <id>] [--quiet] [--auto-workflow] [--confirm-workflow] [--continue-on-error]
fdsx validate <workflow.yaml>
fdsx resume --thread-id <id> [--base-dir <path>]
fdsx list [--base-dir <path>]
fdsx add <task-file> [--split] [--force]
fdsx init [--skill]
fdsx --version
fdsx --ci | --interactive        # global flags (mutually exclusive)
```

`--auto-workflow` and `--confirm-workflow` are mutually exclusive. `--auto-workflow` skips interactive workflow confirmation; `--confirm-workflow` forces the confirmation UI.

`--continue-on-error` (tasks-dir mode only): continue processing remaining entries when an error occurs instead of stopping.

When `fdsx run` is invoked with no workflow, no `--tasks-dir`, and no `--input`, it falls back to the `default_tasks_dir` config value (default: `.fdsx/tasks/`) and runs in tasks-dir mode.

`fdsx add <task-file>` adds a task file to the batch execution queue. Use `--split` to invoke the LLM task splitter to break the file into multiple task files in `.fdsx/tasks/`. Use `--force` to clear existing tasks before writing.

`fdsx init` initializes a new fdsx project with interactive provider and template selection. Use `--skill` to install only the Claude Code skill without scaffolding `.fdsx/`.

## Hooks

Shell commands that run at lifecycle events. There are four scopes with different behaviors:

### State-scope hooks (`on_state_start`, `on_state_end`)

Run before/after individual state execution. Can be defined at flow level and per-state level. Per-state `hooks` blocks on `task`, `choice`, `parallel`, `map`, and `fail` states **only** accept `on_state_start` and `on_state_end` — using `on_workflow_start`, `on_workflow_end`, `on_wait_start`, or `on_wait_end` in those state blocks raises a validation error. **Exception:** `pass` state `hooks` blocks use the full `HookConfig` and accept all six keys (workflow-scope and wait-scope keys are silently ignored at runtime). **Wait state exception:** `wait` state `hooks` blocks use `WaitStateHookConfig`, which accepts `on_state_start`, `on_state_end`, `on_wait_start`, and `on_wait_end` — but not `on_workflow_start` or `on_workflow_end`.

```yaml
hooks:
  on_state_start:
    - command: "echo Starting"
      on_failure: warn       # or "abort"
  on_state_end:
    - command: "echo Done"
```

Each state-scope hook command receives:

- **Positional arguments:** `$1=state_name`, `$2=status`, `$3=data_path`
- **Environment variables:** `FDSX_STATE_NAME`, `FDSX_STATUS`, `FDSX_DATA_PATH`, `FDSX_THREAD_ID`, `FDSX_FLOW_NAME`, `FDSX_HOOKS`

`FDSX_STATUS` values: `starting` (on_state_start), `completed` or `failed` (on_state_end).

`FDSX_HOOKS` contains the lifecycle event name: `on_state_start` or `on_state_end`.

Hook data files are written to `.fdsx/runs/<thread-id>/hooks/<state-name>/input.json` (before execution) and `output.json` (after execution).

State-scope hooks respect `on_failure: abort` — a non-zero exit with `abort` policy raises an error and stops the workflow. `warn` (default) logs a warning and continues.

### Wait-scope hooks (`on_wait_start`, `on_wait_end`)

Run when a `wait` state suspends (before prompting the user) and resumes (after the user provides input). Can be defined at flow level, config level, or in a `wait` state's `hooks` block.

```yaml
states:
  approval:
    type: wait
    mode: prompt
    message: "Approve this change?"
    choices: [APPROVED, REJECTED]
    result_path: $.decision
    hooks:
      on_wait_start:
        - command: "notify.sh 'Waiting for approval'"
          on_failure: warn
      on_wait_end:
        - command: "notify.sh 'Decision received'"
    next: process
```

**`on_wait_start`** fires before the wait state suspends and the user prompt is displayed.

**`on_wait_end`** fires after the user provides input and the wait state resumes execution.

Each wait-scope hook command receives the same positional arguments and environment variables as state-scope hooks:

- **Positional arguments:** `$1=state_name`, `$2=status`, `$3=data_path`
- **Environment variables:** `FDSX_STATE_NAME`, `FDSX_STATUS`, `FDSX_DATA_PATH`, `FDSX_THREAD_ID`, `FDSX_FLOW_NAME`, `FDSX_HOOKS`

`FDSX_STATUS` values: `starting` (on_wait_start), `completed` or `failed` (on_wait_end).

`FDSX_HOOKS` contains `on_wait_start` or `on_wait_end`.

Wait-scope hooks respect `on_failure: abort` — a non-zero exit with `abort` policy raises an error and stops the workflow. `warn` (default) logs a warning and continues.

Hook merging: global → project → flow → state (same order as state-scope hooks).

### Workflow-scope hooks (`on_workflow_start`, `on_workflow_end`)

Run at the start and end of an entire workflow run. Can be defined at flow level, in config files, or in `pass` state `hooks` blocks (though in a `pass` state they are silently ignored at runtime — workflow hooks only fire from `flow.hooks` and config-level hooks).

```yaml
hooks:
  on_workflow_start:
    - command: "echo Workflow starting"
  on_workflow_end:
    - command: "notify.sh"
```

Each workflow-scope hook command receives:

- **No positional arguments** (unlike state-scope hooks)
- **Environment variables:** `FDSX_HOOKS`, `FDSX_STATUS`, `FDSX_FLOW_NAME`, `FDSX_THREAD_ID`
- `FDSX_STATE_NAME` and `FDSX_DATA_PATH` are **not** set

`FDSX_STATUS` values: `starting` (on_workflow_start), `completed`, `failed`, or `aborted` (on_workflow_end).

`FDSX_HOOKS` contains `on_workflow_start` or `on_workflow_end`.

Workflow-scope hooks are always warn-only — non-zero exits log a warning and never abort the workflow. Each hook has a 30-second subprocess timeout. `on_workflow_start` fires only on fresh runs (not on `fdsx resume`).

### Run-scope hooks (`on_run_start`, `on_run_end`)

Run once per CLI invocation — outside any individual workflow or flow context. Configured under a **separate** `run_hooks:` key in `.fdsx/config.yaml` (not under `hooks:`). Not available in workflow YAML or at state level.

```yaml
# .fdsx/config.yaml
run_hooks:
  on_run_start:
    - command: "echo CLI starting"
  on_run_end:
    - command: "notify-completion.sh"
```

Each run-scope hook command receives:

- **No positional arguments**
- **Environment variables:** `FDSX_HOOKS`, `FDSX_STATUS` only
- `FDSX_STATE_NAME`, `FDSX_DATA_PATH`, `FDSX_FLOW_NAME`, and `FDSX_THREAD_ID` are **not set** (run hooks fire outside any flow/thread context)

`FDSX_STATUS` values: `starting` (on_run_start), `completed`, `failed`, or `partial` (on_run_end; `partial` occurs in tasks-dir mode when some entries succeeded and some failed).

`FDSX_HOOKS` contains `on_run_start` or `on_run_end`.

Run-scope hooks are always warn-only — non-zero exits log a warning and never abort the run. Each hook has a 30-second subprocess timeout. Merging: global → project config concatenated (no flow or state level).

## Config File

`.fdsx/config.yaml` supports these options beyond provider settings:

```yaml
auto_workflow: false            # skip workflow confirmation UI (default: false)
workflows_dir: .fdsx/workflows  # directory for workflow discovery
default_tasks_dir: .fdsx/tasks/ # default tasks directory for no-arg fdsx run
workflow_selector:
  provider: claude              # LLM for auto-selecting workflows
  model: claude-sonnet-4-6
  extra_instructions: "..."     # optional additional prompt instructions
task_splitter:                  # must be explicitly present to enable batch splitting
  provider: claude
  model: claude-sonnet-4-6
  extra_instructions: "..."
extraction_fallback:            # global default when no per-rule fallback is configured
  provider: claude              # or use profile: <name> (XOR with provider + model)
  model: claude-sonnet-4-6
  extra_instructions: "..."     # optional instructions appended to recovery prompt
hooks:                          # global hooks applied to all flows
  on_state_start:
    - command: "echo starting"
  on_workflow_end:
    - command: "notify.sh"
  on_wait_start:
    - command: "echo wait starting"
  on_wait_end:
    - command: "echo wait ended"
run_hooks:                      # run-level hooks fired once per CLI invocation
  on_run_start:
    - command: "echo CLI starting"
  on_run_end:
    - command: "notify-completion.sh"
profiles:                       # named provider/model bundles
  fast:
    provider: claude
    model: claude-haiku-4-5-20251001
```

Both `workflow_selector` and `task_splitter` support `profile: <name>` (XOR with `provider`/`model`). When both global (`~/.config/fdsx/config.yaml`) and project (`.fdsx/config.yaml`) configs declare `task_splitter`, the project block fully replaces the global one — fields are not merged.

`extraction_fallback` at config level sets the project-wide default recovery LLM for extraction failures. Individual workflows can override it with their own `extraction_fallback:` field or disable it with `extraction_fallback: false`. When both global (`~/.config/fdsx/config.yaml`) and project (`.fdsx/config.yaml`) declare this block, the project block fully replaces the global one — fields are not merged.

`retry_escalation` at config level sets the project-wide default escalation target used when a workflow AI task exhausts its primary-provider retries. Individual workflows can override it with their own `retry_escalation:` field (full `provider` + `model` object) or opt out entirely with `retry_escalation: false`. When a workflow omits `retry_escalation`, the config-level value is inherited automatically. When both global (`~/.config/fdsx/config.yaml`) and project (`.fdsx/config.yaml`) declare this block, the project block fully replaces the global one — fields are not merged.

`hooks` at config level supports all six lifecycle keys (`on_state_start`, `on_state_end`, `on_workflow_start`, `on_workflow_end`, `on_wait_start`, `on_wait_end`) and are prepended to flow-level and state-level hooks.

`run_hooks` is a separate key from `hooks` and only supports `on_run_start` and `on_run_end`.

`profiles` defined here are merged with workflow-level profiles (workflow-level overrides config-level per name).

## Common Patterns

**Loop (plan-implement-review cycle):**
Set `max_loop` at flow level. Use a `choice` state to either loop back to `plan` or proceed to `done`.

**Parallel review with aggregation:**
Use `parallel` → `pass` (with `aggregate`) → `choice` to fan out reviews, aggregate votes, then branch on result.

**Human gate:**
Use `wait` state with `mode: prompt` to pause for user input, then route with `choice`.

**Map over items (e.g., process each file):**
Use a preceding state to produce an array, then `map` to iterate over it with a sub-workflow per item:

```yaml
states:
  collect:
    type: task
    provider: system
    command: "echo '[{\"path\":\"a.py\"},{\"path\":\"b.py\"}]'"
    result_path: $.files
    next: process_each

  process_each:
    type: map
    items_path: $.files
    iterator:
      states:
        - type: task
          name: review
          provider: claude
          model: claude-sonnet-4-6
          prompt_template: "Review file: {item.path}"
          result_path: $.review
    result_path: $.reviews
    fail_fast: true
    end: true
```

Inside iterator states, `{item}` refers to the current array element. Use `{item.field}` for nested access.

**Hard stop with named error:**
Use a `fail` state to terminate with a structured error when a condition is unrecoverable:

```yaml
states:
  check:
    type: choice
    choices:
      - variable: $.status
        operator: equals
        value: "invalid"
        next: abort
    default: process

  abort:
    type: fail
    error: "InvalidInput"
    cause: "Input status was invalid; cannot proceed."

  process:
    type: task
    provider: claude
    model: claude-sonnet-4-6
    prompt_template: "Process: {task}"
    result_path: $.result
    end: true
```

## Validation Rules

- `start_at` must reference an existing state name
- All `next` references must point to existing states
- Flow must have at least one path to termination (`end: true` or a `fail` state)
- `prompt_template` and `prompt_file` are mutually exclusive
- `next` and `end` are mutually exclusive
- `fail` state forbids `next`, `end`, and `max_iterations`
- `result_file` must be a top-level `$.varname` path (no nesting)
- Extract `result_path` must not use reserved keys: `output`, `exit_code`, `error`
- Map iterator states must all have `type: task` and unique `name` fields
- `extraction_fallback` at flow level must have exactly one of `provider + model` or `profile` set (XOR); `provider` requires `model` and vice versa; `system` is forbidden as provider. Set to `false` to disable config-level inheritance.
- `on_workflow_start` and `on_workflow_end` are forbidden inside per-state `hooks` blocks for `task`, `choice`, `parallel`, `wait`, `map`, and `fail` states; `pass` state `hooks` accepts all six keys (workflow-scope and wait-scope keys are silently ignored at runtime)
- `on_wait_start` and `on_wait_end` are only valid on `wait` state `hooks` blocks and at flow/config level; using them on `task`, `choice`, `parallel`, `map`, or `fail` state `hooks` blocks raises a validation error (`pass` state accepts them via `HookConfig` but silently ignores them at runtime)
- `on_run_start` and `on_run_end` are forbidden in flow YAML (`Flow.hooks`) and all state `hooks` blocks — they are only valid in `.fdsx/config.yaml` and `~/.config/fdsx/config.yaml` under the `run_hooks:` key

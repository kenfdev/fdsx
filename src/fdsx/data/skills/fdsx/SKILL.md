---
name: fdsx
description: >
  Expert guide for authoring, validating, and running fdsx declarative AI agent
  workflow YAML files. Use when writing fdsx workflows, editing workflow YAML,
  configuring fdsx providers (claude, codex, opencode, gemini), setting up
  profiles, adding hooks, using choice/parallel/loop/wait/pass states, running
  fdsx CLI commands, debugging workflow validation errors, or asking about fdsx
  YAML schema. Also triggers on: "fdsx", "workflow YAML", "declarative agent
  workflow", "multi-step AI pipeline", "provider options", "checkpoint resume".
---

# fdsx Workflow Authoring Guide

fdsx executes multi-step AI agent workflows defined in declarative YAML. It compiles workflow definitions into state machines, executes them by invoking LLM CLI tools (`claude`, `codex`, `opencode`, `gemini`) or shell commands as subprocesses, and manages checkpoint/resume across runs.

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

Every state except `choice` supports `next` (go to state) or `end: true` (terminate flow). These are mutually exclusive.

## Providers

| Provider | CLI Command | Requires | Options Key |
|----------|------------|----------|-------------|
| `claude` | `claude -p <prompt> --model <model>` | `model`, `prompt_template` or `prompt_file` | `permission_mode`, `dangerously_skip_permissions`, `allowed_tools`, `disallowed_tools` |
| `codex` | `codex exec --model <model> <prompt>` | `model`, `prompt_template` or `prompt_file` | `sandbox`, `approval_policy`, `full_auto`, `dangerously_bypass_approvals_and_sandbox` |
| `opencode` | `opencode run -m <model> <prompt>` | `model`, `prompt_template` or `prompt_file` | `permission` (passed via `OPENCODE_CONFIG_CONTENT` env var) |
| `gemini` | `gemini -p <prompt> --model <model>` | `model`, `prompt_template` or `prompt_file` | `approval_mode`, `yolo`, `sandbox`, `include_directories`, `extensions`, `policy` |
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

Profile shorthand is supported on task states, parallel branches, and extract fallback configurations.

## Variable Substitution

Prompts use `{variable}` syntax (single curly braces) referencing JSONPath state results:

```yaml
prompt_template: "Review this code: {implementation}"
```

Variables resolve from `$.results.<state_name>.output` or from `--input` CLI arguments. Use `result_path: $.foo` to store a state's output at `$.foo`.

Global variables automatically available in every state: `{task}` and `{source}` (injected at runtime for batch execution).

## Extraction

Extract structured data from provider output:

```yaml
extract:
  strategy: [keyword]           # tried in order: json, regex, keyword
  pattern: "APPROVED|REJECTED"
  result_path: $.decision
  fallback:                     # optional LLM classification fallback
    type: llm_classify
    provider: claude            # or use profile: <name> (XOR with provider)
    prompt: "Classify as APPROVED or REJECTED"
```

`result_path` and `extract.result_path` must not overlap. Branch `extract.result_path` must not use reserved keys: `output`, `exit_code`, `error`.

## CLI Commands

```
fdsx run <workflow.yaml> [--input KEY=VALUE] [--tasks <file>] [--tasks-dir <dir>] [--thread-id <id>] [--quiet] [--auto-workflow] [--confirm-workflow]
fdsx validate <workflow.yaml>
fdsx resume --thread-id <id> [--base-dir <path>]
fdsx list [--base-dir <path>]
fdsx split <task-file> [--force]
fdsx --version
fdsx --ci | --interactive        # global flags (mutually exclusive)
```

`--auto-workflow` and `--confirm-workflow` are mutually exclusive. `--auto-workflow` skips interactive workflow confirmation; `--confirm-workflow` forces the confirmation UI.

## Hooks

Shell commands that run before/after state or flow execution:

```yaml
hooks:
  on_start:
    - command: "echo Starting"
      on_failure: warn       # or "abort"
  on_complete:
    - command: "echo Done"
```

Hooks can be defined at flow level and per-state level. Each hook command receives:

- **Positional arguments:** `$1=state_name`, `$2=status`, `$3=data_path`
- **Environment variables:** `FDSX_STATE_NAME`, `FDSX_STATUS`, `FDSX_DATA_PATH`, `FDSX_THREAD_ID`, `FDSX_FLOW_NAME`

Hook data files are written to `.fdsx/runs/<thread-id>/hooks/<state-name>/input.json` (before execution) and `output.json` (after execution).

## Config File

`.fdsx/config.yaml` supports these options beyond provider settings:

```yaml
auto_workflow: false            # skip workflow confirmation UI (default: false)
workflows_dir: .fdsx/workflows  # directory for workflow discovery
workflow_selector:
  provider: claude              # LLM for auto-selecting workflows
  model: claude-sonnet-4-6
  extra_instructions: "..."     # optional additional prompt instructions
task_splitter:                  # must be explicitly present to enable batch splitting
  provider: claude
  model: claude-sonnet-4-6
  extra_instructions: "..."
```

Both `workflow_selector` and `task_splitter` support `profile: <name>` (XOR with `provider`/`model`).

## Common Patterns

**Loop (plan-implement-review cycle):**
Set `max_loop` at flow level. Use a `choice` state to either loop back to `plan` or proceed to `done`.

**Parallel review with aggregation:**
Use `parallel` → `pass` (with `aggregate`) → `choice` to fan out reviews, aggregate votes, then branch on result.

**Human gate:**
Use `wait` state with `mode: prompt` to pause for user input, then route with `choice`.

## Validation Rules

- `start_at` must reference an existing state name
- All `next` references must point to existing states
- Flow must have at least one path to termination (`end: true`)
- `prompt_template` and `prompt_file` are mutually exclusive
- `next` and `end` are mutually exclusive
- `result_file` must be a top-level `$.varname` path (no nesting)
- Extract `result_path` must not use reserved keys: `output`, `exit_code`, `error`
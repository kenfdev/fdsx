# fdsx — Flow-Driven Stateful eXecution

[![PyPI version](https://img.shields.io/pypi/v/fdsx.svg)](https://pypi.org/project/fdsx/)

A lightweight framework for building and executing complex AI agent workflows using declarative YAML definitions.

## Overview

fdsx enables you to define AI agent workflows in YAML, combining the durability of LangGraph (checkpoint, interrupt, conditional routing) with the declarative structure of AWS Step Functions.

**Key features:**
- Declarative YAML-based workflow definition
- Interactive project initialization and scaffolding
- Stateful execution with checkpoint/resume
- Parallel execution with branch aggregation
- Map state for iterating over arrays with sub-workflows
- Persistent batch task processing with crash-resilient resume
- Multiple LLM provider support (Claude, Cursor, Codex, Gemini, OpenCode, and system commands)
- Named profiles for reusable provider/model configuration
- Webhook notifications on wait states
- Lifecycle hooks (on_state_start / on_state_end / on_workflow_start / on_workflow_end / on_run_start / on_run_end / on_wait_start / on_wait_end) at global, project, flow, and state level
- Output extraction with JSON, regex, keyword strategies and LLM fallback
- Global and per-flow extraction fallback and retry escalation
- Workflow auto-selection via LLM-based matching

## Installation

```bash
pip install fdsx
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install fdsx
```

## Quick Start

Initialize a new project:

```bash
fdsx init
```

This interactively scaffolds a `.fdsx/` directory with configuration and example workflows.

Create a simple YAML workflow file:

```yaml
name: SimpleFlow
description: A minimal hello-world workflow
start_at: greet
version: "1.0"

states:
  greet:
    type: task
    provider: system
    command: "echo 'Hello from fdsx!'"
    result_path: $.message
    end: true
```

Run it:

```bash
fdsx run simple_flow.yaml
```

## Workflow YAML Schema

Below is the full annotated schema. Every field is shown with its type, default, and constraints as inline comments.

```yaml
# ============================================================
# Flow — top-level workflow definition
# ============================================================
name: MyWorkflow                # (string, REQUIRED) human-readable flow name
description: What this flow does # (string, REQUIRED) flow description
start_at: first_state           # (string, REQUIRED) name of the initial state; must exist in `states`
version: "1.0"                  # (string, optional) version identifier
max_loop: 10                    # (int, default: 10) max times any state can be re-entered before aborting

# --- Profiles: named provider+model bundles (optional) ---
# Define here or in .fdsx/config.yaml. Workflow-level overrides config-level.
# Extra fields beyond provider/model are passed as provider_options.
profiles:
  smarty:
    provider: claude            # (string, REQUIRED) one of: claude, cursor, codex, opencode, gemini
    model: claude-opus-4-6      # (string, REQUIRED) model name
  doer:
    provider: opencode
    model: opencode-go/minimax-m2.7
  cursor_coder:
    provider: cursor
    model: claude-sonnet-4-6

# --- Workflow-level provider configs (optional) ---
# Applied to all states using this provider. Overridden by per-task provider_options.
providers:
  claude:
    permission_mode: bypassPermissions
  cursor:
    approve_mcps: true
  codex:
    full_auto: true

# --- Flow-level hooks (optional) ---
# Run before/after states or the entire flow. Merged with config-level hooks.
# See "Hook Environment" section below for available env vars and positional args.
hooks:
  on_state_start:
    - command: "echo 'State starting'"  # (string, REQUIRED) shell command
      on_failure: warn                   # "warn" (default) = log and continue, "abort" = stop execution
  on_state_end:
    - command: "echo 'State done'"
      on_failure: warn
  on_workflow_start:                     # fires once when the workflow begins (fresh runs only)
    - command: "echo 'Workflow starting'"
      on_failure: warn
  on_workflow_end:                       # fires once when the workflow finishes (all terminal paths)
    - command: "echo 'Workflow done'"
      on_failure: warn
  on_wait_start:                         # fires before a wait state suspends for user input
    - command: "echo 'Waiting for input'"
      on_failure: warn
  on_wait_end:                           # fires after a wait state resumes (user has responded)
    - command: "echo 'Input received'"
      on_failure: warn

# --- Per-flow extraction fallback (optional) ---
# Overrides the global config-level extraction_fallback for this workflow.
# Set to false to disable an inherited global fallback.
extraction_fallback:
  provider: claude                      # (string, REQUIRED*) LLM provider
  model: claude-sonnet-4-6             # (string, REQUIRED when provider is set)
  # profile: smarty                    # (string, optional) mutually exclusive with provider/model
  extra_instructions: "..."            # (string, optional) appended to the fallback prompt
# extraction_fallback: false           # set false to disable inherited global config fallback

# --- Per-flow retry escalation (optional) ---
# When a task exhausts its retries, substitute a different provider/model.
# Overrides the global config-level retry_escalation for this workflow.
# Set to false to disable an inherited global escalation.
retry_escalation:
  provider: claude                      # (string, REQUIRED) escalation provider
  model: claude-opus-4-6               # (string, REQUIRED) escalation model
  provider_options:                     # (map, optional) extra options passed to the escalation provider
    permission_mode: bypassPermissions
# retry_escalation: false              # set false to disable inherited global config escalation

# ============================================================
# States — the execution graph
# ============================================================
states:

  # ----------------------------------------------------------
  # task — execute an LLM or shell command
  # ----------------------------------------------------------
  my_task:
    type: task                          # (REQUIRED) literal "task"

    # --- Provider (pick ONE approach) ---
    # Approach A: explicit provider + model
    provider: claude                    # (string, REQUIRED*) one of: claude, cursor, codex, opencode, gemini, system
    model: claude-sonnet-4-6            # (string, REQUIRED for LLM providers, FORBIDDEN for system)
    # Approach B: profile reference (mutually exclusive with provider/model)
    # profile: smarty

    # --- Prompt (REQUIRED for LLM providers, FORBIDDEN for system) ---
    # Use exactly one of prompt_template or prompt_file:
    prompt_template: |                  # (string) inline prompt; {variable} refs resolved at runtime
      Implement this task: {task}
    # prompt_file: plan.md             # (string) path to external prompt file

    # --- Command (REQUIRED for system provider, FORBIDDEN for LLM providers) ---
    # command: "echo hello"

    # --- Output ---
    result_path: $.plan                 # (string, REQUIRED) JSONPath where raw output is stored
    result_file: $.plan_ref             # (string, optional) stores absolute path of a result file
                                        #   must be a simple $.varname (no nesting)

    # --- Extraction: parse structured signals from LLM output (optional) ---
    extract:
      strategy: [keyword, regex]        # (list, REQUIRED) tried in order; values: json, regex, keyword
      pattern: "APPROVED|NEEDS_FIX"     # (string, REQUIRED) regex or keyword pattern
      result_path: $.decision           # (string, REQUIRED) where extracted value is stored
                                        #   must not overlap with the parent result_path
      # --- LLM fallback when extraction strategies all fail (optional) ---
      fallback:
        type: llm_classify              # (literal, REQUIRED) only "llm_classify" supported
        provider: claude                # (string, REQUIRED when not using profile) LLM provider for classification
        model: claude-sonnet-4-6        # (string, REQUIRED when provider is set)
        prompt: "Classify as APPROVED or NEEDS_FIX"  # (string, REQUIRED)
        # Alternatively, use a profile reference (mutually exclusive with provider/model):
        # profile: smarty

    # --- Execution control ---
    retry: 3                            # (int, default: 3) retry attempts on failure
    timeout_seconds: 300                # (int, optional) kill task after this many seconds
    max_iterations: 5                   # (int, optional, >= 1) max times this state can be entered

    # --- Per-task provider option overrides (optional) ---
    # Overrides workflow-level and config-level provider settings.
    provider_options:
      permission_mode: dontAsk

    # --- State-level hooks (optional) ---
    # Note: on_workflow_start, on_workflow_end, on_wait_start, and on_wait_end are NOT valid
    # here; use flow-level hooks (on_wait_start/on_wait_end are only valid on wait states).
    hooks:
      on_state_start:
        - command: "echo 'task starting'"
          on_failure: warn
      on_state_end:
        - command: "echo 'task done'"
          on_failure: abort             # abort = stop the flow if this hook fails

    # --- Transition (pick one) ---
    next: next_state                    # (string) go to this state
    # end: true                         # (bool) terminate the flow
    #   next and end are mutually exclusive

  # ----------------------------------------------------------
  # choice — conditional branching based on variable values
  # ----------------------------------------------------------
  check_result:
    type: choice                        # (REQUIRED) literal "choice"
    choices:                            # (list, REQUIRED) evaluated in order; first match wins
      - variable: $.decision            # (string, REQUIRED) JSONPath to the value to compare
        operator: equals                # (string, REQUIRED) one of:
                                        #   equals, not_equals, greater_than, less_than, contains
        value: "APPROVED"               # (any, REQUIRED) value to compare against
        next: done                      # (string, REQUIRED) target state if condition matches
      - variable: $.decision
        operator: contains
        value: "FIX"
        next: fix
    default: fallback_state             # (string, optional) state when no choice matches
    max_iterations: 10                  # (int, optional) max times this state can be entered
    hooks:                              # (optional) on_state_start / on_state_end only

  # ----------------------------------------------------------
  # parallel — run multiple branches concurrently
  # ----------------------------------------------------------
  parallel_review:
    type: parallel                      # (REQUIRED) literal "parallel"
    branches:                           # (list, REQUIRED) each branch is an independent execution
      - provider: claude                # same provider rules as task
        model: claude-sonnet-4-6
        # Alternatively, use a profile reference (mutually exclusive with provider/model):
        # profile: smarty
        prompt_template: |
          Review code quality: {implementation}
        # prompt_file: review.md        # alternative to prompt_template
        # command: "echo test"          # for system provider
        extract:                        # (optional) same structure as task extract
          strategy: [keyword]
          pattern: "approved|needs_fix"
          result_path: $.verdict
        retry: 2                        # (int, default: 3)
        timeout_seconds: 120            # (int, optional)
        provider_options:               # (map, optional) per-branch overrides
          permission_mode: plan

      - provider: codex
        model: gpt-5.4
        prompt_file: review-security.md
        extract:
          strategy: [keyword]
          pattern: "approved|needs_fix"
          result_path: $.verdict

    result_path: $.reviews              # (string, REQUIRED) JSONPath for the results array
    result_file: $.reviews_ref          # (string, optional) path to result file
    min_success: 2                      # (int, optional) minimum branches that must succeed
    max_iterations: 3                   # (int, optional)
    hooks:                              # (optional) on_state_start / on_state_end only
    next: aggregate_reviews             # next / end — same rules as task
    # end: true

  # ----------------------------------------------------------
  # map — iterate over a list, executing an iterator sub-graph
  # ----------------------------------------------------------
  process_items:
    type: map                            # (REQUIRED) literal "map"
    items_path: $.items                  # (string, REQUIRED) JSONPath to the array to iterate over
    iterator:                            # (map, REQUIRED) sub-graph run once per item
      states:                           # (list, REQUIRED) ordered list of states in the iterator
        - name: step1
          type: task
          provider: system
          command: "echo {item}"         # {item} references the current array element
          result_path: $.iter.step1
          retry: 0
        - name: step2
          type: task
          provider: system
          command: "echo {item}"
          result_path: $.iter.step2
          retry: 0
    fail_fast: true                     # (bool, default: true) stop all iterations on first failure
    result_path: $.map_results           # (string, REQUIRED) JSONPath for the results array
    max_iterations: 10                  # (int, optional) max times this state can be re-entered
    hooks:                              # (optional) on_state_start / on_state_end only
    next: after_map                     # next / end — same rules as task
    # end: true

  # ----------------------------------------------------------
  # pass — data transformation / aggregation (no execution)
  # ----------------------------------------------------------
  aggregate_reviews:
    type: pass                          # (REQUIRED) literal "pass"

    # --- Variable transformation (optional) ---
    parameters:                         # (map, optional) set/transform variables
      status: "reviewed"

    # --- Aggregate parallel results (optional) ---
    aggregate:
      source: $.reviews                 # (string, REQUIRED) JSONPath to the parallel results array
      field: verdict                    # (string, REQUIRED) field to aggregate from each result
      strategy: all                     # (string, REQUIRED) one of: majority, all, any
      match: "approved"                 # (string, REQUIRED) value that counts as a positive match
      no_match: "needs_fix"             # (string, REQUIRED) value when strategy condition not met
      result_path: $.review_decision    # (string, REQUIRED) where aggregated result is stored

    max_iterations: 3                   # (int, optional)
    hooks:                              # (optional) pass states accept all hook keys including
                                        #   on_workflow_start, on_workflow_end, on_wait_start,
                                        #   and on_wait_end
    next: review_route                  # next / end — same rules as task
    # end: true

  # ----------------------------------------------------------
  # wait — pause for human input, optionally send webhook
  # ----------------------------------------------------------
  approval:
    type: wait                          # (REQUIRED) literal "wait"
    mode: prompt                        # (REQUIRED) currently only "prompt" is supported
    message: "Approve the changes?"     # (string, REQUIRED) displayed in the terminal
    choices: ["approve", "reject"]      # (list, REQUIRED, min 1 item) options the user selects from
    result_path: $.approval             # (string, REQUIRED) where the selected value is stored

    # --- Webhook notification (optional) ---
    # Fires a POST request when this wait state is reached.
    # Useful for alerting a team (e.g., Slack) that human input is needed.
    notify:
      webhook:
        url: "https://hooks.slack.com/services/T.../B.../xxx"
                                        # (string, REQUIRED) must be HTTPS
                                        #   HTTP allowed only for localhost / 127.0.0.1
        template: "Approval needed for: {task}"
                                        # (string, REQUIRED) {variable} refs resolved from current state
                                        # Sends POST with JSON body: {"text": "<resolved message>"}
                                        # Non-2xx responses are logged as warnings, never fail the flow

    max_iterations: 1                   # (int, optional)
    hooks:                              # (optional) on_state_start / on_state_end /
                                        #   on_wait_start / on_wait_end
                                        # on_wait_start fires before the prompt is shown
                                        # on_wait_end fires after the user selects a choice
    next: post_approval                 # next / end — same rules as task
    # end: true

  # ----------------------------------------------------------
  # fail — terminate the flow with an error
  # ----------------------------------------------------------
  fatal_error:
    type: fail                          # (REQUIRED) literal "fail"
    error: "TaskFailed"                 # (string, REQUIRED, min 1 char) error name
    cause: "Task could not complete."   # (string, REQUIRED, min 1 char) error cause description
    hooks:                              # (optional) on_state_start / on_state_end only
                                        # Note: fail has no next, end, or max_iterations —
                                        # it terminates the flow immediately on entry
```

### Hook Environment

Every hook command receives context via **environment variables** and **positional arguments**.

**Environment variables:**

| Variable | Description | Example |
|---|---|---|
| `FDSX_STATE_NAME` | Name of the current state | `plan` |
| `FDSX_STATUS` | Lifecycle status | `starting`, `completed`, or `failed` |
| `FDSX_DATA_PATH` | Path to the state data JSON file | `.fdsx/runs/<thread_id>/hooks/plan/input.json` |
| `FDSX_THREAD_ID` | Current run thread ID | `abc123` |
| `FDSX_FLOW_NAME` | Name of the flow | `MyWorkflow` |
| `FDSX_HOOKS` | Lifecycle event name that triggered the hook | `on_state_start`, `on_workflow_end`, `on_wait_start`, `on_wait_end`, `on_run_start`, `on_run_end` |

**Positional arguments** (appended to your command):

| Position | Value | Same as env var |
|---|---|---|
| `$1` | State name | `FDSX_STATE_NAME` |
| `$2` | Status | `FDSX_STATUS` |
| `$3` | Data path | `FDSX_DATA_PATH` |

**Data files:** Before each hook runs, fdsx writes a JSON file containing the current state dictionary:

- `on_state_start` hooks receive `input.json` — the state *before* execution
- `on_state_end` hooks receive `output.json` — the state *after* execution

Files are written to `.fdsx/runs/<thread_id>/hooks/<state_name>/`.

**Example hook using env vars:**

```yaml
hooks:
  on_state_start:
    - command: "curl -X POST https://slack.example.com/webhook -d '{\"text\": \"State '\"$FDSX_STATE_NAME\"' starting in flow '\"$FDSX_FLOW_NAME\"'\"}'"
      on_failure: warn
  on_state_end:
    - command: "cat $FDSX_DATA_PATH | jq .review_verdict"
      on_failure: warn
```

**Merge order:** Hooks from multiple levels are concatenated (not replaced) in this order: global config → project config → flow → state. All hooks at every level run.

**Hook scope:**
- `on_state_start` and `on_state_end` are valid at all levels (global config, project config, flow, and individual states).
- `on_workflow_start` and `on_workflow_end` are only valid at global config, project config, and flow scope — placing them inside a state's `hooks:` block will raise a validation error (the one exception is `pass` states, which accept all hook keys).
- `on_run_start` and `on_run_end` fire once per CLI invocation (wrapping the entire `fdsx run` or `fdsx resume` call). They are only valid in global config (`~/.config/fdsx/config.yaml`) and project config (`.fdsx/config.yaml`) under the `run_hooks:` key — placing them in flow or state YAML will raise a validation error.
- `on_wait_start` and `on_wait_end` are only valid on `wait` states and at global config, project config, and flow scope (inside `hooks:`). Placing them inside any non-wait state's `hooks:` block raises a validation error. `on_wait_start` fires before the wait prompt is shown to the user; `on_wait_end` fires after the user selects a choice and execution resumes.

### Variable References

Variables use JSONPath syntax throughout:

```yaml
# Storing output — result_path sets where a state's output goes
result_path: $.plan               # stored at key "plan" in flow state

# Reading variables — {variable} in prompts, templates, and webhook messages
prompt_template: |
  Here is the plan: {plan}        # reads from $.plan
  Reviews: {reviews}              # reads from $.reviews

# Comparing variables — choice rules reference with $.
choices:
  - variable: $.review_decision   # reads from $.review_decision
    operator: equals
    value: "approved"
    next: done

# Map iteration — {item} and {item.field} reference the current element
# {item} is the raw array element; {item.field} accesses a field on it
iterator:
  states:
    - name: step1
      type: task
      command: "echo {item}"      # current item from the items array
      prompt_template: |
        Process this record: {item.name}
```

**Built-in variables** are automatically injected into every state's variable context before prompt/command resolution:

| Variable | Description |
|---|---|
| `{task}` | Task description passed via `--input task=...` or from batch task entry |
| `{source}` | Source origin passed via `--input source=...` or from batch task file |
| `{run_path}` | Absolute path of the current run's data directory (`<base_dir>/runs/<thread_id>`). Read-only — cannot be overridden by `--input` or a state's `result_path`. Use it to share files between states: write to `{run_path}/artifact.txt` in one state and read from it in the next. |

## Project Configuration (`.fdsx/config.yaml`)

Config is loaded from two sources (later wins):
1. Global: `$XDG_CONFIG_HOME/fdsx/config.yaml` (or `~/.config/fdsx/config.yaml`)
2. Project: `.fdsx/config.yaml`

```yaml
# ============================================================
# .fdsx/config.yaml — full annotated schema
# ============================================================

# --- Profiles (optional) ---
# Same format as workflow-level profiles. Config profiles are available
# to all workflows; workflow-level profiles override by name.
profiles:
  smarty:
    provider: claude
    model: claude-opus-4-6
  doer:
    provider: opencode
    model: opencode-go/minimax-m2.7
  cursor_agent:
    provider: cursor
    model: claude-sonnet-4-6

# --- Workflows directory ---
workflows_dir: .fdsx/workflows    # (string, default: ".fdsx/workflows")
                                  #   must be relative, no ".." components
                                  #   where `fdsx run --tasks-dir` discovers workflows

# --- Default tasks directory ---
default_tasks_dir: .fdsx/tasks    # (string, optional) default directory for bare `fdsx run`
                                  #   when no workflow, --tasks, or --tasks-dir is given

# --- Auto-workflow selection ---
auto_workflow: false              # (bool, default: false) skip interactive confirmation UI

# --- Workflow selector: LLM used for auto-selecting workflows ---
workflow_selector:
  profile: smarty                 # (string, optional) profile ref — mutually exclusive with provider/model
  # provider: claude              # (string, default: "claude") one of: claude, cursor, codex, opencode, gemini
  # model: claude-sonnet-4-6     # (string, default: "claude-sonnet-4-6")
  extra_instructions: |           # (string, optional) appended to the selection prompt
    Prefer simple-impl for small tasks.

# --- Task splitter: LLM used by `fdsx add --split` ---
task_splitter:
  profile: smarty                 # (string, optional) profile ref — mutually exclusive with provider/model
  # provider: claude              # (string, default: "claude")
  # model: claude-sonnet-4-6     # (string, default: "claude-sonnet-4-6")
  extra_instructions: |           # (string, optional) appended to the split prompt
    Group related tasks together.

# --- Global extraction fallback (optional) ---
# Applied when extraction strategies all fail and no per-rule fallback is configured.
# Can be overridden per workflow via the flow-level extraction_fallback field.
extraction_fallback:
  provider: claude                # (string, REQUIRED when not using profile)
  model: claude-sonnet-4-6        # (string, REQUIRED when provider is set)
  # profile: smarty               # (string, optional) mutually exclusive with provider/model
  extra_instructions: "..."       # (string, optional)

# --- Global retry escalation (optional) ---
# When a task exhausts its retries, substitute a different provider/model.
# Can be overridden per workflow via the flow-level retry_escalation field.
retry_escalation:
  provider: claude                # (string, REQUIRED) escalation provider
  model: claude-opus-4-6          # (string, REQUIRED) escalation model
  provider_options:               # (map, optional) extra options for the escalation provider
    permission_mode: bypassPermissions

# --- Provider-specific defaults (optional) ---
# Applied to all workflows using that provider.
# Overridden by workflow-level `providers:` and per-task `provider_options:`.
# Merge precedence: config < workflow < task/branch
providers:

  claude:
    effort: high                         # (string, optional) one of:
                                         #   low, medium, high, xhigh, max
    permission_mode: bypassPermissions  # (string, optional) one of:
                                        #   default, acceptEdits, bypassPermissions, dontAsk, plan, auto
    dangerously_skip_permissions: true   # (bool, default: false)
    allowed_tools: []                    # (list of strings, default: []) tool allowlist
    disallowed_tools: []                 # (list of strings, default: []) tool denylist
    system_prompt: "Custom system prompt"  # (string, optional) override the default system prompt
    append_system_prompt: "Extra instructions"  # (string, optional) append to the default system prompt
    inactivity_timeout: 600              # (int, optional) seconds before killing inactive subprocess

  cursor:
    force: false                         # (bool, default: false) pass --force to the agent CLI
    sandbox: enabled                     # (string, optional) one of: enabled, disabled
    approve_mcps: false                  # (bool, default: false) pass --approve-mcps to the agent CLI
    inactivity_timeout: 600              # (int, optional) seconds before killing inactive subprocess

  codex:
    reasoning_effort: high               # (string, optional) one of:
                                         #   low, medium, high, xhigh, max, ultra
    sandbox: workspace-write             # (string, optional) one of:
                                         #   read-only, workspace-write, danger-full-access
    approval_policy: never               # (string, optional) one of: untrusted, on-request, never
    full_auto: false                     # (bool, default: false)
    dangerously_bypass_approvals_and_sandbox: false  # (bool, default: false)
    inactivity_timeout: 600              # (int, optional)

  opencode:
    variant: high                        # (string, optional) model-specific variant
    permission: "allow"                  # (string or map, optional)
                                         #   passed as OPENCODE_CONFIG_CONTENT env var
    inactivity_timeout: 600              # (int, optional)

  gemini:
    approval_mode: auto_edit             # (string, optional) one of: default, auto_edit, yolo, plan
    yolo: false                          # (bool, default: false) overrides approval_mode when true
    sandbox: false                       # (bool, default: false)
    include_directories: []              # (list of strings, default: []) extra directories to include
    extensions: []                       # (list of strings, default: []) extensions to enable
    policy: []                           # (list of strings, default: []) policy files to apply
    inactivity_timeout: 600              # (int, optional)

# --- Global hooks (optional) ---
# Merged with flow-level hooks (config hooks run first).
hooks:
  on_state_start:
    - command: "echo 'global state start'"
      on_failure: warn
  on_state_end:
    - command: "echo 'global state done'"
      on_failure: warn
  on_workflow_start:
    - command: "echo 'global workflow start'"
      on_failure: warn
  on_workflow_end:
    - command: "echo 'global workflow done'"
      on_failure: warn
  on_wait_start:
    - command: "echo 'global wait state entered'"
      on_failure: warn
  on_wait_end:
    - command: "echo 'global wait state resumed'"
      on_failure: warn

# --- Run-level hooks (optional) ---
# Fire once per CLI invocation (once per `fdsx run` or `fdsx resume` call),
# regardless of how many workflows or tasks are executed inside that invocation.
# Only valid here (global/project config) — not in flow or state YAML.
run_hooks:
  on_run_start:
    - command: "echo 'run starting'"
      on_failure: warn
  on_run_end:
    - command: "echo 'run done'"
      on_failure: warn
```

## CLI Reference

### Global Flags

| Flag | Description |
|------|-------------|
| `--version` | Show version and exit |
| `--ci` | Run in CI mode (non-interactive, mutually exclusive with `--interactive`). Also auto-detected from `CI` and `GITHUB_ACTIONS` environment variables |
| `--interactive` | Force interactive mode (mutually exclusive with `--ci`) |

### Commands

| Command | Description |
|---------|-------------|
| `fdsx init` | Initialize a new fdsx project with interactive setup |
| `fdsx init --skill` | Install the /fdsx Claude Code skill only (skip scaffold) |
| `fdsx run` | Execute tasks from default tasks directory (`default_tasks_dir` or `.fdsx/tasks/`) |
| `fdsx run <workflow.yaml>` | Execute a workflow |
| `fdsx run <workflow.yaml> --input key=value` | Pass input variables |
| `fdsx run --tasks-dir <dir>` | Persistent batch execution (workflow optional) |
| `fdsx run ... --quiet` | Suppress stderr streaming output |
| `fdsx run ... --auto-workflow` | Skip workflow confirmation UI |
| `fdsx run ... --confirm-workflow` | Show workflow confirmation UI (requires interactive mode) |
| `fdsx run ... --continue-on-error` | Continue processing remaining entries on error in tasks-dir mode |
| `fdsx resume --thread-id <id>` | Resume from checkpoint |
| `fdsx resume --thread-id <id> --base-dir <dir>` | Resume with custom base directory |
| `fdsx validate <workflow.yaml>` | Validate YAML syntax |
| `fdsx list` | List recent runs |
| `fdsx list --base-dir <dir>` | List runs from custom base directory |
| `fdsx add <task_file>` | Add a task file to the batch execution queue (single task) |
| `fdsx add <task_file> --split` | Split a task file into individual task files |
| `fdsx add <task_file> --split --force` | Clear existing tasks directory before splitting |

## Example Workflow

```yaml
name: Plan-Implement-Review Loop
description: Iterative plan-implement-review cycle with LLM-based approval gating
start_at: plan
version: "1.0"
max_loop: 3

profiles:
  planner:
    provider: claude
    model: claude-sonnet-4-6
  coder:
    provider: cursor
    model: claude-sonnet-4-6

states:
  plan:
    type: task
    profile: planner
    prompt_template: |
      You are a planning agent. Break down the following task into clear,
      actionable implementation steps.

      Task: {task}
    result_path: $.plan
    next: implement

  implement:
    type: task
    profile: coder
    prompt_template: |
      You are an implementation agent. Follow this plan exactly.

      Plan: {plan}
    result_path: $.implementation
    next: review

  review:
    type: task
    provider: codex
    model: gpt-5.4
    prompt_template: |
      Review the implementation against the plan.

      Plan: {plan}
      Implementation: {implementation}
    result_path: $.review
    extract:
      strategy: [keyword]
      pattern: "APPROVED|NEEDS_FIX"
      result_path: $.review_verdict
    next: check_review

  check_review:
    type: choice
    choices:
      - variable: $.review_verdict
        operator: contains
        value: "APPROVED"
        next: done
    default: implement

  done:
    type: pass
    end: true
```

Run this example:
```bash
# Initialize the project (creates .fdsx/ with config and example workflows):
fdsx init

# Then run the scaffolded example workflow:
fdsx run .fdsx/workflows/plan-implement-review/workflow.yaml --input task="Build a web calculator"
```

## Checkpoint & Resume

Flows automatically persist state after each step. If interrupted (Ctrl+C, crash), resume from where you left off:

```bash
fdsx resume --thread-id <thread_id>
```

List all executions:
```bash
fdsx list
```

## License

MIT License.

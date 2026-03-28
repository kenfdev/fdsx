# fdsx — Flow-Driven Stateful eXecution

[![PyPI version](https://img.shields.io/pypi/v/fdsx.svg)](https://pypi.org/project/fdsx/)

A lightweight framework for building and executing complex AI agent workflows using declarative YAML definitions.

## Overview

fdsx enables you to define AI agent workflows in YAML, combining the durability of LangGraph (checkpoint, interrupt, conditional routing) with the declarative structure of AWS Step Functions.

**Key features:**
- Declarative YAML-based workflow definition
- Stateful execution with checkpoint/resume
- Parallel execution with branch aggregation
- Batch task processing (in-memory and persistent)
- Multiple LLM provider support (Claude, Codex, Gemini, OpenCode, and system commands)
- Named profiles for reusable provider/model configuration
- Webhook notifications on wait states
- Lifecycle hooks (on_start / on_complete) at flow and state level
- Output extraction with JSON, regex, keyword strategies and LLM fallback
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
    provider: claude            # (string, REQUIRED) one of: claude, codex, opencode, gemini
    model: claude-opus-4-6      # (string, REQUIRED) model name
  doer:
    provider: opencode
    model: opencode-go/minimax-m2.7

# --- Workflow-level provider configs (optional) ---
# Applied to all states using this provider. Overridden by per-task provider_options.
providers:
  claude:
    permission_mode: bypassPermissions
  codex:
    full_auto: true

# --- Flow-level hooks (optional) ---
# Run before/after the entire flow. Merged with config-level hooks.
hooks:
  on_start:
    - command: "echo 'Flow starting'"  # (string, REQUIRED) shell command
      on_failure: warn                  # "warn" (default) = log and continue, "abort" = stop execution
  on_complete:
    - command: "echo 'Flow done'"
      on_failure: warn

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
    provider: claude                    # (string, REQUIRED*) one of: claude, codex, opencode, gemini, system
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
        provider: claude                # (string, REQUIRED) LLM provider for classification
        prompt: "Classify as APPROVED or NEEDS_FIX"  # (string, REQUIRED)

    # --- Execution control ---
    retry: 3                            # (int, default: 3) retry attempts on failure
    timeout_seconds: 300                # (int, optional) kill task after this many seconds
    max_iterations: 5                   # (int, optional, >= 1) max times this state can be entered

    # --- Per-task provider option overrides (optional) ---
    # Overrides workflow-level and config-level provider settings.
    provider_options:
      permission_mode: dontAsk

    # --- State-level hooks (optional) ---
    hooks:
      on_start:
        - command: "echo 'task starting'"
          on_failure: warn
      on_complete:
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
    hooks:                              # (optional) same structure as task hooks

  # ----------------------------------------------------------
  # parallel — run multiple branches concurrently
  # ----------------------------------------------------------
  parallel_review:
    type: parallel                      # (REQUIRED) literal "parallel"
    branches:                           # (list, REQUIRED) each branch is an independent execution
      - provider: claude                # same provider rules as task (or use profile:)
        model: claude-sonnet-4-6
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
    hooks:                              # (optional)
    next: aggregate_reviews             # next / end — same rules as task
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
    hooks:                              # (optional)
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
    hooks:                              # (optional)
    next: post_approval                 # next / end — same rules as task
    # end: true
```

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
```

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

# --- Workflows directory ---
workflows_dir: .fdsx/workflows    # (string, default: ".fdsx/workflows")
                                  #   must be relative, no ".." components
                                  #   where `fdsx run --tasks-dir` discovers workflows

# --- Auto-workflow selection ---
auto_workflow: false              # (bool, default: false) skip interactive confirmation UI

# --- Workflow selector: LLM used for auto-selecting workflows ---
workflow_selector:
  profile: smarty                 # (string, optional) profile ref — mutually exclusive with provider/model
  # provider: claude              # (string, default: "claude") one of: claude, codex, opencode, gemini
  # model: claude-sonnet-4-6     # (string, default: "claude-sonnet-4-6")
  extra_instructions: |           # (string, optional) appended to the selection prompt
    Prefer simple-impl for small tasks.

# --- Task splitter: LLM used by `fdsx split` ---
task_splitter:
  profile: smarty                 # (string, optional) profile ref — mutually exclusive with provider/model
  # provider: claude              # (string, default: "claude")
  # model: claude-sonnet-4-6     # (string, default: "claude-sonnet-4-6")
  extra_instructions: |           # (string, optional) appended to the split prompt
    Group related tasks together.

# --- Provider-specific defaults (optional) ---
# Applied to all workflows using that provider.
# Overridden by workflow-level `providers:` and per-task `provider_options:`.
# Merge precedence: config < workflow < task/branch
providers:

  claude:
    permission_mode: bypassPermissions  # (string, optional) one of:
                                        #   default, acceptEdits, bypassPermissions, dontAsk, plan, auto
    dangerously_skip_permissions: true   # (bool, default: false)
    allowed_tools: []                    # (list of strings, default: []) tool allowlist
    disallowed_tools: []                 # (list of strings, default: []) tool denylist
    inactivity_timeout: 600              # (int, optional) seconds before killing inactive subprocess

  codex:
    sandbox: workspace-write             # (string, optional) one of:
                                         #   read-only, workspace-write, danger-full-access
    approval_policy: never               # (string, optional) one of: untrusted, on-request, never
    full_auto: false                     # (bool, default: false)
    dangerously_bypass_approvals_and_sandbox: false  # (bool, default: false)
    inactivity_timeout: 600              # (int, optional)

  opencode:
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
  on_start:
    - command: "echo 'global start'"
      on_failure: warn
  on_complete:
    - command: "echo 'global done'"
      on_failure: warn
```

## CLI Reference

### Global Flags

| Flag | Description |
|------|-------------|
| `--version` | Show version and exit |
| `--ci` | Run in CI mode (non-interactive) |
| `--interactive` | Force interactive mode |

### Commands

| Command | Description |
|---------|-------------|
| `fdsx run <workflow.yaml>` | Execute a workflow |
| `fdsx run <workflow.yaml> --input key=value` | Pass input variables |
| `fdsx run <workflow.yaml> --tasks tasks.yaml` | In-memory batch execution |
| `fdsx run --tasks-dir <dir>` | Persistent batch execution (workflow optional) |
| `fdsx run ... --quiet` | Suppress stderr streaming output |
| `fdsx run ... --auto-workflow` | Skip workflow confirmation UI |
| `fdsx run ... --confirm-workflow` | Show workflow confirmation UI |
| `fdsx resume --thread-id <id>` | Resume from checkpoint |
| `fdsx resume --thread-id <id> --base-dir <dir>` | Resume with custom base directory |
| `fdsx validate <workflow.yaml>` | Validate YAML syntax |
| `fdsx list` | List recent runs |
| `fdsx list --base-dir <dir>` | List runs from custom base directory |
| `fdsx split <task_file>` | Split a task file into individual task files |
| `fdsx split <task_file> --force` | Clear existing tasks directory before splitting |

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
    provider: opencode
    model: opencode/minimax-m2.5-free
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
# First run in a new directory scaffolds .fdsx/ with example workflows:
fdsx run

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

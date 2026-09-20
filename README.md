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
- Multiple LLM provider support (Claude, Cursor, Codex, Gemini, Grok, OpenCode, Pi, and system commands)
- Named profiles for reusable provider/model configuration
- Webhook notifications on wait states
- Lifecycle hooks (on_state_start / on_state_end / on_workflow_start / on_workflow_end / on_run_start / on_run_end / on_wait_start / on_wait_end) at global, project, flow, and state level
- [Jev evaluation](#jev-evaluation) via schema-based tasks or explicit evaluation states, with Choice/Noul/Score answers and checkpoint reuse
- Output extraction with JSON, regex, keyword strategies and LLM fallback
- Provider-independent JSON Schema validation for structured task and branch output
- [Native session forks](docs/session-forks.md) with `fork_from` for tasks, parallel branches and map items; Pi support plus Claude/Codex/Grok integrations with native qualification pending
- Stable keyed upsert merging for iterative structured ledgers
- Named parallel branches with required-branch boolean gates
- One-based state iteration values for loop-aware prompts
- Explicit non-success outcomes when a workflow reaches `max_loop`
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

Below is the annotated workflow schema. For Jev-specific task restrictions and the additional `evaluate` state, see [Jev Evaluation](#jev-evaluation) and its [full reference](docs/evaluation.md).

```yaml
# ============================================================
# Flow — top-level workflow definition
# ============================================================
name: MyWorkflow                # (string, REQUIRED) human-readable flow name
description: What this flow does # (string, REQUIRED) flow description
start_at: first_state           # (string, REQUIRED) name of the initial state; must exist in `states`
version: "1.0"                  # (string, optional) version identifier
max_loop: 10                    # (int, default: 10) max loop iterations; exhaustion returns max_loop_reached

# --- Profiles: named provider+model bundles (optional) ---
# Define here or in .fdsx/config.yaml. Workflow-level overrides config-level.
# Extra fields beyond provider/model are passed as provider_options.
profiles:
  smarty:
    provider: claude            # (string, REQUIRED) one of: claude, cursor, codex, opencode, gemini, grok, pi
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
    provider: claude                    # (string, REQUIRED*) claude, cursor, codex, opencode, gemini, grok, pi, system, jev
    # Jev requires structured_output and retry: 0 (default); see Jev Evaluation below.
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
    # Alternatively, configure structured_output instead of result_path/extract:
    # structured_output:
    #   schema: schemas/plan.schema.json # relative to this workflow YAML
    #   result_path: $.plan              # stores the parsed object/list, not a JSON string
    #   allow_extra_fields: false         # optional; default true, false rejects extras
    #   merge:                            # optional; lists of objects only
    #     strategy: upsert
    #     key: id                        # replace by key, append new keys, retain omissions

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
      - name: quality                   # (string, optional) stable branch identity
        provider: claude                # same provider rules as task
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

      - name: security
        provider: codex
        model: gpt-5.4
        prompt_file: review-security.md
        extract:
          strategy: [keyword]
          pattern: "approved|needs_fix"
          result_path: $.verdict

    result_path: $.reviews              # (string, REQUIRED) JSONPath for the results array
    result_file: $.reviews_ref          # (string, optional) path to result file
    min_success: 2                      # (int, optional) minimum branches that must succeed
    # Or use a required-branch gate instead of min_success:
    # Required branches must replace extract with structured_output.
    # gate:
    #   required: [quality, security]   # named branches with veto power
    #   field: $.review.approved        # branch-local structured output field
    #   expected: true
    #   result_path: $.approved         # top-level boolean for a following choice
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

### Classifier

Use [`type: classifier`](docs/classifier.md) for a single Jev choice with probability/confidence acceptance conditions and a configured LLM fallback. It supports top-level and parallel execution and writes a common answer for subsequent choice routing.

### Jev Evaluation

Use `provider: jev` on a top-level task to classify or score a prompt against a JSON Schema. Unlike the LLM providers, Jev uses the bundled Typesafe SDK, not a CLI. Set `TYPESAFE_API_KEY` in the execution environment; workflows without evaluation do not need it.

```yaml
assess:
  type: task
  provider: jev
  model: jev-1.13.0
  prompt_template: "Review: {review}\nRequirements: {requirements}"
  structured_output:
    schema: review-decision.schema.json
    result_path: $.assessment
    allow_extra_fields: false
  next: route
```

The schema defines independent Choice (named alternatives), Noul (Yes probability), or Score (ordered levels) questions. It must use the supported evaluation schema subset, not arbitrary JSON Schema. Route on the saved fields with a normal `choice` state. Evaluation returns answers; it does not grant permissions or perform the proposed action.

The [Jev review](src/fdsx/examples/workflows/evaluation-task/review-jev.yaml) and [Claude review](src/fdsx/examples/workflows/evaluation-task/review-llm.yaml) examples share their prompt and schema. Switching `provider` and `model` preserves the output contract and downstream routing, but does not guarantee identical answers. The [minimal example](src/fdsx/examples/workflows/evaluation-task/minimal-jev.yaml) classifies a fixed sentence; [Space Hotel](src/fdsx/examples/workflows/evaluation-task/space-hotel.yaml) combines terminal input, Jev classification, and conditional routing.

The existing `type: evaluate`, `evaluator: jev` format remains supported for explicitly named `literal`/`ref` materials and inline questions. It saves a different result shape: for example, `$.assessment.answers.action.choice` rather than the task schema's `$.assessment.action`.

Both formats are top-level only. Jev tasks require `structured_output` and an explicit model; task retries default to zero. Session forks, provider options, task timeout overrides, output-file writes, and structured-output merges are unsupported. Only the SDK retries communication; there is no LLM fallback. Start checks require a key before hooks or preceding tasks. Resume can reuse saved answers without a key when no evaluation is reachable; returning to an evaluation makes a new request.

See [Jev evaluation steps](docs/evaluation.md) for schema definitions, restrictions, transport limits, safe diagnostics, and resume behavior. Examples are tested with mocked responses, not live-service accuracy checks.

### Native Session Forks

Use `fork_from` to inherit an earlier task's conversation rather than inserting
its final text into a prompt. Implementation and review can independently inherit
planning while sharing the current working files:

```yaml
name: independent-review
description: Implement and review the same plan
start_at: plan
retry_escalation: false
profiles:
  agent:
    provider: pi
    model: anthropic/claude-sonnet-4-6
states:
  plan:
    type: task
    profile: agent
    prompt_template: Plan the requested change.
    next: implement
  implement:
    type: task
    profile: agent
    fork_from: plan
    prompt_template: Implement the plan in the current directory.
    next: review
  review:
    type: task
    profile: agent
    fork_from: plan
    prompt_template: Review the current files against the plan.
    result_path: $.review
    end: true
```

The source must be a top-level task guaranteed to finish before the destination
on every route. Parallel branches and map items can fork the same outer source.
Each retry creates a new child; failed child conversations do not carry over.
Forks do not isolate or roll back files. Both ends must use the same provider,
but may use different models. Same-provider model changes are also allowed during
retry escalation. Model availability and history compatibility depend on the provider CLI.

Pi uses its saved completed endpoint (baseline 0.85.1). Claude, Codex and Grok
integrations select the saved session's usable conversation at fork time;
**full native qualification, including cross-model behavior, remains pending**.
Cursor, Gemini, OpenCode and system forks are rejected. Both ordinary resume and
`resume --from` preserve saved session references, including with `--input` updates.
Resume from a fork destination to reuse its source's last successful session, or
from the source to regenerate it. Each destination execution creates a new child,
not a continuation of its previous attempt. Native history must remain available;
checkpoints are not conversation backups.

See [session fork rules and provider limits](docs/session-forks.md) and the
[Pi](examples/session_fork_smoke.yaml) / [Grok](examples/session_fork_smoke_grok.yaml)
manual smoke examples. Real checks require separate execution approval and can
incur model charges.

### Structured Output and Convergence

Task states and parallel branches can validate complete provider stdout against a JSON Schema before it enters workflow state:

```yaml
states:
  update_ledger:
    type: task
    provider: claude
    model: claude-sonnet-4-6
    prompt_template: "Produce ledger update {state.iteration} as JSON"
    structured_output:
      schema: schemas/ledger.schema.json
      result_path: $.ledger
      merge:
        strategy: upsert
        key: id
    next: review

  review:
    type: parallel
    branches:
      - name: security
        provider: claude
        model: claude-sonnet-4-6
        prompt_template: "Review the ledger: {ledger}"
        structured_output:
          schema: schemas/review.schema.json
          result_path: $.review
      - name: style
        provider: codex
        model: gpt-5.4
        prompt_template: "Give advisory feedback for: {ledger}"
        structured_output:
          schema: schemas/review.schema.json
          result_path: $.review
    result_path: $.reviews
    gate:
      required: [security]
      field: $.review.approved
      expected: true
      result_path: $.approved
    end: true
```

The schema path is relative to the workflow file and is loaded and checked before provider execution. Claude, Codex, and Grok also receive the schema through their native CLI schema controls while retaining streaming; Gemini, Cursor, and OpenCode receive JSON-only schema guidance in the prompt. fdsx always parses and validates the final value locally, regardless of native support. Complete output must be one JSON object or list; a single outer Markdown code fence is allowed. Validation failures use the state's normal retry count and provide bounded corrective feedback. The `system` provider is not retried for a structured-output validation failure.

Extra fields are allowed by default: `additionalProperties: false` and `unevaluatedProperties: false` are non-fatal, including inside nested and composite schemas. Unknown fields are retained in workflow state; required fields, known-property schemas, enum/pattern/numeric constraints, JSON syntax, and the object/list requirement remain enforced. Set `allow_extra_fields: false` to reject unknown fields strictly.

`merge.strategy: upsert` requires a top-level result path and lists of objects containing the configured key. Matching objects are replaced in place, new keys append, and omitted existing objects remain. A batch with missing or duplicate keys is rejected.

With `gate`, required named branches must execute successfully and provide schema-valid output. A valid value different from `expected` sets the boolean result to `false`; execution, validation, or missing-field failures on required branches fail the parallel state. Unlisted advisory branch failures remain in the results without blocking the gate. `gate` and `min_success` are mutually exclusive.

Reaching `max_loop` returns `FlowResult.status == "max_loop_reached"`, preserves partial results and checkpoint state, passes that status to workflow-end hooks, produces a non-zero CLI exit, and marks tasks-directory entries failed.

### Hook Environment

Every hook command receives context via **environment variables** and **positional arguments**.

**Environment variables:**

| Variable | Description | Example |
|---|---|---|
| `FDSX_STATE_NAME` | Name of the current state | `plan` |
| `FDSX_STATUS` | Lifecycle status | `starting`, `completed`, `failed`, `aborted`, `partial`, or `max_loop_reached` depending on hook scope |
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
| `{state.iteration}` | One-based execution count for the current state. The first entry is `1`; loop re-entry increments it. |

## Common task instructions

Set `prompt_prefix` in `$XDG_CONFIG_HOME/fdsx/config.yaml` (by default
`~/.config/fdsx/config.yaml`) or the project's `.fdsx/config.yaml` to share
instructions across workflows without editing AGENTS.md:

```yaml
prompt_prefix: |
  Explain changes briefly.
  Run the relevant local tests before finishing.
```

Alternatively, put the instructions in a UTF-8 file:

```yaml
prompt_prefix_file: rules.md
```

Relative paths are based on the folder containing the configuration file:
this example reads `.fdsx/rules.md` for project configuration, or
`$XDG_CONFIG_HOME/fdsx/rules.md` for global configuration. Inherited global
paths keep that base; neither the current working directory nor the workflow
folder is used. Absolute paths, parent references (`../`), symlinks, and `~/`
(expanded to the home folder) are supported. File line endings are preserved.

Do not specify `prompt_prefix` and `prompt_prefix_file` in the same configuration,
even if one value is empty. The project choice replaces the global choice
completely, including when switching between inline text and a file.
Omitting both keys inherits the global value; `prompt_prefix: ""` disables it,
as does a string
containing only spaces, tabs, or newlines. An empty or whitespace-only file also
disables the prefix without falling back to the global value.
An empty file path is invalid; use an existing empty file or `prompt_prefix: ""`
to disable instructions. A missing value, explicit `null`, or
non-string value is a configuration error. Each configuration file is validated
before merging, even when the project replaces an invalid global value.
Only the selected instruction file is read: an overridden global file need not
exist or be readable. A selected file that is missing, unreadable, or not valid
UTF-8 causes a configuration error before auto-selection or any AI task starts.
Diagnostics identify the setting and error cause without including file contents.
Configuration lookup locations are unchanged.

For a nonblank value, fdsx preserves all characters, including leading/trailing
whitespace and braces, then adds two newline characters and the resolved task
body. Only the task body receives variable substitution. An unset or disabled
prefix leaves the body unchanged, with no added separator. This works with both
inline task prompts and existing task `prompt_file` inputs.

The prefix is sent once per AI task invocation across all LLM providers,
including parallel branches, map iterations, loops, retries, provider escalation,
and retries with structured-output feedback. It is excluded from workflow
auto-selection, extraction fallback/recovery calls, system commands, and hooks.
It is configured only at the global or project level.

Each `run_flow` or `resume_flow` call reads the current configuration and selected
instruction file once; it does not reload during that call. Resume uses the
latest contents and file reference, including switching between file and inline text,
disabling the prefix or removing a project override to inherit the global value.
Invalid current settings prevent remaining tasks from starting. Consecutive task
files use the existing per-`run_flow`/`resume_flow` configuration loading boundary.
The checkpoint format does not change. Configuration loading raises `ValueError`
for invalid prefix values and file reading/decoding failures; resume wraps setup
errors in its existing `FlowExecutionError`. The CLI reports configuration errors
on stderr and exits nonzero.

This is ordinary prompt text, separate from provider-specific `system_prompt`
or developer instruction options. It does not guarantee instruction priority,
compliance, permissions, or command restrictions. Do not include secrets: the
text is sent to the provider and may appear in existing prompt records. Provider
stdout/stderr is also recorded in per-state logs, so echoed instructions can
appear there even in quiet mode.

## Output and failure diagnostics

fdsx preserves Japanese and other Unicode characters in generated JSON for run
records, hook data, map progress, and native structured provider results instead
of converting them to `\uXXXX` escapes. The JSON format and parsed values are
unchanged.

Parallel branch entries in run records include `name`, `exit_code`, and `error`
alongside provider details. Use these fields and per-branch stdout/stderr logs
to investigate failures. Codex streaming `turn.failed` and `error` messages are
preserved in stderr logs and, on a nonzero exit, supplement the returned stderr
without being mixed into agent output. Quiet mode suppresses terminal streaming,
not the saved logs. Logs may contain sensitive provider output.

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

# --- Common AI task instructions (choose inline text OR a UTF-8 file) ---
prompt_prefix: "Explain changes briefly."
# prompt_prefix_file: rules.md    # relative to this config file's directory

# --- Workflow selection ---
auto_workflow: false              # (bool, default: false) skip interactive confirmation UI
manual_workflow: false            # (bool, default: false) disable AI workflow selection

# --- Workflow selector: LLM used for auto-selecting workflows ---
workflow_selector:
  profile: smarty                 # (string, optional) profile ref — mutually exclusive with provider/model
  # provider: claude              # (string, default: "claude") one of: claude, cursor, codex, opencode, gemini, grok, pi
  # model: claude-sonnet-4-6     # (string, default: "claude-sonnet-4-6")
  extra_instructions: |           # (string, optional) appended to the selection prompt
    Prefer simple-impl for small tasks.

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
    developer_instructions: "Stay within the assigned task."  # (string, optional)
    agents_enabled: false                # (bool, optional) enable/disable Codex subagents
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

  grok:
    permission_mode: dontAsk             # default; default|acceptEdits|auto|dontAsk|bypassPermissions|plan
    sandbox: workspace                   # (string, optional) Grok sandbox profile; unset by default
    allow: []                            # (list of strings) repeatable permission allow rules
    deny: []                             # (list of strings) repeatable permission deny rules
    tools: []                            # (list of strings) built-in tool allowlist
    disallowed_tools: []                 # (list of strings) built-in tools to remove
    reasoning_effort: high               # (string, optional)
    max_turns: 20                        # (positive int, optional)
    on_max_turns: fail                   # fail (default) or return_partial
    no_subagents: true                   # default: true
    no_plan: true                        # default: true
    cross_session_memory: off            # off (default), on, or inherit
    disable_web_search: false            # default: false
    verbatim: true                       # default: true
    cwd: /workspace/project              # (string, optional)
    agent: reviewer                      # (string, optional) agent name or definition path
    agents: {}                           # JSON-compatible map; requires no_subagents: false when non-empty
    rules: "Review carefully"            # mutually exclusive with system_prompt_override
    # system_prompt_override: "..."      # replaces Grok's normal system prompt
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
| `fdsx run --tasks-dir <dir>` | Drain queued tasks sequentially until the directory is empty (workflow optional) |
| `fdsx run ... --quiet` | Suppress stderr streaming output |
| `fdsx run ... --auto-workflow` | Auto-select and skip confirmation; override manual config |
| `fdsx run ... --manual-workflow` | Disable AI selection and use the numbered workflow editor |
| `fdsx run ... --confirm-workflow` | Show workflow confirmation UI (requires interactive mode) |
| `fdsx run ... --continue-on-error` | Continue processing remaining entries on error in tasks-dir mode |
| `fdsx resume --thread-id <id>` | Resume an interrupted or retryable failed execution from its checkpoint |
| `fdsx resume --thread-id <id> --from <state>` | Recover a non-successful execution by jumping to a previously executed state |
| `fdsx resume --thread-id <id> --base-dir <dir>` | Resume with custom base directory |
| `fdsx validate <workflow.yaml>` | Validate YAML syntax |
| `fdsx resolve <workflow.yaml>` | Print normalized YAML with prompt files and referenced profiles resolved for inspection |
| `fdsx list` | List recent runs |
| `fdsx list --base-dir <dir>` | List runs from custom base directory |
| `fdsx add <task_file>...` | Append one or more files to the default task queue in argument order |

`fdsx add` copies each source file verbatim into one queued task. It appends after
existing active and completed sequence numbers and honors `default_tasks_dir`.
`fdsx run` processes one task at a time, rescans for tasks added while it is active,
and exits successfully when the queue is empty. Only one runner may drain a given
tasks directory at a time.

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

Maps resume at item boundaries. Both iterator forms reuse saved items by their
zero-based input index, even when saved indices are not contiguous. Unfinished
items restart at their first step; final results remain in input order. A new
visit to the map starts fresh. Execution remains sequential.

Map progress uses a versioned format with the map visit and each collected
item's result and status. Older contiguous progress is read without modifying
the file and migrates on the next successful save. An old `null` whose status
cannot be determined remains collected with status `unknown`; it is neither
retried nor counted as a known success or failure. Newly saved failures retain
their status across resume. With `fail_fast: false`, legacy iterators still fail
the map after collecting all items; local workflows can succeed with failure
envelopes. With `fail_fast: true`, failed items are recorded for diagnostics but
are retried on ordinary resume.

Progress messages on stderr use one-based item numbers. Results (`index`) and
run records (`item_index`) use zero-based numbers. Item logs live under
`logs/<map>/<visit>/<execution-id>/<item-index>/`; every map invocation, including
resume, gets a new execution ID so earlier logs remain intact. Local state names
and managed result-file locations retain their existing scope.

Map entries in `run.json` distinguish `completed_count` (durably collected
items), `reused_count`, and `executed_count` (items started this invocation).
`success_count`, `failure_count`, and `unknown_count` describe the collected
results. `attempt_count` and per-item `task_attempts` count task-provider attempts,
including retries, during this invocation; local routing/pass states do not
increment them. Reused items add no new execution or attempt records. Saving an
item unsuccessfully fails the map and leaves that item eligible for execution
on resume.

Ordinary resume assumes unchanged inputs. To change inputs, use explicit recovery
with `--from <map>` and `--input`; this invalidates map progress and reruns the
map. An external side effect completed before progress was saved can happen
again after a stop: item checkpointing does not guarantee exactly-once effects.

For a terminal non-success outcome such as `fail`, `abort_*`, `max_loop`, or
`max_iterations`, fix the workflow or its inputs and explicitly select a
previously executed state:

```bash
fdsx resume --thread-id <thread_id> --from review
```

The equivalent Python API is:

```python
from fdsx.core.engine import resume_flow

result = resume_flow("<thread_id>", from_state="review")
```

To replace existing execution inputs during recovery, repeat `--input KEY=VALUE`:

```bash
fdsx resume --thread-id <thread_id> --from review --input 'task=Revised requirements'
```

`--from` is required even when submitted values are identical. Only saved input
keys are accepted; unspecified values remain unchanged. The terminal shows line
differences, the restart state, and a warning that retained results may reflect
old inputs. Interactive confirmation is required unless you pass `--yes` to
approve the submitted values and restart state without prompting:

```bash
fdsx resume --thread-id <thread_id> --from review --input 'task=Revised requirements' --yes
```

Without a terminal, input updates require explicit `--yes`; otherwise the command
exits with code 1 without changing saved data or running states or hooks.
Cancellation has the same effect. `--yes` does not bypass validation or locking,
and has no effect on ordinary resume without input updates or wait-state choices.

Recovery keeps the same thread and runs normal workflow verification and routing.
Changed inputs preserve full saved values, the prior run record, and copies of
fdsx-managed result files under `runs/<thread_id>/revisions/`; the checkpoint's
`_meta.input_revisions` links these snapshots. New runs also retain initial inputs
in `_meta.initial_inputs`. Older runs preserve only history still available when
updated. Identical inputs record a recovery attempt without a new input revision;
its full snapshot and managed files are linked by `_meta.recovery_snapshots`
in the same archive directory. After the first input revision, explicit recovery
without new inputs also archives the values and managed files it is about to
replace. Each snapshot includes the prior checkpoint metadata and run record,
so earlier revisions and reviews remain traceable after summary rewrites.

Snapshot publication precedes the single SQLite checkpoint update that stores
both accepted inputs and their history reference, under the existing thread lock.
If the process exits before that checkpoint commits, the old inputs remain
current; an unreferenced snapshot or `.pending` directory may remain and does not
represent an accepted revision. If it exits after commit, the new inputs and
published history remain together. Resume with an explicit `--from` to retry
interrupted recovery. This uses the existing local filesystem and SQLite
persistence; it does not reconstruct missing archives. For older executions,
only values and artifacts available at the first update can be preserved, not
already overwritten requirements or reviews.

Tasks-directory resume retains the original task entry and thread. Its normal
status updates still occur, but edited task descriptions and source files are
not imported into checkpoint inputs. Submit existing-input replacements explicitly;
resume does not start another batch or rewrite those descriptions.

External-tool files are not archived. Changing a file behind an unchanged input
path does not import its contents: submit the desired input value explicitly.

This is a recovery jump, not a rewind. It uses the latest checkpoint's business
data together with the current workflow YAML, starts the selected state with
fresh loop/parallel/map runtime bookkeeping, and continues on the same thread.
It does not reconstruct historical state from before the failure. The target
must be a previously executed state in the current workflow, cannot be a
`fail` state, and must have all variables it currently requires. A successful
execution cannot be recovered. Running terminal resume without `--from` prints
the eligible states; ordinary pending task/provider failures retain the
existing bare-resume retry behavior.

If the recovered workflow reaches another non-success outcome, fix the problem
and invoke `--from` (or `from_state`) again. Each explicit invocation gets a
fresh execution budget; fdsx never starts another recovery attempt
automatically.

List all executions:
```bash
fdsx list
```

### Manual workflow selection

Use `fdsx run --tasks-dir .fdsx/tasks --manual-workflow` to choose workflows
without calling the workflow-selection AI. To make this the default, set
`manual_workflow: true` in `.fdsx/config.yaml` or the global
`$XDG_CONFIG_HOME/fdsx/config.yaml` (normally `~/.config/fdsx/config.yaml`).
This also applies to `fdsx run` with no arguments. Project settings override
global settings, including `manual_workflow: false`.

Manual mode preserves saved assignments before applying a workflow argument.
With multiple candidates, unspecified tasks start unassigned in the existing
numbered editor; with one candidate, it is assigned before confirmation.
Enter a task number, then a workflow number to change an assignment; `c`
confirms only when every task is assigned, and `q` cancels before execution.
Confirmed assignments use the existing task YAML format. Project and global
workflows remain available, with project workflows taking precedence for duplicates.

- `--manual-workflow` or `manual_workflow: true` takes precedence over saved
  `auto_workflow: true` and disables selection AI.
- Explicit `--auto-workflow` overrides manual configuration, enables automatic
  selection, and skips confirmation. It conflicts with both `--manual-workflow`
  and `--confirm-workflow`.
- `--confirm-workflow` works with manual mode and does not re-enable selection AI.
  Explicit confirmation still requires interactive input.
- Without interactive input, unresolved manual assignments produce an error:
  supply a workflow argument or set `workflow` in each task. Fully assigned tasks
  and single-candidate assignments can run without input.

New task files discovered during a run inherit the mode and are confirmed at the
next batch, before those tasks execute. Manual mode does not disable AI tasks
inside workflows or change provider permissions. Direct single-workflow runs
continue without a selection screen.

## License

MIT License.

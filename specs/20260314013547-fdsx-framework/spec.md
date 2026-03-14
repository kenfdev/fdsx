# Feature Specification: Flow-Driven Stateful eXecution (fdsx)

**Status:** Draft
**Created:** 2026-03-14
**Last Updated:** 2026-03-14

## 1. Overview

### Problem Statement

When building complex workflows with AI agents (e.g., planning → implementation → parallel review → approval → PR creation), existing tools have the following challenges:

- **Ralph loop**: Lightweight but weak in parallel review, checkpointing, and tracing
- **LangGraph**: Powerful but requires writing graphs in Python code, lacking declarative definition capabilities
- **CrewAI / AutoGen**: Strong in multi-agent scenarios but lacking in declarative YAML definitions
- **Agent SDK dependency**: For users who want to call CLI tools directly, SDKs and API key billing become barriers

As of 2026, there is a rapidly growing demand for "writing AI workflows in a Step Functions-like manner using YAML/JSON," and fdsx aims to fulfill this as a "lightweight ASL (Agent States Language) for the LLM era."

### Proposed Solution

fdsx is a lightweight framework that enables building and executing complex AI agent workflows using only declarative YAML definitions. Based on a state transition model inspired by AWS Step Functions' ASL (Amazon States Language), it places LLM agents as nodes and achieves stateful (state-persisted) execution.

Core concepts:
- Simply define a "flow" in YAML, and everything from plan → implement → multi-review loop → approval → PR creation runs automatically
- Directly invoke existing CLI tools (claude, opencode, codex, etc.) as subprocesses, eliminating the need for Agent SDKs or API key billing
- Combines the durability of LangGraph (checkpoint / interrupt / conditional routing) with the declarative structure of Step Functions

### Target Users

- **Individual AI developers**: Claude Pro/Max users who want to automate workflows using LLMs
- **Prototyping leads in small teams**: People who want to quickly assemble and test AI workflows
- **Ralph loop users**: People who want to scale existing CLI-based workflows
- **Multi-LLM users**: People who want to use different models for different purposes, such as Opus for planning, MiniMax for implementation, and GPT-5.4 for review

## 2. User Scenarios & Testing

### Scenario 1: Simple Task → Implementation → Review Flow

**Actor**: Individual AI developer
**Goal**: Define a 3-step flow of Plan → Implement → Review in YAML and execute it from the CLI

**Flow**:
1. User creates a flow definition in a YAML file (3 Task states: Planner → Implement → Review)
2. Execution starts with the `fdsx run workflow.yaml` command
3. The Planner state invokes the claude CLI and breaks down the task
4. The result is saved to a variable, and the flow transitions to the Implement state
5. The Implement state invokes the opencode CLI and performs the implementation
6. The result is saved to a variable, and the flow transitions to the Review state
7. The Review state executes a review using the claude CLI
8. The flow completes successfully, and each state's results are recorded in the log

**Acceptance Criteria**:
- If there are validation errors in the YAML file, specific error messages are displayed before execution
- The start and completion of each state is displayed in the terminal
- The overall flow execution result is saved to a file in JSON format

### Scenario 2: Parallel Review + Majority Vote Conditional Branching

**Actor**: Multi-LLM user
**Goal**: Review simultaneously with 3 LLMs, determine approval/rejection by majority vote, and replan if rejected

**Flow**:
1. User defines a YAML flow containing a Parallel state
2. When the implementation result reaches the Parallel state, 3 LLM CLIs are launched simultaneously
3. The terminal displays progress for each branch via status lines
4. After all branches complete, a majority vote determination is performed using the Pass state's aggregation strategy (majority)
5. The Choice state routes based on the determination result: to CommitPR if APPROVED, to Planner if REJECTED
6. If REJECTED, the Planner is re-executed with the previous review results retained in state variables

**Acceptance Criteria**:
- The 3 branches are actually executed in parallel (not sequentially)
- During parallel execution, status of each branch is displayed in real-time
- Even if one parallel branch fails, execution can continue as long as the configured minimum success count is met
- The majority vote result and each reviewer's determination are recorded
- State variables from the previous iteration are retained during loops

### Scenario 3: Human-in-the-Loop Approval Gate (Wait State + Webhook Notification)

**Actor**: Quality-conscious developer
**Goal**: Establish a gate where a human approves before PR creation, and send a notification to Slack

**Flow**:
1. A Wait state (`type: wait`) is placed in the flow definition, with a Slack Incoming Webhook configured via `notify`
2. When the flow reaches the Wait state, an approval-pending notification is sent to Slack (via webhook)
3. A review result summary and options (approve/reject/retry) are displayed in the terminal
4. The user selects an option in the terminal
5. The selection result is saved to `result_path`, and the subsequent Choice state branches to CommitPR for approve, stop for reject, or Planner for retry

**Acceptance Criteria**:
- Flow execution is paused when the Wait state is reached
- A webhook notification is sent to the specified URL
- The user's selection result is recorded in the log
- Checkpoints are saved even during the waiting period
- The flow can be resumed from the waiting state with `fdsx resume` even after process interruption

### Scenario 4: Resumption from Interruption

**Actor**: User executing long-running flows
**Goal**: Resume from the last checkpoint even if the process is interrupted

**Flow**:
1. The process is interrupted during flow execution (Ctrl+C, terminal close, etc.)
2. Later, the user runs the `fdsx resume --thread-id myapp` command
3. Execution resumes from the state following the last completed state
4. All state variables from before the interruption are restored

**Acceptance Criteria**:
- Checkpoints are saved to a file upon each state completion
- Upon resumption, which state execution resumes from is displayed
- The flow after resumption produces the same result as if there had been no interruption

### Scenario 5: Decision Value Extraction for State Transitions

**Actor**: User using Choice states
**Goal**: Reliably extract decision values from LLM output and perform correct state transitions

**Flow**:
1. The Review state's LLM output (free text) is saved to result_path
2. Deterministic extraction rules (json / regex / keyword) are applied in order
3. If extraction succeeds, the value is used as the comparison variable for the Choice state
4. If deterministic extraction fails, a 2-phase determination (a separate lightweight LLM call) is executed
5. The Choice state determines the transition destination based on the extracted value

**Acceptance Criteria**:
- The deterministic extraction rule fallback chain (json → regex → keyword) works correctly
- The 2-phase determination is triggered only when all extraction methods fail
- If the 2-phase determination also fails, the flow stops with an error (subject to retry settings)
- The extraction method and result are recorded in the log

## 3. Functional Requirements

### FR-1: YAML-Based Flow Definition

- Users can define workflows in YAML files
- Naming conventions within YAML are unified as snake_case
- Flow definitions include the following required fields: `name`, `start_at`, `states`
- Optional fields: `comment`, `version`, `task_splitter` (LLM specification for batch task splitting, containing `provider` and `model`)
- YAML files are schema-validated before execution, and if errors are found, execution is rejected with specific error messages
- Variable reference syntax is differentiated by use case: `{variable}` (Python str.format()-style) within `prompt_template`, and `$.path` (JSONPath) for `result_path`/`aggregate`/`extract`
- Variables within `prompt_template` support dot access (`{review.decision}`) and index access (`{reviews[0].summary}`)
- Variable expansion uses a custom implementation that safely replaces only `{name}` patterns that exactly match registered variable names, rather than Python str.format(). Unknown `{...}` patterns (such as JSON or code contained in LLM output) are preserved as literals
- During validation, variable references within `prompt_template` are statically analyzed, and unreachable variable references (references to variables not set by preceding states' `result_path`) are detected as errors by tracing the flow's state transitions

### FR-2: State Types

The following state types are supported:

#### FR-2.1: Task State
- Executes the specified provider's CLI as a subprocess
- `provider`, `model`, `prompt_template` (or `prompt_file`) can be specified
- `prompt_template`: Write the prompt string inline. Variables can be referenced with `{variable}`
- `prompt_file`: Load the prompt from an external file. The path is resolved relative to the workflow YAML file. Variable references with `{variable}` are also available within the file
- `prompt_template` and `prompt_file` are mutually exclusive (specifying both results in a validation error)
- LLM output is saved to the variable specified by `result_path`
- `next` specifies the next state. `end: true` terminates the flow
- Automatic retry on error (number of retries is configurable in YAML, default 3)
- `timeout_seconds` (optional) specifies the subprocess execution timeout in seconds. Default is none (unlimited). Timeouts are treated as errors and are subject to retry

#### FR-2.2: Choice State
- Branches the transition destination based on state variable values
- Defines condition-destination pairs in the `choices` array
- Comparison operators: `equals`, `not_equals`, `greater_than`, `less_than`, `contains`
- `default` specifies the transition destination when no condition matches
- If default is undefined and no condition matches, it is an error

#### FR-2.3: Parallel State
- Executes multiple branches simultaneously
- Each branch has the same configuration as a Task state (provider, model, prompt_template/prompt_file)
- `min_success` specifies the minimum number of successful branches required (default: all branches)
- Each branch is retried individually (only failed branches are re-executed; already successful branches are not re-executed)
- If the minimum success count is not met after all retries, the entire Parallel state is an error
- Each branch's result is saved as an array to `result_path`. Each element is an object with `output` (raw LLM output string) and fields set by `extract.result_path` (e.g., `{output: "Full review text...", status: "APPROVED"}`)

#### FR-2.4: Pass State
- Passes input directly to output (or with parameter transformation)
- Performs state variable transformation/mapping with `parameters`
- Supports aggregation of parallel results with the `aggregate` block (see FR-4 below)

#### FR-2.5: Wait State
- Pauses flow execution and waits for external input
- `mode` specifies the wait mode. v1 supports only `prompt` (terminal prompt). `timer` (time-based wait) and `callback` (external callback) are planned for the future
- `message` specifies the message displayed in the terminal. Variable references with `{variable}` are available
- The `choices` array defines options the user can select (e.g., `[approve, reject, retry]`)
- The user's selection result is saved to `result_path`
- The `notify` block (optional) can send webhook notifications:
  - `webhook.url`: Notification destination URL (Slack Incoming Webhook, Discord Webhook, etc.)
  - `webhook.template`: Notification message template (variable references with `{variable}` are available)
  - If webhook delivery fails (network error, incorrect URL, etc.), a warning is logged, but the terminal prompt is displayed normally and the flow continues (notification is an auxiliary feature, and stopping the flow would be excessive)
- Checkpoints are saved even during waiting, and the flow can be resumed with `fdsx resume` after process interruption
- `next` specifies the next state. Can be combined with a Choice state to branch based on the selection result

#### FR-2.6: Loop (Implicit)
- Loops are achieved by specifying a previous state (such as Planner) in a Choice state's `default` or choices' `next`
- State variables are retained during loops (stateful)
- To prevent infinite loops, a maximum loop count can be configured (default: 10). The count is shared across the entire flow, tracking the total number of transitions across all loop edges (consistent with LangGraph's recursion_limit)
- When the maximum loop count is reached, the flow stops as "loop completed" rather than an error. Resuming with `fdsx resume` continues from the next iteration

### FR-3: Providers

The initial version supports the following preset providers:

- **claude**: Invokes the Claude CLI with the `claude -p` command
- **opencode**: Invokes the `opencode` command
- **codex**: Invokes the `codex` command
- **system**: Executes arbitrary shell commands (git, gh, etc.). Uses the `command` field instead of `prompt_template`, and the `model` field is not required. Variable references with `{variable}` are also available within `command`. Non-zero exit codes are treated as failures and are subject to retry

Each provider is executed as a subprocess, capturing stdout/stderr.
- At `fdsx validate` and `fdsx run` startup, all provider CLIs used in the flow definition are checked for existence on PATH, and if not found, execution is rejected with an error

### FR-4: Aggregation Strategies

The Pass state's `aggregate` block supports aggregation of parallel results:

```yaml
aggregate:
  source: $.parallel_reviews
  field: status
  strategy: majority  # majority | all | any
  match: "APPROVED"
  no_match: "REJECTED"
  result_path: $.decision
```

- **majority**: If more than half match the `match` value, sets the `match` value; otherwise sets the `no_match` value
- **all**: Sets the `match` value only if all match the `match` value; otherwise sets the `no_match` value
- **any**: If at least one matches the `match` value, sets the `match` value; if none match, sets the `no_match` value
- The `no_match` field is required. The user explicitly specifies the value to set when there is no match

### FR-5: Context Management

All states always start with a fresh context. Data passing between states is accomplished by explicitly referencing variables saved to `result_path` as `{variable}` in the next state's `prompt_template`. This achieves unified inter-state communication independent of the provider.

### FR-6: Output Extraction and Routing

A 2-layer approach is adopted to reliably extract decision values from LLM output for use in Choice states:

#### FR-6.1: Deterministic Extraction Rules (Default)
Specify an `extract` block in the state definition to extract values from LLM output:

```yaml
extract:
  strategy: [json, regex, keyword]
  pattern: "APPROVED|REJECTED"
  result_path: $.review_decision
```

- **json strategy**: First looks for a ` ```json...``` ` code block in the output and parses it; if not found, applies JSON.parse to the entire output. After successful parsing, retrieves the value using the field name specified in `pattern` as the key
- **regex strategy**: Applies `pattern` as a regular expression to the LLM output and uses the first match as the extracted value
- **keyword strategy**: Treats `pattern` as a pipe-delimited keyword list and uses the first keyword that appears in the LLM output as the extracted value (case-insensitive)
- Fallback chain: Strategies specified in the `strategy` array are attempted in the specified order (e.g., `[regex]` tries only regex, `[json, keyword]` tries json then keyword)
- If any strategy succeeds, the extracted value is saved to `result_path`
- If all fail, proceeds to 2-phase determination (if configured)

#### FR-6.2: 2-Phase Determination (Optional)
If deterministic extraction fails, a lightweight LLM call classifies the output:

```yaml
extract:
  strategy: [json, regex, keyword]
  pattern: "APPROVED|REJECTED"
  fallback:
    type: llm_classify
    provider: claude
    prompt: "Is the following review result APPROVED or REJECTED? Answer in one word: {output}"
  result_path: $.review_decision
```

- If the 2-phase determination also fails, it is an error (subject to retry settings)

### FR-7: Human-in-the-Loop (via Wait State)

When human approval or feedback is needed, the Wait state (FR-2.5) is used:

- The Wait state's `mode: prompt` displays a prompt in the terminal and waits for human input
- `choices` defines the options, and the result is saved to `result_path`
- `notify` sends notifications via webhook (Slack, Discord, etc.) to inform humans of pending approval
- Placing a Choice state after the Wait state enables branching based on the selection result (approve → next, reject → stop/replan, etc.)
- Supports checkpoint saving during waiting and resumption with `fdsx resume`

### FR-8: State Persistence (Checkpoint)

- Checkpoint persistence uses LangGraph's SqliteSaver (as part of full LangGraph integration, delegating checkpointer functionality to LangGraph)
- The default save location is a SQLite file in the `.fdsx/checkpoints/` directory relative to CWD
- `fdsx resume --thread-id <id>` resumes from the last checkpoint (leveraging LangGraph's resume functionality)
- Checkpoints include state variables, current state name, and execution history
- To prevent concurrent execution against the same thread_id, exclusive control is implemented on the fdsx side using PID-based lock files. The lock file records the PID, and if a lock is already held, subsequent executions result in an error. Stale locks from process crashes are automatically detected and cleaned up via PID liveness checks during `fdsx list` and lock acquisition
- Checkpoint integrity is verified upon resumption, and if corruption is detected, execution stops with an error. The user decides whether to start a new execution (`fdsx run`)

### FR-9: Error Handling

- Each state supports automatic retry (`retry` field for count specification, default 3)
- Retry intervals use exponential backoff
- When all retries are exhausted, the flow stops and the error details are recorded in the log
- Even after error-induced stops, the flow can be resumed with `fdsx resume` (from checkpoint)

### FR-10: CLI Interface

- `fdsx run <workflow.yaml>`: Execute a flow (new)
- `fdsx run <workflow.yaml> --input <key>=<value>`: Pass input values for a single task via CLI arguments (multiple allowed). All values are treated as strings. Cannot be used simultaneously with `--tasks` (validation error)
- `fdsx run <workflow.yaml> --thread-id <id>`: Execute with a specified thread ID (if omitted, a UUID is auto-generated and displayed in the terminal at execution start)
- `fdsx run <workflow.yaml> --tasks <tasks-file>`: Batch task execution (see FR-13 below)
- `fdsx resume --thread-id <id>`: Resume from checkpoint
- `fdsx validate <workflow.yaml>`: Execute YAML schema validation only
- `fdsx list`: Display list of running and stopped flows

### FR-13: Batch Task Execution

When a task file (Markdown, etc.) is specified with the `--tasks` option, fdsx performs the following:

1. Reads the task file and workflow definition
2. Uses the LLM specified by the `task_splitter` field (provider/model) to automatically split the task file content into task units appropriate for the workflow (referencing the workflow definition to determine appropriate granularity)
3. Displays the split task list to the user for confirmation
4. Executes the workflow sequentially for each task
5. Records the completion/failure of each task and automatically proceeds to the next task
6. Displays an execution result summary for all tasks at the end

- After task splitting, the user can review the split results before execution begins (execution starts upon approval, aborts upon rejection. If modifications are needed, the user edits the task file and re-runs)
- If one task fails, the user is prompted whether to continue executing the remaining tasks
- Each task is executed with an independent thread_id and can be individually resumed

### FR-11: Observability

#### Real-time Display
- During sequential execution: LLM output is streamed to the terminal
- During parallel execution: Progress for each branch is displayed via status lines, and after completion, each branch's output is displayed sequentially with headers
- A summary line is displayed for each state's start, completion, and elapsed time

#### Structured Log Storage
- All executions are automatically saved in JSON format to a file (`runs/<thread_id>.json` relative to CWD). On resume, data is appended to the same file
- Records input, output, elapsed time, and status for each state
- Structured to allow visualization by reading this JSON when future Web UI support is added

### FR-12: PyPI Publishing

- Packaged for installation via `pip install fdsx`
- Provides the `fdsx` command as a CLI entry point

### FR-14: YAML Schema Definition

The complete schema for flow definition YAML is shown below. `#` denotes comments, `?` denotes optional fields.

```yaml
# ============================================================
# Flow Definition (Top Level)
# ============================================================
name: string                        # Required. Flow name
start_at: string                    # Required. Name of the first state to execute (key in states)
states:                             # Required. Map of state definitions
  <state_name>: <StateDefinition>   # Key is the state name (snake_case)

comment?: string                    # Optional. Flow description
version?: string                    # Optional. Flow version
task_splitter?:                     # Optional. LLM configuration for batch task splitting (when using --tasks)
  provider: string                  #   Required. Provider name (claude, opencode, codex)
  model: string                     #   Required. Model name
max_loop?: integer                  # Optional. Maximum loop count (default: 10)

# ============================================================
# Task State
# ============================================================
<state_name>:
  type: task                        # Required
  provider: string                  # Required. claude | opencode | codex | system
  model?: string                    # Required except when provider=system. Model name
  prompt_template?: string          # Mutually exclusive with prompt_file. Inline prompt ({variable} references available)
  prompt_file?: string              # Mutually exclusive with prompt_template. Prompt file path (relative to workflow YAML, {variable} references available)
  command?: string                  # Used when provider=system (instead of prompt_template/prompt_file). {variable} references available
  result_path: string               # Required. Output destination (JSONPath format: $.variable_name)
  extract?:                         # Optional. Output value extraction rules
    strategy: [string]              #   Required. Array of extraction strategies (json, regex, keyword). Attempted in specified order
    pattern: string                 #   Required. json: field name, regex: regular expression, keyword: pipe-delimited keywords
    fallback?:                      #   Optional. Fallback when deterministic extraction fails
      type: llm_classify            #     Required. Fixed value
      provider: string              #     Required. LLM provider name
      prompt: string                #     Required. Classification prompt ({output} references the original output)
    result_path: string             #   Required. Destination for extracted value (JSONPath format)
  retry?: integer                   # Optional. Retry count (default: 3)
  timeout_seconds?: integer         # Optional. Subprocess execution timeout in seconds (default: none = unlimited)
  next?: string                     # Next state name (mutually exclusive with end: true)
  end?: boolean                     # true to end the flow (mutually exclusive with next)

# ============================================================
# Choice State
# ============================================================
<state_name>:
  type: choice                      # Required
  choices:                          # Required. Array of conditional branches
    - variable: string              #   Required. Comparison target variable (JSONPath format: $.variable_name)
      operator: string              #   Required. Comparison operator: equals | not_equals | greater_than | less_than | contains
      value: any                    #   Required. Comparison value
      next: string                  #   Required. Transition destination state name when condition matches
  default?: string                  # Optional. Transition destination when no condition matches. Error if unspecified and no match

# ============================================================
# Parallel State
# ============================================================
<state_name>:
  type: parallel                    # Required
  branches:                         # Required. Array of branches to execute in parallel
    - provider: string              #   Required. Provider name
      model?: string                #   Required except when provider=system
      prompt_template?: string      #   Mutually exclusive with prompt_file
      prompt_file?: string          #   Mutually exclusive with prompt_template (relative to workflow YAML)
      command?: string              #   Used when provider=system
      extract?:                     #   Optional. Same structure as Task state's extract
        strategy: [string]
        pattern: string
        fallback?: { type: llm_classify, provider: string, prompt: string }
        result_path: string
      retry?: integer               #   Optional. Per-branch retry count (default: 3)
      timeout_seconds?: integer     #   Optional. Per-branch timeout in seconds (default: none = unlimited)
  result_path: string               # Required. Destination for array of all branch results (JSONPath format)
  min_success?: integer             # Optional. Minimum number of successful branches (default: total branch count)
  next?: string                     # Next state name (mutually exclusive with end: true)
  end?: boolean                     # true to end the flow (mutually exclusive with next)

# ============================================================
# Pass State
# ============================================================
<state_name>:
  type: pass                        # Required
  parameters?:                      # Optional. Variable transformation/mapping
    <key>: <value>                  #   Arbitrary key-value pairs (JSONPath references available)
  aggregate?:                       # Optional. Aggregation of parallel results
    source: string                  #   Required. Source variable for aggregation (JSONPath format: $.parallel_results)
    field: string                   #   Required. Field name to aggregate
    strategy: string                #   Required. Aggregation strategy: majority | all | any
    match: string                   #   Required. Match determination value
    no_match: string                #   Required. Value when not matched
    result_path: string             #   Required. Destination for aggregation result (JSONPath format)
  next?: string                     # Next state name (mutually exclusive with end: true)
  end?: boolean                     # true to end the flow (mutually exclusive with next)

# ============================================================
# Wait State
# ============================================================
<state_name>:
  type: wait                        # Required
  mode: string                      # Required. "prompt" only in v1
  message: string                   # Required. Terminal display message ({variable} references available)
  choices: [string]                 # Required. Array of user choices (e.g., [approve, reject, retry])
  result_path: string               # Required. Destination for selection result (JSONPath format)
  notify?:                          # Optional. Webhook notification settings
    webhook:                        #   Required (within notify block)
      url: string                   #     Required. Notification destination URL
      template: string              #     Required. Message template ({variable} references available)
  next?: string                     # Next state name (mutually exclusive with end: true)
  end?: boolean                     # true to end the flow (mutually exclusive with next)
```

#### Validation Rules

- Simultaneous specification of `prompt_template` and `prompt_file` is prohibited
- States with provider=system must use `command`; `prompt_template`/`prompt_file`/`model` are prohibited
- States with provider=claude/opencode/codex require either `prompt_template` or `prompt_file`; `command` is prohibited
- Simultaneous specification of `next` and `end: true` is prohibited
- The value of `start_at` must exist as a key in `states`
- Values of `choices[].next` and `default` must exist as keys in `states`
- `{variable}` references within `prompt_template`/`prompt_file` are statically analyzed by tracing the flow's state transitions, and unreachable variable references are detected as errors
- `prompt_file` paths are resolved as relative paths from the workflow YAML file. If the file does not exist, it is a validation error

#### Complete Flow Definition Example

```yaml
name: plan_implement_review
comment: Plan → Implement → Parallel Review → Approval → PR Creation
version: "1.0"
start_at: planner
max_loop: 5

states:
  planner:
    type: task
    provider: claude
    model: opus
    prompt_file: prompts/plan.md
    result_path: $.plan
    next: implement

  implement:
    type: task
    provider: opencode
    model: default
    prompt_template: |
      Please implement based on the following plan:
      {plan}
    result_path: $.implementation
    next: parallel_review

  parallel_review:
    type: parallel
    branches:
      - provider: claude
        model: sonnet
        prompt_template: |
          Please review the following implementation:
          {implementation}
        extract:
          strategy: [regex, keyword]
          pattern: "APPROVED|REJECTED"
          result_path: $.status
      - provider: opencode
        model: default
        prompt_file: prompts/review.md
        extract:
          strategy: [regex, keyword]
          pattern: "APPROVED|REJECTED"
          result_path: $.status
      - provider: codex
        model: default
        prompt_template: |
          Review this implementation:
          {implementation}
        extract:
          strategy: [json, regex, keyword]
          pattern: "APPROVED|REJECTED"
          fallback:
            type: llm_classify
            provider: claude
            prompt: "Is this APPROVED or REJECTED? Answer in one word: {output}"
          result_path: $.status
    result_path: $.reviews
    min_success: 2
    next: aggregate_reviews

  aggregate_reviews:
    type: pass
    aggregate:
      source: $.reviews
      field: status
      strategy: majority
      match: "APPROVED"
      no_match: "REJECTED"
      result_path: $.decision
    next: check_decision

  check_decision:
    type: choice
    choices:
      - variable: $.decision
        operator: equals
        value: "APPROVED"
        next: approval_gate
    default: planner

  approval_gate:
    type: wait
    mode: prompt
    message: |
      Review result: {decision}
      Review details: {reviews}
      Do you approve?
    choices: [approve, reject, retry]
    result_path: $.approval
    notify:
      webhook:
        url: https://hooks.slack.com/services/xxx/yyy/zzz
        template: "Review complete: {decision} - Awaiting approval"
    next: check_approval

  check_approval:
    type: choice
    choices:
      - variable: $.approval
        operator: equals
        value: "approve"
        next: create_pr
      - variable: $.approval
        operator: equals
        value: "retry"
        next: planner
    default: flow_end

  create_pr:
    type: task
    provider: system
    command: "gh pr create --title 'Auto PR' --body '{plan}'"
    result_path: $.pr_url
    next: flow_end

  flow_end:
    type: pass
    end: true
```

## 4. Success Criteria

- A complete flow from planning → implementation → parallel review → conditional branching → PR creation works with a single YAML file
- 3 reviewers are actually executed in parallel (subprocesses are launched simultaneously, not sequentially)
- Flow interruption and resumption works correctly, and the state before interruption is fully restored when resuming from a checkpoint
- Decision value extraction in Choice states succeeds on the first deterministic extraction attempt with a probability of 95% or higher
- A user with only basic YAML knowledge can create a basic flow definition within 30 minutes while referencing documentation
- Switching between preset providers (claude, opencode, codex) is accomplished by simply changing the provider field in the YAML
- The approval gate via Wait state works, webhook notifications are sent, and the flow correctly branches based on the terminal selection result

## 5. Key Entities

### Flow
The definition of the entire workflow. Has name, start_at, and states.

### State
Each node within a flow. Has a type (task, choice, parallel, pass, wait) and defines the transition destination with next. State variables can store any JSON-compatible value (strings, numbers, objects, arrays).

### Provider
An abstraction of LLM CLI tools. Four types: claude, opencode, codex, and system.

### Checkpoint
A snapshot of the execution state saved upon state completion. Identified by thread_id.

### Run (Execution Record)
A structured log that retains the complete history of a flow execution. Includes input/output and elapsed time for each state.

### Extraction Rule
A rule definition for extracting structured values from LLM output. Includes deterministic extraction and 2-phase determination.

## 6. Scope

### In Scope

- YAML-based flow definition and execution (Task, Choice, Parallel, Pass, Wait, Loop)
- 4 preset providers (claude, opencode, codex, system)
- State persistence (in-memory + file storage) and resumption from checkpoints
- Parallel execution and control via minimum success count
- Strategy-based aggregation logic (majority, all, any)
- 2-layer output extraction (deterministic extraction + 2-phase LLM determination)
- Human approval gate via Wait state (CLI prompt + webhook notification)
- Inter-state data passing (result_path + prompt_template variable references)
- Error handling with retries
- CLI (fdsx run / resume / validate / list) and flow input (--input, --tasks)
- Batch task execution (LLM auto-splitting from task file → sequential workflow execution)
- Real-time status display and structured log storage
- PyPI publishing (pip install fdsx)

### Out of Scope

- **Web UI (Streamlit/FastAPI)**: Planned for the future. Designed with structured logs as the foundation, but the UI itself is not included
- **LangGraph Cloud support**: Cloud deployment is planned for the future
- **Custom provider extensions**: User-defined providers are planned for the future. Initial version supports presets only
- **Wait state extended modes**: `timer` (time-based wait) and `callback` (external callback) modes are planned for the future. v1 supports only `prompt` (terminal prompt)
- **Catch state**: Conditional branching on error is planned for the future. Initial version supports only retry + stop
- **Security / sandboxing**: Not guaranteed in the initial version. At the user's own risk
- **Human-in-the-Loop from Web UI**: Initial version supports CLI prompt only
- **Callback responses from Slack bots, etc.**: Initial version supports webhook notification (one-way) only. Callback mode to resume flows from Slack replies is planned for the future

## 7. Assumptions

- Python 3.11 or later is installed in the user's environment
- The provider CLI tools (claude, opencode, codex, etc.) used by the user are pre-installed and authenticated
- LLM provider CLIs return text output to stdout
- Write permissions to the file system exist (for checkpoint and log storage)
- Full integration with LangGraph (Python) as runtime: YAML flow definitions are dynamically compiled into LangGraph StateGraphs, delegating checkpoint, conditional branching, interrupt, and parallel execution to the LangGraph engine

## 8. Dependencies

- LangGraph: Dynamic state graph construction and execution engine
- PyYAML: YAML definition parsing
- Typer: CLI interface construction
- concurrent.futures: Concurrent execution of parallel branches
- httpx (or requests): Webhook notification delivery
- Provider CLI tools (claude, opencode, codex, git, gh): Must be installed in the user's environment

## 9. Risks

- **Provider CLI output format changes**: Updates to CLI tools may change their output format, potentially breaking parsing. Version pinning or an adapter layer is needed
- **Non-determinism of LLM output**: The same prompt can return different outputs, causing extraction rules to fail. Covered by 2-phase determination, but 100% reliability cannot be guaranteed
- **Resource consumption during parallel execution**: Simultaneously executing many branches may hit machine resource limits or API rate limits
- **LangGraph dependency**: Subject to the impact of LangGraph API changes or incompatible updates

## Clarifications

### Session 2026-03-14

- Q: How is the "opposite value" determined when majority does not reach a majority in the aggregation strategy? → A: Explicitly specified in the YAML via the `no_match` field (required field)
- Q: What happens when an undefined variable is referenced in prompt_template? → A: Detected as an error via static analysis during `fdsx validate` (pre-execution verification)
- Q: What happens when --thread-id is omitted? → A: A UUID is auto-generated and displayed in the terminal at execution start
- Q: When are uninstalled provider CLIs detected? → A: PATH is checked at `fdsx validate` and `fdsx run` startup, resulting in an error if not found
- Q: What happens with concurrent execution of the same thread_id? → A: Exclusive control via lock files. Subsequent attempts result in an error
- Q: What is the resume behavior when a checkpoint file is corrupted? → A: Stops with an error. The user decides whether to start a new execution
- Q: Quantification of "significantly reduced" for parallel execution? → A: Not needed. It is sufficient to confirm that "execution actually runs in parallel"
- Q: Is there an upper limit on the number of Parallel state branches? → A: No upper limit. At the user's own risk
- Q: How are input values passed to a flow? → A: Single tasks use `--input key=value`, batch tasks use `--tasks tasks.md` where an LLM auto-splits and executes sequentially
- Q: What happens when the maximum loop count is reached? → A: Stops as "loop completed" rather than an error. Resume continues to the next iteration
- Q: How is the system provider's exit status handled? → A: Non-zero exit codes are treated as failures and are subject to retry
- Q: What is the variable reference syntax in prompt_template? → A: Two syntaxes differentiated by use case. prompt_template uses `{variable}` (Python str.format()-style), result_path/aggregate/extract use `$.path` (JSONPath)
- Q: What are the data types for state variables? → A: Any JSON-compatible value (strings, numbers, objects, arrays). LLM output is a string, extract results are structured data, Parallel results are arrays
- Q: How deep is the LangGraph integration? → A: Full integration. YAML is compiled into a LangGraph StateGraph, delegating checkpoint, conditional branching, interrupt, and parallel execution to LangGraph
- Q: Which LLM is used for batch task splitting? → A: Explicitly specified in the YAML flow definition via the `task_splitter` field
- Q: What is the base directory for `.fdsx/checkpoints/` and `runs/`? → A: Relative paths based on CWD (current working directory at command execution time)
- Q: How is `context: inherit` realized? → A: `context: inherit` has been removed. All states use only `context: fresh` (explicit variable passing). `result_path` and `{variable}` references in `prompt_template` serve as the inter-state communication mechanism
- Q: What is the persistence format for state variables? → A: Checkpoint (FR-8) + structured logs (FR-11) are sufficient. No separate human-readable format such as Markdown is needed
- Q: Is extract's strategy a fixed chain or user-selectable? → A: User selects and specifies strategies as an array. Specify only what is needed, such as `strategy: [regex]` or `strategy: [json, keyword]`, and they are attempted in the specified order
- Q: How does `fdsx list` determine running/stopped status? → A: PID-based locking. The lock file records the PID, and process liveness is checked during `fdsx list`. Stale locks from crashes are also automatically detected
- Q: What is the structure of the `task_splitter` field? → A: Placed at the flow top level as `task_splitter: {provider, model}`. Reuses the same provider/model structure as Task states
- Q: What is the retry unit for Parallel branch failures? → A: Individual branch retry. Already successful branches are not re-executed
- Q: What is the default behavior when `on_reject` is unspecified? → A: Flow stops (checkpoint saved, resume possible)
- Q: What is the notification and response architecture for human feedback? → A: A new Wait state type is added. human_gate is removed and unified into Wait
- Q: What is the v1 support scope for Wait state? → A: `mode: prompt` (terminal prompt) only. timer/callback are planned for the future
- Q: What notification channels are supported? → A: Webhook only (Slack/Discord/Teams etc. are handled via webhook URLs). No dedicated Slack built-in is needed
- Q: What is the YAML structure for Wait state? → A: Has type: wait, mode, message, choices, notify (webhook), result_path, and next
- Q: What is the relationship with human_gate? → A: human_gate is removed and unified into Wait state. Mechanisms are consolidated

### Session 2026-03-15

- Q: Should per-state timeouts be set for CLI subprocesses? → A: `timeout_seconds` field optionally set in YAML. Default is none (unlimited). Timeouts are treated as errors and are subject to retry
- Q: How are `{` or `}` characters in variable values handled during `{variable}` expansion in prompt_template? → A: Safe custom replacement. Only `{name}` patterns that match registered variable names are expanded; unknown `{...}` patterns are preserved as literals
- Q: Can `--input` and `--tasks` be used simultaneously? → A: Mutually exclusive. Simultaneous specification results in a validation error
- Q: FR-8's custom file-based checkpoint and the full LangGraph integration (Assumption) checkpoint delegation contradict each other. Which is adopted? → A: LangGraph's SqliteSaver is used as the checkpointer. Exclusive control such as PID locks is additionally implemented on the fdsx side
- Q: What is the specific behavior of extract's json strategy? → A: First looks for a ```json...``` code block and parses it; if not found, applies JSON.parse to the entire output. After parsing, retrieves the field specified by pattern
- Q: What happens when webhook delivery fails in a Wait state? → A: Logged as a warning and the flow continues. The terminal prompt is displayed normally. Notification is an auxiliary feature
- Q: What is the value type for `--input key=value`? → A: All treated as strings. Sufficient since the primary use is in prompt_template
- Q: What is the naming convention for execution log files? → A: `runs/<thread_id>.json` format, tied to thread_id. On resume, appended to the same file
- Q: How are prompt_template and model handled for the system provider? → A: Uses the `command` field; `model` is not required. Variable references with `{variable}` are also available in command
- Q: What is the file reference syntax for prompt_template? → A: Uses the `prompt_file` field (mutually exclusive with `prompt_template`). Path is resolved relative to the workflow YAML file
- Q: What is counted toward max_loop? What is the behavior in flows with multiple loops? → A: Shared count across the entire flow. Counts the total number of transitions across all loop edges, consistent with LangGraph's recursion_limit
- Q: What is the object structure of each Parallel state branch result? → A: `{output, ...extracted}` format. Each element is an object with `output` (raw LLM output) + fields set by extract.result_path
- Q: What is the user confirmation UI after FR-13 batch task splitting? → A: Approve/reject only. If modifications are needed, edit the task file and re-run

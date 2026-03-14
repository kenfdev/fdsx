# Data Model: fdsx Framework

## Core Entities

### Flow (top-level workflow definition)

```
Flow
├── name: str                      # Required. Flow name
├── start_at: str                  # Required. Initial state name (must exist in states)
├── states: dict[str, State]       # Required. State definitions keyed by name
├── comment: str?                  # Optional. Flow description
├── version: str?                  # Optional. Flow version
├── task_splitter: TaskSplitter?   # Optional. Batch task splitting LLM config
└── max_loop: int = 10             # Optional. Max loop iterations (global counter)
```

### TaskSplitter

```
TaskSplitter
├── provider: str                  # Required. Provider name (claude/opencode/codex)
└── model: str                     # Required. Model name
```

### State (union type, discriminated by `type` field)

```
State = TaskState | ChoiceState | ParallelState | PassState | WaitState
```

### TaskState

```
TaskState
├── type: "task"                   # Literal
├── provider: str                  # Required. claude | opencode | codex | system
├── model: str?                    # Required for non-system providers
├── prompt_template: str?          # Exclusive with prompt_file
├── prompt_file: str?              # Exclusive with prompt_template
├── command: str?                  # For provider=system only
├── result_path: str               # Required. JSONPath ($.variable_name)
├── extract: ExtractRule?          # Optional. Output extraction config
├── retry: int = 3                 # Optional. Retry count
├── timeout_seconds: int?          # Optional. Subprocess timeout
├── next: str?                     # Exclusive with end
└── end: bool?                     # Exclusive with next
```

### ChoiceState

```
ChoiceState
├── type: "choice"                 # Literal
├── choices: list[ChoiceRule]      # Required. Condition-transition pairs
└── default: str?                  # Optional. Fallback transition
```

### ChoiceRule

```
ChoiceRule
├── variable: str                  # Required. JSONPath to compare
├── operator: str                  # Required. equals|not_equals|greater_than|less_than|contains
├── value: Any                     # Required. Comparison value
└── next: str                      # Required. Target state name
```

### ParallelState

```
ParallelState
├── type: "parallel"               # Literal
├── branches: list[Branch]         # Required. Parallel branch definitions
├── result_path: str               # Required. JSONPath for results array
├── min_success: int?              # Optional. Min successful branches (default: all)
├── next: str?                     # Exclusive with end
└── end: bool?                     # Exclusive with next
```

### Branch

```
Branch
├── provider: str                  # Required. Provider name
├── model: str?                    # Required for non-system providers
├── prompt_template: str?          # Exclusive with prompt_file
├── prompt_file: str?              # Exclusive with prompt_template
├── command: str?                  # For provider=system only
├── extract: ExtractRule?          # Optional. Output extraction
├── retry: int = 3                 # Optional. Branch-level retry
└── timeout_seconds: int?          # Optional. Branch-level timeout
```

### PassState

```
PassState
├── type: "pass"                   # Literal
├── parameters: dict[str, Any]?    # Optional. Variable transformation
├── aggregate: AggregateRule?      # Optional. Parallel result aggregation
├── next: str?                     # Exclusive with end
└── end: bool?                     # Exclusive with next
```

### AggregateRule

```
AggregateRule
├── source: str                    # Required. JSONPath to parallel results
├── field: str                     # Required. Field name to aggregate
├── strategy: str                  # Required. majority | all | any
├── match: str                     # Required. Match value
├── no_match: str                  # Required. Non-match value
└── result_path: str               # Required. JSONPath for result
```

### WaitState

```
WaitState
├── type: "wait"                   # Literal
├── mode: "prompt"                 # Required. v1: prompt only
├── message: str                   # Required. Terminal display message
├── choices: list[str]             # Required. User selection options
├── result_path: str               # Required. JSONPath for selection result
├── notify: NotifyConfig?          # Optional. Webhook notification
├── next: str?                     # Exclusive with end
└── end: bool?                     # Exclusive with next
```

### NotifyConfig

```
NotifyConfig
└── webhook: WebhookConfig
    ├── url: str                   # Required. Webhook URL
    └── template: str              # Required. Message template ({variable} refs)
```

### ExtractRule

```
ExtractRule
├── strategy: list[str]            # Required. [json, regex, keyword] - tried in order
├── pattern: str                   # Required. Pattern for extraction
├── fallback: LLMClassifyFallback? # Optional. 2-phase LLM classification
└── result_path: str               # Required. JSONPath for extracted value
```

### LLMClassifyFallback

```
LLMClassifyFallback
├── type: "llm_classify"           # Literal
├── provider: str                  # Required. LLM provider
└── prompt: str                    # Required. Classification prompt ({output} ref)
```

## Runtime Entities (not in YAML, created during execution)

### FlowExecution

```
FlowExecution
├── thread_id: str                 # UUID or user-specified
├── flow: Flow                     # Parsed flow definition
├── state_variables: dict          # Current variable bindings
├── current_state: str             # Current state name
├── loop_count: int                # Global loop counter
├── status: str                    # running | waiting | completed | error
└── history: list[StateExecution]  # Execution history
```

### StateExecution

```
StateExecution
├── state_name: str
├── started_at: datetime
├── completed_at: datetime?
├── duration_seconds: float?
├── input_variables: dict          # Snapshot of variables at entry
├── output: str?                   # Raw output (for Task/Parallel)
├── extracted: dict?               # Extracted values
├── status: str                    # success | error | skipped
└── error: str?                    # Error message if failed
```

### RunLog (persisted to runs/<thread_id>.json)

```
RunLog
├── thread_id: str
├── flow_name: str
├── started_at: datetime
├── completed_at: datetime?
├── status: str
├── state_executions: list[StateExecution]
└── final_variables: dict
```

## Relationships

```
Flow 1──* State
  TaskState 1──? ExtractRule
  ParallelState 1──* Branch
    Branch 1──? ExtractRule
  PassState 1──? AggregateRule
  WaitState 1──? NotifyConfig
  ChoiceState 1──* ChoiceRule
  ExtractRule 1──? LLMClassifyFallback

FlowExecution 1──1 Flow
FlowExecution 1──* StateExecution
FlowExecution 1──1 RunLog
```

## Validation Rules

1. `start_at` must reference a key in `states`
2. All `next` fields must reference keys in `states`
3. All `choices[].next` and `default` must reference keys in `states`
4. `prompt_template` and `prompt_file` are mutually exclusive
5. `next` and `end: true` are mutually exclusive
6. provider=system requires `command`, forbids `prompt_template`/`prompt_file`/`model`
7. provider=claude/opencode/codex requires `prompt_template` or `prompt_file`, forbids `command`
8. `prompt_template` variable references must be reachable via state transitions
9. `prompt_file` paths resolved relative to YAML file; must exist
10. At least one state must have `end: true` or the flow must be reachable to termination

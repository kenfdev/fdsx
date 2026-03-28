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
- Lifecycle hooks at flow and state level
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
description: "A simple greeting workflow"
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

## Feature Overview

### State Types
- **task** — Execute LLM or CLI commands with optional output extraction
- **parallel** — Run multiple branches concurrently with `min_success` threshold
- **choice** — Conditional routing based on variables
- **wait** — Pause for human input via terminal prompt with selectable choices
- **pass** — Pass-through state for data transformation and parallel result aggregation

### Parallel Execution
Define parallel branches that execute simultaneously. Use `min_success` to set how many branches must succeed. Aggregate results via a `pass` state with `aggregate` rules (majority, all, any).

### Checkpoint & Resume
Flows automatically persist state. Resume from interruption with:
```bash
fdsx resume --thread-id <thread_id>
```

### Batch Tasks
Process multiple tasks in batch mode (in-memory splitting):
```bash
fdsx run workflow.yaml --tasks tasks.md
```

Or use persistent batch execution with resume support:
```bash
fdsx split tasks.md
fdsx run --tasks-dir .fdsx/tasks/
```

### Profiles
Define named provider/model bundles in your workflow or config to avoid repetition:
```yaml
profiles:
  fast:
    provider: claude
    model: claude-haiku-4-5-20251001
  strong:
    provider: claude
    model: claude-sonnet-4-6

states:
  plan:
    type: task
    profile: fast
    prompt_template: "Plan the task: {task}"
    result_path: $.plan
    next: implement
```

### Hooks
Run shell commands before or after flow/state execution:
```yaml
hooks:
  on_start:
    - command: "echo 'Starting...'"
      on_failure: warn
  on_complete:
    - command: "echo 'Done!'"
```

### Output Extraction
Extract structured values from LLM output using `json`, `regex`, or `keyword` strategies with optional LLM classification fallback:
```yaml
extract:
  strategy: [keyword, regex]
  pattern: "APPROVED|REJECTED"
  result_path: $.decision
```

### Workflow Auto-Selection
When using `--tasks-dir` without specifying a workflow, fdsx discovers workflows from your workflows directory and uses an LLM to select the best match for each task.

### Structured Logging
Execution details are logged under `.fdsx/runs/<thread_id>/logs/`.

### Provider Support
Use any CLI-based LLM provider: Claude, Codex, Gemini, OpenCode, or system commands. Providers can be configured globally in `.fdsx/config.yaml` or per-task via `provider_options`.

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
| `fdsx run <workflow.yaml> --tasks tasks.md` | In-memory batch execution |
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

## Configuration

fdsx loads configuration from two levels (later wins):
1. Global: `~/.config/fdsx/config.yaml`
2. Project: `.fdsx/config.yaml`

```yaml
# .fdsx/config.yaml
profiles:
  default:
    provider: claude
    model: claude-sonnet-4-6

task_splitter:
  provider: claude
  model: claude-sonnet-4-6

workflow_selector:
  provider: claude
  model: claude-sonnet-4-6

workflows_dir: ".fdsx/workflows"
auto_workflow: false

providers:
  claude:
    permission_mode: auto
  codex:
    full_auto: true

hooks:
  on_start:
    - command: "echo 'Flow starting'"
      on_failure: warn
```

## Example Workflow

```yaml
name: Plan-Implement-Review Loop
description: "Iterative plan-implement-review cycle with LLM-based approval gating"
start_at: plan
version: "1.0"
max_loop: 3

states:
  plan:
    type: task
    provider: claude
    model: claude-sonnet-4-6
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
    next: check_review

  check_review:
    type: choice
    choices:
      - variable: $.review
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

## License

MIT License.
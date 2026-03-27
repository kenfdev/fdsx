# fdsx — Flow-Driven Stateful eXecution

[![PyPI version](https://img.shields.io/pypi/v/fdsx.svg)](https://pypi.org/project/fdsx/)

A lightweight framework for building and executing complex AI agent workflows using declarative YAML definitions.

## Overview

fdsx enables you to define AI agent workflows in YAML, combining the durability of LangGraph (checkpoint, interrupt, conditional routing) with the declarative structure of AWS Step Functions.

**Key features:**
- Declarative YAML-based workflow definition
- Stateful execution with checkpoint/resume
- Parallel execution with branch aggregation
- Batch task processing
- Multiple LLM provider support (Claude, OpenCode, and more)

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
- **task** — Execute LLM or CLI commands
- **parallel** — Run multiple branches concurrently
- **choice** — Conditional routing based on variables
- **wait** — Pause for human approval or webhook callback
- **pass** — Pass-through state for data transformation

### Parallel Execution
Define parallel branches that execute simultaneously with aggregation strategies (majority vote, all, any).

### Checkpoint & Resume
Flows automatically persist state. Resume from interruption with:
```bash
fdsx resume --thread-id <thread_id>
```

### Batch Tasks
Process multiple tasks in batch mode:
```bash
fdsx run workflow.yaml --tasks tasks.md
```

### Structured Logging
All execution details are logged to `runs/<thread_id>.json`.

### Provider Support
Use any CLI-based LLM provider: Claude, Codex, OpenCode, or system commands.

## CLI Reference

| Command | Description |
|---------|-------------|
| `fdsx run <workflow.yaml>` | Execute a workflow |
| `fdsx run <workflow.yaml> --input key=value` | Pass input variables |
| `fdsx resume --thread-id <thread_id>` | Resume from checkpoint |
| `fdsx validate <workflow.yaml>` | Validate YAML syntax |
| `fdsx list` | List recent runs |

## Example Workflow

```yaml
name: Plan-Implement-Review Loop
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

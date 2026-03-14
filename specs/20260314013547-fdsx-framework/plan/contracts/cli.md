# CLI Contract: fdsx

## Commands

### `fdsx run <workflow>`

Run a workflow from a YAML file.

```
fdsx run <workflow.yaml> [OPTIONS]

Arguments:
  workflow    Path to the YAML workflow file (required)

Options:
  --thread-id TEXT     Thread ID for this execution (default: auto-generated UUID)
  --input KEY=VALUE    Input variable (repeatable, e.g. --input task="fix bug" --input repo="myapp")
  --tasks FILE         Batch task file path (exclusive with --input)

Output:
  - Streams state transitions and LLM output to stderr
  - On completion: prints final JSON result to stdout
  - Exit code 0: success
  - Exit code 1: flow error (all retries exhausted)
  - Exit code 2: validation error (bad YAML, missing provider CLI)
```

### `fdsx resume --thread-id <id>`

Resume a stopped/interrupted flow from its last checkpoint.

```
fdsx resume [OPTIONS]

Options:
  --thread-id TEXT    Thread ID to resume (required)

Output:
  - Displays which state execution resumes from
  - Same streaming behavior as `fdsx run`
  - Exit codes same as `fdsx run`
```

### `fdsx validate <workflow>`

Validate a YAML workflow file without executing it.

```
fdsx validate <workflow.yaml>

Arguments:
  workflow    Path to the YAML workflow file (required)

Output:
  - Exit code 0: valid
  - Exit code 2: validation errors (printed to stderr with line numbers)
```

### `fdsx list`

List all known flow executions and their status.

```
fdsx list

Output (table format):
  THREAD_ID    FLOW_NAME    STATUS     CURRENT_STATE    STARTED_AT
  abc-123      my_flow      running    implement        2026-03-14 10:30
  def-456      my_flow      waiting    approval_gate    2026-03-14 09:15
  ghi-789      my_flow      completed  flow_end         2026-03-14 08:00
```

## Terminal Output Format

### State Transition Lines
```
[10:30:15] ▶ planner (task/claude/opus)
[10:31:42] ✓ planner completed (87s)
[10:31:42] ▶ implement (task/opencode/default)
```

### Parallel Execution Status
```
[10:32:00] ▶ parallel_review (parallel, 3 branches)
  [branch-1] claude/sonnet    ⏳ running...
  [branch-2] opencode/default ✓ completed (45s)
  [branch-3] codex/default    ⏳ running...
```

### Wait State Prompt
```
[10:35:00] ⏸ approval_gate (waiting for input)

  レビュー結果: APPROVED
  レビュー詳細: [review summaries...]
  承認しますか？

  [1] approve
  [2] reject
  [3] retry

  Select (1-3): _
```

### Error Output
```
[10:36:00] ✗ implement failed after 3 retries
  Error: subprocess exited with code 1
  Checkpoint saved. Resume with: fdsx resume --thread-id abc-123
```

## Run Log Format (runs/<thread_id>.json)

```json
{
  "thread_id": "abc-123",
  "flow_name": "plan_implement_review",
  "flow_version": "1.0",
  "started_at": "2026-03-14T10:30:15Z",
  "completed_at": "2026-03-14T10:40:00Z",
  "status": "completed",
  "states": [
    {
      "name": "planner",
      "type": "task",
      "started_at": "2026-03-14T10:30:15Z",
      "completed_at": "2026-03-14T10:31:42Z",
      "duration_seconds": 87,
      "status": "success",
      "output_preview": "First 500 chars of LLM output...",
      "variables_set": ["$.plan"]
    },
    {
      "name": "parallel_review",
      "type": "parallel",
      "started_at": "2026-03-14T10:32:00Z",
      "completed_at": "2026-03-14T10:34:30Z",
      "duration_seconds": 150,
      "status": "success",
      "branches": [
        {"index": 0, "provider": "claude", "status": "success", "duration_seconds": 120},
        {"index": 1, "provider": "opencode", "status": "success", "duration_seconds": 45},
        {"index": 2, "provider": "codex", "status": "success", "duration_seconds": 150}
      ],
      "variables_set": ["$.reviews"]
    }
  ],
  "final_variables": {
    "plan": "...",
    "implementation": "...",
    "reviews": [...],
    "decision": "APPROVED",
    "pr_url": "https://github.com/..."
  }
}
```

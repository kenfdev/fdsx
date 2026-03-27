# Self-Improve Workflow

Analyzes recent fdsx workflow runs to identify problems and write lessons learned to `.fdsx/LESSONS.md` for continuous improvement.

## Prerequisites

- fdsx installed
- Provider CLI available (configured provider for the `generalist` profile)
- Existing workflow runs in `.fdsx/runs/`

## Setup

1. Copy this directory to your workflows folder:

   ```bash
   cp -r examples/self-improve .fdsx/workflows/
   ```

2. Ensure your `.fdsx/config.yaml` has a `generalist` profile. Example:

   ```yaml
   profiles:
     generalist:
       provider: claude
       model: claude-sonnet-4-6
   ```

## Usage

From your project root:

```bash
fdsx run .fdsx/workflows/self-improve/workflow.yaml
```

The workflow will:
- Collect run data from `.fdsx/runs/` since the last analysis
- Analyze each run for problems (duration hotspots, failures, retries, aborts, loop issues)
- Research solutions for identified problems
- Write new lessons to `.fdsx/LESSONS.md` (with deduplication against existing entries)

## Files

| File | Description |
|------|-------------|
| `workflow.yaml` | 9-state workflow definition |
| `collect_data.sh` | Bash script that gathers run data from `.fdsx/runs/` |
| `analyze.md` | Prompt for analyzing run data and identifying problem categories |
| `research.md` | Prompt for researching solutions to identified problems |
| `write_lessons.md` | Prompt for composing lessons and updating LESSONS.md |

## How It Works

The workflow has 9 states:

1. **collect_data** — Runs `collect_data.sh` to gather run data since last analysis
2. **check_data** — Routes to `analyze` if new runs exist, otherwise `end_no_runs`
3. **analyze** — Uses `analyze.md` to identify problems and classify them (Prompts / Workflow / Rules)
4. **analyze_route** — Routes to `research` if problems found, otherwise `update_timestamp_clean`
5. **research** — Uses `research.md` to find solutions for each problem
6. **write_lessons** — Uses `write_lessons.md` to compose and merge lessons into LESSONS.md
7. **update_timestamp** — Updates last-run marker after successful analysis
8. **update_timestamp_clean** — Updates last-run marker when no problems found
9. **end_no_runs** — Ends cleanly when no new runs to analyze

## Output

Lessons are written to `.fdsx/LESSONS.md` in the project root, organized by workflow name and category (Prompts / Workflow / Rules).

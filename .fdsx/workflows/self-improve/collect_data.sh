#!/usr/bin/env bash
# Collects run data from .fdsx/runs/ for self-improvement analysis.
# Reads run.json from each run directory, filters based on last analysis timestamp,
# and outputs a pipe-delimited table with run information.

set -euo pipefail

RUNS_DIR=".fdsx/runs"
LAST_RUN_FILE=".fdsx/self-improve-last-run"
PENDING_FILE=".fdsx/self-improve-last-run.pending"
FDSX_DIR=".fdsx"

mkdir -p "$FDSX_DIR"

if [[ ! -d "$RUNS_DIR" ]]; then
    echo "NO_RUNS"
    exit 0
fi

last_run_dir=""
if [[ -f "$LAST_RUN_FILE" ]]; then
    last_run_dir=$(cat "$LAST_RUN_FILE")
fi

output_header="run_dir|flow_name|run_status|state_name|state_type|duration_s|state_status|retry_count"
output_lines=""
has_new_runs=false
newest_run_dir=""

while IFS= read -r run_dir; do
    run_json="${run_dir}run.json"
    if [[ ! -f "$run_json" ]]; then
        continue
    fi
    
    # Capture Python output — empty means run was filtered out
    py_output=$(python3 -c "
import json
import sys
import os

run_dir = sys.argv[1]
run_json = sys.argv[2]
last_run_dir = sys.argv[3] if len(sys.argv) > 3 else ''

with open(run_json, 'r') as f:
    data = json.load(f)

run_name = os.path.basename(run_dir.rstrip('/'))
flow_name = data.get('flow_name', 'unknown')
run_status = data.get('status', 'unknown')

if last_run_dir and run_name <= last_run_dir:
    sys.exit(0)

states = data.get('states', [])
for state in states:
    state_name = state.get('name', '')
    state_type = state.get('type', '')
    duration = state.get('duration_seconds', 0)
    state_status = state.get('status', '')

    logs_dir = os.path.join(run_dir, 'logs')
    retry_count = 0
    if os.path.isdir(logs_dir):
        import glob
        log_pattern = os.path.join(logs_dir, f'{state_name}_*.log')
        log_files = glob.glob(log_pattern)
        retry_count = len(log_files) - 1 if log_files else 0

    print(f'{run_name}|{flow_name}|{run_status}|{state_name}|{state_type}|{duration}|{state_status}|{retry_count}')

# If no states, still emit a run-level line so we know it passed the filter
if not states:
    print(f'{run_name}|{flow_name}|{run_status}||||0|0')
" "$run_dir" "$run_json" "$last_run_dir" 2>/dev/null || true)

    # Only count as new if Python produced output (run passed the date filter)
    if [[ -n "$py_output" ]]; then
        output_lines="${output_lines:+${output_lines}
}${py_output}"
        has_new_runs=true
        if [[ -z "$newest_run_dir" ]] || [[ "$run_dir" > "$newest_run_dir" ]]; then
            newest_run_dir="$run_dir"
        fi
    fi
done < <(ls -1d "$RUNS_DIR"/*/ 2>/dev/null | sort)

if [[ "$has_new_runs" == "false" ]]; then
    echo "NO_RUNS"
    exit 0
fi

echo "$output_header"
if [[ -n "$output_lines" ]]; then
    echo "$output_lines"
fi

if [[ -n "$newest_run_dir" ]]; then
    newest_name=$(basename "$newest_run_dir" | tr -d '/')
    # Write pending file for workflow's update_timestamp state
    printf '%s' "$newest_name" > "$PENDING_FILE"
    echo "NEWEST_RUN:${newest_name}"
fi

echo "HAS_RUNS"

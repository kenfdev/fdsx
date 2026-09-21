# Bounded map execution

A map's `max_concurrency` counts active items, including internal work and retry
waits. Omitted or explicit `1` keeps sequential execution. The value must be a
positive integer; floats (even `2.0`), booleans, strings, zero, negatives and
unlimited values are rejected before execution. It is not a workflow-wide or
process-count limit. Each free slot takes the next unstarted input index. Actual
process starts and log arrival need not follow that assignment order.

Both iterator forms use the same setting:

```yaml
work:
  type: map
  items_path: $.items
  max_concurrency: 2
  iterator:
    states:
      - name: echo_item
        provider: system
        command: echo {item}
        result_path: $.value
        retry: 0
  result_path: $.results
  end: true
```

```yaml
work:
  type: map
  items_path: $.items
  max_concurrency: 2
  iterator:
    start_at: echo_item
    output_path: $.value
    states:
      echo_item:
        type: task
        provider: system
        command: echo {item}
        result_path: $.value
        retry: 0
        end: true
  result_path: $.results
  end: true
```

Items retain their internal task order, local routing and loop state. Results
remain in input order: legacy items export the last task output, and local items
export `index`, `exit_code`, `error`, `output`. Empty inputs yield `[]`. A limit
larger than the input starts only existing items.

`fail_fast: true` stops new starts when a final failure is detected, then waits
for running items and saves their successes. It does not kill them for an
ordinary failure. If several fail, the lowest input index supplies the primary
error; all failure reasons remain recorded. `fail_fast: false` collects all
handled failures: legacy iterators save `null` and ultimately fail, while local
workflows can succeed with failure envelopes. Infrastructure/save errors always
stop new starts, take precedence over ordinary failures, and drain running work,
still attempting its saves. An interrupt takes precedence over waiting: process
groups receive the existing graceful-stop/kill policy, and no subsequent task or
retry starts. Run finalization and lock release follow worker shutdown.

Each completed item is saved without waiting for earlier indices. Ordinary resume
reuses saved results, including collected failures under `fail_fast: false`.
Fail-fast failures and unfinished/unsaved items restart at their first step.
Earlier sequential progress remains readable. A new map visit or explicit
recovery starts fresh; changing inputs uses the existing `--from` recovery path.
Side effects completed before saving can repeat on resume.

The working directory is shared. Managed result files are item-scoped, but users
must avoid writing the same arbitrary file from concurrent items. This adds no
new nested state types, session inheritance, workspace isolation or automatic
item-level retries. Logs identify map, visit, execution and zero-based item index;
terminal progress displays one-based indices. Infrastructure diagnostics appear
in the map record's `execution_errors`, separately from reusable completions.

## Repeatable offline CLI verification

From the repository root run:

```sh
rtk proxy uv run python scripts/verify_concurrent_map.py
```

The driver runs the committed `tests/fixtures/concurrent_map/{legacy,local}.yaml`
workflows through the CLI with `item.sh`, which only writes markers, waits for
release files, and echoes results in a newly created temporary directory. Each
case gets its own empty `.fdsx` directory and `XDG_CONFIG_HOME`; no user hooks,
LLM binaries or external services are used. Evidence remains under the printed
`/tmp/fdsx-concurrent-map-*` directory, including `cli.log`, item PID start/end
markers, `progress.json`, `run.json`, and `results.json`.

For both forms the driver checks:

- A and B start while C and D cannot start (limit two).
- Releasing B starts C while A remains blocked; releasing C starts D. B/C/D
  become durably saved before A finishes. Final results remain in input order.
- B's terminal failure prevents C/D starting, but A finishes and is saved.
  Resume skips A and runs the failed/unstarted items.
- SIGINT with A unfinished preserves saved B/C/D, exits 130, stops A's process,
  finalizes an interrupted record and releases the checkpoint lock. Resume runs
  A from the beginning without rerunning B/C/D.

Waits have deadlines and failure cleanup signals only the test CLI. These gates,
not elapsed-time speedups, establish overlap, slot refill and recovery behavior.

## Execution design

Threads reuse synchronous internal task execution and local LangGraph invocation.
Each submission receives a separate copied context (flow logging, cancellation,
attempt observer) and each item has a deep-copied variable context. Only up to the
configured number of items are submitted; completion-driven refill replaces
fixed batches. A scheduling lock orders stop observation and admission. Worker
exceptional exits close admission in `finally`, and Future exceptions are
inspected after draining rather than broadly caught as ordinary failures.

The implementation reuses 01's MapProgress atomic publication and recorder locks.
Completion publication is serialized, but execution is not. Execution-scoped
cancellation reaches retry waits and the common subprocess boundary (including
extraction fallback). A process registered after a signal snapshot is stopped by
its registering worker. The engine retains checkpoint ownership until LangGraph
and the map executor have drained. Known task failures have a distinct internal
exception type so unexpected local-runtime errors do not become collected item
failures; the established local result envelope remains unchanged.

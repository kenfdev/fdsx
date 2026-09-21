# Resume and recovery

Use this reference when resuming maps, replacing saved execution inputs, choosing approval behavior, or inspecting recovery history. Recovery target rules are in `../SKILL.md` under **Recover after a terminal non-success outcome**.

## Map item recovery

Ordinary `fdsx resume --thread-id <id>` reuses durably collected items in the
current map visit for both iterator forms. Items are keyed by zero-based input
index, including noncontiguous indices saved by concurrent execution. Each item
is saved as it finishes, without waiting for earlier indices. Results remain in
input order. Unfinished or unsaved items restart at their first step, not their
last internal task; local workflows have no internal step checkpoints.

- With `fail_fast: true`, failed items are recorded for diagnostics but retried on ordinary resume.
- With `fail_fast: false`, saved failures are collected results and reused rather than retried.
- A new visit to the map starts fresh. Explicit recovery with `--from` invalidates map progress; use `--from <map>` to rerun that map.
- Ordinary resume assumes unchanged inputs. To revise them, use explicit `--from <map> --input ...` recovery and the approval rules below.
- An external side effect completed before progress was saved can repeat. Item persistence does not guarantee exactly-once effects.

Versioned progress stores the map visit, collected results and their statuses.
Older contiguous progress remains readable without rewriting it; the next
successful save migrates it. An old `null` with indeterminate status remains
collected as `unknown`, is not retried, and counts as neither known success nor
known failure. Newly saved failures retain their status across resume.

### Map diagnostics

Terminal progress uses one-based item numbers. Result `index` and run-record
`item_index` use zero-based numbers. Item logs are stored under
`logs/<map>/<visit>/<execution-id>/<item-index>/`; each invocation, including
resume, gets a fresh execution ID so earlier logs remain intact. Local state
names and managed result-file locations retain their existing scope.

Map entries in `run.json` distinguish:

- `completed_count`: durably collected items, including reused items.
- `reused_count` and `executed_count`: saved items reused and items started this invocation.
- `success_count`, `failure_count`, `unknown_count`: statuses of collected results.
- `attempt_count` and per-item `task_attempts`: task-provider attempts, including retries, this invocation; local routing/pass states do not count.
- `execution_errors`: infrastructure diagnostics, separate from reusable completions.

Reused items create no new task execution or attempt records. A save failure
fails the map and leaves that item eligible for execution on resume. For
concurrency limits and failure scheduling, see [MapState](yaml-schema.md#mapstate).

## Prepare the recovery

```bash
fdsx resume --thread-id <id> --from review --input 'task=Revised requirements'
```

- `--input KEY=VALUE` is repeatable and requires `--from`, even when values are unchanged.
- Only keys recorded in the checkpoint's input-key metadata are accepted. Missing metadata is an error; fdsx does not infer input keys from results.
- Each submitted value replaces the whole saved value as a string. Unspecified inputs stay unchanged. Values may contain `=`; repeated keys use the last value.
- The restart state must satisfy the normal recovery rules. Required variables are checked against the proposed inputs before approval.
- Retained results may reflect old requirements. Select a restart state that reruns the checks needed for the revised inputs; recovery does not invalidate all earlier results automatically.
- Ordinary resume and explicit `--from` recovery preserve native session references, including with input changes. For `fork_from` workflows, choose a destination to fork its source's last successful session or choose the source to regenerate it. Input changes do not rewrite saved conversation history. Destination retries create new child sessions rather than continue the previous attempt; missing native history still requires rerunning the source.

## Obtain approval

The terminal displays line differences, the restart state, and a warning about retained results. Interactive confirmation is required by default.

For an explicitly approved unattended recovery:

```bash
fdsx resume --thread-id <id> --from review --input 'task=Revised requirements' --yes
```

- Without a terminal, input updates require `--yes`. Otherwise the command exits with code 1.
- Cancellation or unavailable approval leaves saved execution data unchanged and runs no states or hooks.
- `--yes` approves only the submitted inputs and restart target. It does not bypass validation or the thread lock, and does not answer wait-state choices or change ordinary resume behavior.
- Malformed input without `=` exits with code 2.

Python callers pass `input_updates` and a `confirm_inputs(old_inputs, submitted_inputs, target)` callback to `resume_flow(..., from_state=...)`. The callback must return true to approve. It receives copies under the thread lock, after validation and before saved-state changes. Missing or false approval cancels the update.

## Preserved history

Accepted updates remain current on later resumes of the same thread.

- New executions retain initial inputs in `_meta.initial_inputs`.
- Changed inputs create snapshots under `<base-dir>/runs/<id>/revisions/`, linked from `_meta.input_revisions`.
- Each snapshot preserves full pre-update checkpoint values and metadata, the prior run record, the effective inputs, and copies of fdsx-managed result files.
- Identical submitted inputs still require approval and record a recovery attempt. Their snapshot is linked from `_meta.recovery_snapshots`, not a new input revision.
- After the first input revision, explicit `--from` recovery without new inputs also archives the values and managed files it is about to replace.
- Older executions preserve only values and files still available at the first update. Already overwritten history cannot be recovered.

The snapshot is published before one SQLite checkpoint update stores both accepted inputs and the history link under the thread lock. A process exit before that update leaves old inputs current; an unreferenced snapshot or `.pending` directory is not an accepted revision. After commit, new inputs and their history link remain together. Retry interrupted recovery with an explicit `--from`. Missing archives are not reconstructed.

## File and queue boundaries

Tasks-directory resume keeps the original task entry and thread, with normal status updates. It does not start another batch, rewrite task descriptions, or import edited task descriptions or source files into saved inputs. Submit replacements explicitly.

Files written by external tools are not archived. Editing a file behind an unchanged input path does not import its contents into checkpoint inputs.

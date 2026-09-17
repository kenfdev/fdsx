# Resume with revised inputs

Use this reference when replacing saved execution inputs, choosing approval behavior, or inspecting recovery history. Recovery target rules are in `../SKILL.md` under **Recover after a terminal non-success outcome**.

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

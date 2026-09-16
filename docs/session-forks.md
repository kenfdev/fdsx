# Native Pi forks from ordinary tasks

`fork_from` names a top-level task in the same workflow. For example:

```yaml
name: plan-implement-review
description: Implement and independently review a plan
start_at: plan
states:
  plan:
    type: task
    provider: pi
    model: anthropic/claude-sonnet-4-6
    prompt_template: Plan the requested change.
    next: implement
  implement:
    type: task
    provider: pi
    model: anthropic/claude-sonnet-4-6
    fork_from: plan
    prompt_template: Implement the plan in the current working directory.
    next: review
  review:
    type: task
    provider: pi
    model: openai/gpt-5.4
    fork_from: plan
    prompt_template: Review the current code against the plan.
    result_path: $.review
    end: true
```

Use model IDs available in your Pi installation. Pi controls model availability,
credentials, cross-model message conversion and context compaction. Model vendors
such as `anthropic` and `openai` are not different FDSX providers: both tasks above
use `pi`. Same-provider retry model escalation is supported.

Implementation and review receive separate native children of planning's completed
conversation. Review sees the current working directory, including implementation's
file changes. A fork does not restore files, create a worktree, or isolate tools.
Every execution retry and structured-output retry creates another independent
child of the originally selected source endpoint. Bounded validation feedback still
applies; failed child history does not carry over. File changes are never undone.

## Validation

Only ordinary Pi tasks are supported initially. Other providers, system tasks,
parallel branches and map iterator destinations reject `fork_from`. Sources must
be top-level ordinary tasks; branch names, iterator names, paths and external
session IDs are not source references. A forked task may itself be a source.

The source must strictly precede the destination on every path from entry,
including the first visit to a loop. A shared ancestor before a split is valid
after the join. A task in only one optional branch is not. Self references and
unreachable fork destinations are rejected. Unreachable incoming paths do not
invalidate an otherwise mandatory ancestor.

Profiles resolve before these checks. Effective workflow/config retry escalation
must retain provider `pi` on both endpoints; use `retry_escalation: false` to
explicitly disable an incompatible inherited policy. Validation does not execute
Pi and cannot guarantee that its runtime, selected models or native files exist.

## Native interface and storage contract

The supported baseline is **Pi 0.85.1** (session format v3), not a claim that every
earlier version lacks forks. FDSX checks the version and fails closed if the
required native interface or saved history is unavailable.

FDSX invokes a bundled local extension in a separate, prompt-free Pi subprocess.
The extension calls Pi's documented `SessionManager.forkFrom` with the current
working directory, then `createBranchedSession(completedEntryId)` to select the
completed endpoint. Pi owns both native operations. The task then runs using
`pi -p --session <child> --session-dir <directory>`. The preparation process has
discovery disabled and exits before task execution. No conversation is copied,
replayed or reconstructed by FDSX. The ordinary CLI `--fork` alone is insufficient
because it selects the source's current end, which could have advanced.

Sessions live beneath Pi's agent directory (`PI_CODING_AGENT_DIR`, otherwise
`~/.pi/agent`), in unique `sessions/fdsx-*` directories. Keep these directories,
including native intermediate forks, for as long as the workflow needs them.
FDSX does not delete them. Session selection and preparation metadata never become
task output; non-fork workflows retain their existing provider invocation.

Checkpoint references contain only provider identity, absolute native path,
session ID, completed entry ID, byte count and SHA-256 of the completed file.
Later appends can be present: native branch selection still stops at the saved
entry. Changes to the completed prefix, missing files, corrupt trees, incompatible
versions and missing metadata fail closed. Trailing label entries are normalized
to their preceding conversation endpoint because Pi regenerates labels in forks.
Do not concurrently rewrite native history while it is being forked.

## Completion and recovery

Only a successful task whose output passed extraction/structured validation
publishes a reference, together with its normal checkpoint update. Replanning
replaces the reference after each successful completion. A failed replan stops
execution under the existing failure rules; it does not select an older plan.

An ordinary interrupted resume restores references and can fork planning without
rerunning it. Old checkpoints lacking required metadata, or checkpoints referring
to unavailable Pi data, fail with the affected state and recovery guidance.
Explicit recovery (`resume --from`) clears all saved session references. Choose
a recovery target that reruns required sources; jumping directly to a destination
fails closed. This prevents skipping a failed replan and using its older context.

The SQLite checkpoint is a reference, not a backup of the native conversation.
Retain accessible Pi files at their recorded paths when moving or resuming a run.
Crashes after native execution but before output validation/checkpoint publication
can leave orphan sessions or cause a task to execute again. Exactly-once provider
execution and recovery from deleted native history are not promised.

## Interface evidence and test limits

The local Pi 0.85.1 distribution was inspected without invoking a provider:

- `docs/session-format.md`: v3 trees, stable entry IDs, storage, `forkFrom`,
  `createBranchedSession`, `getBranch` and session metadata.
- `docs/sdk.md`: native branching and session manager selection.
- `docs/usage.md` and `docs/extensions.md`: explicit local extensions with
  discovery disabled; `--session` and `--session-dir`.
- Embedded source in the distributed `pi` executable: `SessionManager._buildIndex`
  restores the final append as leaf; `forkFrom` changes cwd and session identity;
  `createBranchedSession` selects the ancestor path and regenerates labels while
  preserving compaction boundaries. `transformMessages` handles upstream model
  changes natively, including thinking signatures and tool-call identifiers.

Automated tests use synthetic native-format files and mocked provider subprocesses.
They establish FDSX routing, retries and persistence, not installed Pi semantics or
model quality. Real-provider verification requires separate explicit approval.

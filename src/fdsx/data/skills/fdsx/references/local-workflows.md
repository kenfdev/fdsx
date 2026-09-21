# Local workflows in map and parallel

New local definitions have `start_at`, a mapping `states`, required concrete
`output_path`, and optional positive `max_loop` (otherwise inherited from Flow).
Map places this definition under `iterator`; parallel places it under a branch's
`workflow`, alongside optional `name`. Legacy list iterators and ordinary/classifier
branches retain their syntax, defaults, output shapes and resume behavior.

Local states are task (including Jev), classifier, evaluate, choice, fail, and pass.
Pass supports local data shaping and successful termination without a provider call.
Wait/map/parallel are rejected. Start and transitions must reference local names;
task/pass require exactly one next or end:true. Choice uses its existing implicit
terminal when default is absent; fail terminates on entry. At least one terminal
path must be reachable from the local start. Local names are ASCII
identifiers (hyphens also allowed), for unambiguous scoped recording paths.
Local fork_from is rejected: native session ancestry across these new boundaries
is not defined. Legacy forks remain supported.

Existing compiler/node factories compile the local graph without a checkpoint saver.
Internal and recorded names are `parent.items.INDEX.step` or
`parent.branches.INDEX.step`. Result paths are unchanged. Each execution receives
a deep copy of parent user variables, plus item for map, and fresh internal counters
and session state. Writes cannot leak into siblings or parent; only output_path is
exported. Existing hooks, evaluation diagnostics and SDK retry constraints apply.
Run records keep scoped local steps under `local_workflows`, separate from the
outer terminal-state history. Structured branch exports are retained as results;
the terminal displays their branch status without rendering their contents.

Managed task `result_file` artifacts use a stable directory per item/branch under
`<run_dir>/local-results/<scope SHA-256>/data/`. Parent artifacts remain under
`<run_dir>/data/`. The same local variable is replaced on a later execution of
that subject, including replay; sibling and parent artifacts are preserved.
`run_path` and hook run-directory context still refer to the original run.
This isolation concerns managed result files, not arbitrary provider file writes.

max_iterations retains check-before-execution entry counts for states that already
support it. Evaluate/classifier retain their field restrictions. max_loop retains
the compiler's back-edge target visit counting, using independent local counters.
Both limit outcomes become local failures. No parent container is replayed.
Explicit fail and provider/evaluation failures are also collected; interruptions
propagate. A missing output_path on successful completion is a local failure.

New map results are ordered envelopes:
`{index: 0, exit_code: 0, error: null, output: ...}` or
`{index: 1, exit_code: 1, error: max_loop_reached, output: null}`.
Index identifies the input without copying it into diagnostics. Parallel preserves
its ordered/name-based identification and uses exit_code/error/output with name.
Other failure envelopes contain the error category, not evaluation materials;
explicit fail keeps its declared error name.

Map `max_concurrency` applies to local workflows as well as legacy iterators.
For limits, scheduling and shared-file safety, see [MapState](yaml-schema.md#mapstate).
Map `fail_fast: true` stops new starts on final failure, then waits for running
items and saves their successes. With false all items finish. A
following pass.aggregate over exit_code, strategy:all, match:"0", no_match:"1",
then choice/fail demands all success; omitting that policy accepts partial results.
Parallel collects all branches and retains min_success's default of all branches.
A lower threshold accepts partial results. Gate can reference a named workflow
branch through $.output.approved; failed execution/missing field fails, value
mismatch produces false. Gate and min_success remain mutually exclusive.

Map saves item envelopes within the parent map visit, even out of input order.
For reuse of saved successes/failures, restart boundaries and explicit recovery,
see [Map item recovery](resume.md#map-item-recovery).
Parallel retains its existing branch/collector checkpoint behavior; a pending
branch can replay its complete local graph. There are no local step checkpoints
or new completed parallel branch reuse guarantees.

### YAML examples

Legacy examples (state fragments):
```yaml
# map
items_path: $.items
iterator:
  states:
    - name: build
      provider: system
      command: echo built
      result_path: $.draft
# parallel
branches:
  - name: build
    provider: system
    command: echo built
```

New example: the anchor below is expanded into either iterator or branch.workflow.
Classifier and Jev task can replace assess using their existing definitions and
their corresponding choice result path.
```yaml
name: local-review
description: Repair each item independently
start_at: work
states:
  work:
    type: map
    items_path: $.items
    fail_fast: false
    result_path: $.outcomes
    iterator: &local
      start_at: generate
      max_loop: 3
      output_path: $.draft
      states:
        generate:
          type: task
          provider: system
          command: echo initial
          result_path: $.draft
          next: assess
        assess:
          type: evaluate
          evaluator: jev
          input: {draft: {ref: $.draft}}
          questions:
            action:
              type: choice
              instructions: Choose the required action.
              criteria: {approved: Complete, prose: Revise prose, code: Revise code}
          result_path: $.assessment
          next: route
        route:
          type: choice
          choices:
            - {variable: $.assessment.answers.action.choice, operator: equals, value: approved, next: done}
            - {variable: $.assessment.answers.action.choice, operator: equals, value: prose, next: fix_prose}
          default: fix_code
        fix_prose:
          type: task
          provider: system
          command: echo revised-prose
          result_path: $.draft
          next: assess
        fix_code:
          type: task
          provider: system
          command: echo revised-code
          result_path: $.draft
          next: assess
        done: {type: pass, end: true}
    next: require_all
  require_all:
    type: pass
    aggregate: {source: $.outcomes, field: exit_code, strategy: all, match: "0", no_match: "1", result_path: $.failed}
    next: finish
  finish:
    type: choice
    choices:
      - {variable: $.failed, operator: equals, value: "0", next: done}
    default: rejected
  done: {type: pass, end: true}
  rejected: {type: fail, error: PartialFailure, cause: Some items failed}
```

To accept partial map results, use end:true instead of work.next. For parallel,
replace work with the following (expand the same local anchor twice). Omit
min_success to require every branch after collection, or set it to 0 to accept all
failures. Remaining branches execute in all these cases.
```yaml
work:
  type: parallel
  branches:
    - {name: first, workflow: *local}
    - {name: second, workflow: *local}
  min_success: 1
  result_path: $.outcomes
  end: true
```

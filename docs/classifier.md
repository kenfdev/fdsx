# Classifier

A `classifier` answers one choice question with Jev. It accepts the selected
maximum-probability candidate if the applicable conditions pass; otherwise it
asks one configured LLM. A separate `choice` state selects the next workflow step.
Classification never grants permission to commit, deploy, or perform other actions.

```yaml
name: classify-review
description: Choose the next action from a review
start_at: classify
profiles:
  judge:
    provider: claude
    model: claude-sonnet-4-6
states:
  classify:
    type: classifier
    evaluator: jev
    input:
      review:
        literal: Tests pass; review found a missing edge case.
    question:
      type: choice
      instructions: Which action should follow this review?
      criteria:
        commit: All required work and checks are complete.
        fix: Required work or checks remain.
    acceptance:
      probability: 0.6
      probability_by_choice:
        commit: 0.8
      confidence: 0.7
      mode: all
    fallback:
      profile: judge
      include_jev_result: false
    result_path: $.decision
    next: route
  route:
    type: choice
    choices:
      - variable: $.decision.answer
        operator: equals
        value: commit
        next: ready
    default: repair
  ready:
    type: pass
    parameters:
      $.next_action: commit
    end: true
  repair:
    type: pass
    parameters:
      $.next_action: fix
    end: true
```

## Declaration and acceptance

`input` uses the same named `{literal: ...}` / `{ref: $.path}` materials as
`evaluate`. Reference values are resolved when the classifier executes; literal
braces are not templates. `question` is a single `EvaluationQuestion` with
`type: choice`, nonblank instructions and at least two candidate criteria.
Candidate identifiers match `[A-Za-z_][A-Za-z0-9_]{0,127}`. Rule text may use
any language. Multiple questions, score and noul are not supported here.

`model` selects Jev and defaults to `jev-1.13.0`. `evaluator` defaults to `jev`.
The top-level `result_path` must name one non-reserved top-level variable, as for
`evaluate`. Top-level classifiers require exactly one of `next` or `end: true`;
state start/end hooks are supported with summary-only hook payloads.

All thresholds are finite numbers from 0 through 1; booleans are rejected.
`probability_by_choice` overrides `probability` only for the first candidate
selected by Jev. Unknown candidate names are configuration errors. Probability
and confidence are separate service measurements; neither is a completion score.
Equality with a threshold passes. Tied maximum candidates retain Jev's selection.
No requirement that probabilities sum to one is added.

`mode: all` is the default; `any` accepts if any applicable condition passes.
With one applicable condition both modes test that condition. With no applicable
conditions both modes accept Jev. A candidate without an override uses the common
probability threshold, if present; other candidates' thresholds are never tested.
There is no automatic selection of a runner-up.

Any configured threshold, including an override for a different candidate,
requires `fallback` before execution starts. With no thresholds, `fallback` is
allowed but is never called and does not generate an unused-setting warning.

## LLM fallback and failures

Use `fallback.provider` plus `fallback.model`, or a workflow/project/global
`profile` with a supported LLM provider. `system` and `jev` are not LLM fallback
providers. `provider_options` overrides profile options; the classifier does not
inherit ordinary task prompts, extraction fallback, or retry escalation.
`timeout_seconds` defaults to 1800 (positive integer); `inactivity_timeout`
defaults to 300 (nonnegative integer; zero disables inactivity detection).
These timeout fields govern the LLM, not Jev.

Jev retains the shared SDK configuration: 30-second request timeout, at most two
SDK retries, and a 60-second retry budget. Only a valid but below-threshold Jev
answer causes LLM fallback. Communication failures and malformed Jev answers fail
the classifier. Invalid answer content is not retried.

The LLM prompt is generated from the resolved materials and the same question and
criteria. `include_jev_result: true` also includes Jev's choice, probabilities and
confidence; the default is false. The LLM must return one JSON object containing
exactly `answer` (a configured candidate) and `reason` (a string). There is no
probability/confidence field and no second acceptance test.

The reason is stripped of terminal controls, flattened to one line and trimmed.
It must then contain 1–500 printable characters. Empty, overlong, missing, wrongly
typed, unknown-candidate and extra-field responses fail. Invalid JSON fails;
there is no repair request, automatic retry, alternate-model chain or return to
the rejected Jev answer. Transport failures and timeouts also fail after one LLM
invocation. Native provider schema support is supplemented by local validation.

## Results and parallel execution

Both sources write the same result:

```json
{
  "answer": "fix",
  "source": "llm",
  "reason": "The review identifies missing edge-case coverage.",
  "jev": {
    "validated": true,
    "answer": "commit",
    "probabilities": {"commit": 0.75, "fix": 0.25},
    "confidence": 0.8,
    "mode": "all",
    "conditions": {
      "probability": {"value": 0.75, "threshold": 0.8, "passed": false},
      "confidence": {"value": 0.8, "threshold": 0.7, "passed": true}
    },
    "accepted": false,
    "model": {"requested": "jev-1.13.0"}
  }
}
```

For Jev adoption, `source` is `jev` and `reason` is null. The `jev` object always
describes the original Jev answer and applicable conditions, even when the LLM
chooses a different candidate. Only completed, validated classifications are
published at `result_path`.

Parallel branches can declare `type: classifier`, the same input/question/
acceptance/fallback fields, an optional `name`, and a branch-local `result_path`.
They have no `next`, `end` or branch hooks. For example:

```yaml
name: independent-reviews
description: Classify independent review questions in parallel
start_at: reviews
states:
  reviews:
    type: parallel
    branches:
      - type: classifier
        name: security
        input:
          review: {literal: No security issues were found.}
        question:
          type: choice
          instructions: Does this review require security work?
          criteria:
            ready: No required security work remains.
            fix: Security work remains.
        result_path: $.decision
      - name: local_check
        provider: system
        command: echo checked
    result_path: $.reviews
    gate:
      required: [security]
      field: $.decision.answer
      expected: ready
      result_path: $.ready
    end: true
```

Results retain existing branch metadata (`name`, `exit_code`, `error`) and put the
classification under the branch's result path. Branch paths cannot overwrite
`name`, `index`, `exit_code`, `error`, `output` or `_duration`. Existing task branch
declarations are unchanged. `min_success` and required/advisory gate behavior are
unchanged; a classifier is a valid structured gate source without a user schema
file. A failed classifier contributes a failed branch, never a successful answer.
Map iterators remain task-only. Existing Jev task/evaluate placement restrictions
remain unchanged.

An interrupted or failed classifier that is executed again starts at Jev. No
Jev-complete/LLM-pending checkpoint is added, so reassessment can incur extra cost.
Completed classifications are reused by ordinary downstream resume. Explicit
`resume --from <state>` follows the existing recovery contract. For collected
parallel failures, use `--from <parallel-state>` to rerun branches; bare resume
can retry the pending collector using saved branch results. An abrupt interruption
before run metadata was saved may require supplying the workflow path to resume.

## Records and private materials

Each invocation has a unique attempt identifier. State and branch location,
acceptance conditions, original measurements, fallback and validated LLM reason
are recorded in `run.json` under `classifier_events`, and in ordinary
`logs/classifier-<attempt>.jsonl`. Fallback and reason messages also go to stderr.
`--quiet` suppresses those terminal messages but retains both records. No classifier
messages go to machine-readable stdout. Provider raw stream diagnostics and raw
stdout/stderr are not copied into classifier logs.

Invalid Jev diagnostics carry `validated: false`, availability and allowlisted
numeric fields for the declared question and candidates. No raw choice, unknown
key, free text or exception body is copied. Booleans and strings are unavailable;
finite range violations carry `valid_range: false`, nonfinite values use the
fixed string `nonfinite`, and magnitudes above 1e100 use `out_of_range`. Bounded
JSON exception bodies can supply metrics; unavailable or unparseable bodies are
reported as unavailable. The SDK may reject data before exposing these values;
the record is not a guarantee that every malformed value can be recovered.

`record_full_input: true` enables a separate private JSON file at
`classifier-inputs/<attempt>.json` inside the run directory. The default is false.
The directory is mode 0700 and files are 0600. The file records resolved materials,
Jev model/question data, and, when fallback starts, the generated LLM prompt and
output schema. It is atomically replaced when the prompt is added; one attempt
has at most one file. The total JSON limit is 1 MiB; overflow or inability to write
fails the classifier with a safe error. Parallel attempts use distinct files.

These snapshots describe application-level request data, not SDK HTTP headers,
credentials, provider-added schema guidance or complete native CLI history. They
are not guaranteed wire-level reproductions. There is no automatic expiry: keep
them according to your retention policy and delete the specific private JSON file
or its owning run directory when no longer needed. Deletion does not remove
independent checkpoints or provider-owned history.

Even with full-input recording disabled, a validated LLM reason may quote input;
the reason is intentionally retained in results, normal logs and the non-quiet
screen. This setting does not disable existing workflow input/checkpoint storage,
hooks elsewhere in the workflow, or provider-owned local history.

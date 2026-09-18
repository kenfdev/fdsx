# Jev evaluation

## Choose an interface

- `type: task`, `provider: jev`: evaluate the resolved prompt against a supported JSON Schema. Change provider/model to use an ordinary LLM with the same prompt, schema, saved fields, and routing.
- `type: evaluate`, `evaluator: jev`: evaluate explicit named materials against inline questions. Save the full validated answer envelope.

Both are top-level only, including when Jev is selected through a profile. Use a following `choice` state to route; evaluation itself neither performs actions nor grants permissions. Questions in one request are independent.

The bundled `typesafe-sdk` calls `https://api.typesafe.ai`; no Jev CLI or extra install is needed. Supply `TYPESAFE_API_KEY` through the environment without printing it or putting it in workflow files. Workflows without evaluation need no key and make no Jev requests.

## Schema-based task

```yaml
name: Review evaluation
description: Assess a review against requirements
start_at: assess
states:
  assess:
    type: task
    provider: jev
    model: jev-1.13.0
    prompt_template: "Review: {review}\nRequirements: {requirements}"
    structured_output:
      schema: decision.schema.json
      result_path: $.assessment
      allow_extra_fields: false
    end: true
```

Place `decision.schema.json` beside the workflow:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["action"],
  "properties": {
    "action": {
      "type": "string",
      "description": "Decide whether the review meets the requirements.",
      "oneOf": [
        {"const": "fix", "description": "Blocking problems remain."},
        {"const": "proceed", "description": "No blocking problems remain."}
      ]
    }
  }
}
```

The saved value is `{"action": "fix"}` or `{"action": "proceed"}`. Route on `$.assessment.action`, not the legacy answer envelope.

The prompt is resolved once and sent as one material. Missing/null references and a wholly blank prompt fail before HTTP. An empty substitution within a nonempty prompt is allowed. No extra state, history, file contents, or common `prompt_prefix` instructions are appended automatically.

### Supported schema subset

The root must be a closed object (`additionalProperties: false`) with nonempty properties, all required. Question names match `[A-Za-z_][A-Za-z0-9_]*`. Root descriptive keywords are `$schema`, `$comment`, `title`, and `description`. Unsupported keywords/constraints fail loading rather than being discarded.

| Question | Property definition |
|---|---|
| Choice | `type: string`, nonblank `description`, and `oneOf` with at least two unique string `const` values and nonblank descriptions |
| Noul | `type: number`, `minimum: 0`, `maximum: 1`, `description`, `x-fdsx-evaluation: {kind: noul}` |
| Score | `type: number`, `minimum: 0`, `maximum: N-1`, `description`, `x-fdsx-evaluation: {kind: score, criteria: [level0, level1, ...]}` with at least two nonblank ordered levels |

Noul is the Yes probability, not a boolean. Score is an expected score and may be fractional.

Optional metadata properties use `x-fdsx-evaluation: {kind: metadata, question: action, field: confidence}` for a Choice/Score confidence number bounded by 0 and 1. `field: probabilities` requires a closed, fully required object of candidate probabilities, each bounded by 0 and 1. Score keys are decimal strings starting at `0`. Noul metadata, model metadata, and free-form rationale are unsupported.

Only requested fields are projected and saved, after all SDK answers pass validation. For ordinary LLMs, evaluation annotations become descriptive guidance and are removed from the provider-facing schema; the original schema still validates the saved value. Keep `allow_extra_fields: false` for strict output from both providers. Valid answers may differ between providers; LLM-generated metrics are self-reported, not service-calibrated accuracy.

### Task restrictions

An explicit nonblank model and `structured_output` are required. Profiles may supply provider/model. Omit `retry` or set it to `0`; nonzero task retries are rejected. `max_iterations` applies normally.

Unsupported: parallel/map placement, `fork_from`, `timeout_seconds`, `provider_options`, `result_file`, structured-output `merge`, and workflow `providers.jev` options. The usual structured-output exclusion of legacy `result_path`/`extract` applies. Jev bypasses inherited LLM options, retry escalation, and extraction fallback.

## Explicit evaluation state

```yaml
assess:
  type: evaluate
  evaluator: jev
  model: jev-1.13.0
  input:
    review:
      ref: $.review
    requirements:
      literal: Do not put secret material in logs.
  questions:
    action:
      type: choice
      instructions: Choose the next action.
      criteria:
        fix: Blocking problems remain.
        proceed: No blocking problems remain.
    ambiguity:
      type: noul
      instructions: Are the requirements ambiguous?
    quality:
      type: score
      instructions: Assess compliance with the requirements.
      criteria: [Not met, Partially met, Fully met]
  result_path: $.assessment
  end: true
```

Required fields: `evaluator: jev`, nonempty `input`, nonempty `questions`, `result_path`, and exactly one of `next` or `end: true`. Optional fields: `model` (default `jev-1.13.0`) and state hooks (`on_state_start`/`on_state_end`). Other fields, including task retry/options and `max_iterations`, are rejected.

`result_path` must name one top-level key. Internal destinations are forbidden: prefixes `_meta`, `__`, `_br_`, `_state_`, and keys `_session_references`, `remaining_steps`, `run_path`, `state`.

Material and question names match `[A-Za-z_][A-Za-z0-9_]*`. Each material has exactly one of `literal` or `ref`. References support concrete identifier fields, nonnegative array indices, and supported quoted object keys, not root/wildcards/filters. Missing references fail before transmission. Null, blank strings, and empty lists/objects are invalid material values; zero and false are valid. Nonempty containers may contain empty children. Nested values must be JSON-compatible with finite numbers and string keys. Literal content is not interpolated.

Question instructions are fixed nonblank strings. Choice needs at least two named candidates with nonblank descriptions. Score needs at least two ordered nonblank descriptions. Noul may optionally use criteria with quoted string keys `"true"` and `"false"`.

The saved envelope contains only `model: {requested, reported}` and `answers` keyed by question name:

- Choice: `{type, choice, probabilities, confidence}`; route on `$.assessment.answers.action.choice`.
- Noul: `{type, noul}`; compare `$.assessment.answers.ambiguity.noul` numerically.
- Score: `{type, score, legend, probabilities, confidence}`; compare `$.assessment.answers.quality.score` numerically. Legend/probability keys are strings starting at `"0"`.

## Validation, failures, and transport

All answers must pass validation before any save. Names/types/candidates/legends must match. Probabilities and confidence are finite numbers in [0, 1], not booleans. Distributions sum to one within `1e-6`. Choice selects a maximum-probability candidate (ties preserve the service selection); Score matches the weighted mean within `1e-6 × max(1, levels−1)`. Invalid responses fail without partial storage or retry. Low confidence alone is not failure and is not an accuracy guarantee.

Only the SDK retries communication: at most three attempts for connection/timeouts, HTTP 408/429, and 5xx. Backoff is normally 1 then 2 seconds; `Retry-After` takes precedence. The retry window is 60 seconds, with separate 30-second connect/read/write/pool timeouts. These are not a whole-operation deadline. Authentication/configuration failures and invalid answers are not retried. There is no second fdsx retry loop or other-AI fallback.

State hooks receive only `{state, status}` summaries. Raw evaluation input, SDK output, and exception bodies are excluded from task stream logs. Separate `evaluation` diagnostics record provider/service, requested/reported model, question names/types, confidence, and distributions, not rubric text or SDK payloads. Existing workflow input/checkpoint storage still applies; these boundaries do not erase supplied inputs.

## Start and resume

Fresh runs locally check for a nonblank key for all evaluation states, even ones a choice may skip, before start hooks or preceding tasks. Tasks-directory preflight checks selected workflows before starting the batch. Loading validates schema/options without probing service credentials or model availability.

Resume checks every reachable alternative/default/loop from the saved position or `--from` target. Saved answers can be reused without a key or HTTP only if no evaluation is reachable. Returning to an evaluation makes a new request; a failed evaluation resends all questions. `--from` still cannot reopen a successfully completed run. Pre-save interruption or failing end hooks can repeat a completed request, so exactly-once HTTP is not guaranteed.

Validate files locally before execution. Live evaluation transmits declared materials to an external service; mocked tests establish format and execution contracts, not accuracy, retention, or live compatibility.

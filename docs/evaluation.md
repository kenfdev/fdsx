# Jev evaluation steps

Use a top-level `type: evaluate`, `evaluator: jev` state to assess explicit
materials. It returns answers; use an ordinary `choice` state for routing.
It does not perform work, approve actions, or grant permissions. The existing evaluate format remains supported. Evaluation does not change
existing extraction or extraction fallback.
System output is still excluded from automatic external extraction assistance;
an author may explicitly name it as an evaluation material.

The official `typesafe-sdk>=0.6.0,<0.7` is a normal dependency (locked to 0.6.0).
No extra installation option or Jev CLI is needed. Set `TYPESAFE_API_KEY` in
the execution environment when using evaluation. Installation or execution of
a workflow without evaluation does not contact Jev or require its key.

## Shared task interface

A top-level `type: task` can select `provider: jev`. Change only `provider`
and `model` to use an ordinary LLM with the same prompt, output contract,
result path and following states. The [Jev example](../src/fdsx/examples/workflows/evaluation-task/review-jev.yaml)
and [Claude example](../src/fdsx/examples/workflows/evaluation-task/review-llm.yaml)
share their schema and prompt file. They require `review` and `requirements`
inputs. These examples are tested with mocked transport, not live providers.

```yaml
assess:
  type: task
  provider: jev
  model: jev-1.13.0
  prompt_file: review-input.txt
  structured_output:
    schema: review-decision.schema.json
    result_path: $.assessment
    allow_extra_fields: false
  next: route
```

This path evaluates every time the task executes; it is not extraction recovery.
The input is the once-resolved prompt, sent as one explicitly named material.
No state, history or file contents are appended automatically. Missing or null
references are rejected before HTTP. A wholly empty or whitespace-only resolved
prompt is rejected; an empty individual substitution in a nonempty prompt is
not independently rejected. Literal braces use the existing template-reference
syntax. Normal LLM handling of unresolved references remains unchanged.

### Supported output definitions

The schema must be a closed object (`additionalProperties: false`) with nonempty,
named properties, all required. Question names use `[A-Za-z_][A-Za-z0-9_]*`.
Root descriptive keywords are `$schema`, `$comment`, `title`, and `description`.
Unsupported keywords or constraints are rejected rather than discarded.

- Choice: a string property with a nonblank `description` and `oneOf` alternatives,
  each containing a unique string `const` and nonblank `description` (at least two).
- Noul: a number property with `minimum: 0`, `maximum: 1`, `description`, and
  `x-fdsx-evaluation: {kind: noul}`. It returns the Yes probability, not a boolean.
- Score: a number property with `minimum: 0`, `maximum: N-1`, `description`, and
  `x-fdsx-evaluation: {kind: score, criteria: [level0, level1, ...]}`. At least two
  nonblank ordered levels are required. Fractional expected scores are preserved.
- Metadata: `x-fdsx-evaluation: {kind: metadata, question: action, field: confidence}`
  requests a Choice/Score confidence number, with bounds 0 and 1. `field: probabilities`
  requests a closed required object of candidate probabilities, each a number with
  bounds 0 and 1. Score probability keys are decimal strings starting at `0`.
  Noul metadata, model metadata and free-form rationale generation are unsupported.

The [mixed schema](../src/fdsx/examples/workflows/evaluation-task/assessment.schema.json)
includes all three question types and explicitly requested metadata. All questions
are independent and all SDK answers are validated before any projection or save.
The original schema validates the projected JSON through the shared structured-output
parser. Only requested fields are saved; the default extra-field policy still applies
to normal LLMs, so use `allow_extra_fields: false` for strict output.

For ordinary LLMs, evaluation annotations are converted to descriptions conveying
Yes probability, ordered scoring levels and metadata provenance, then removed from
the provider-facing schema. The original schema remains the save-time contract.
Schemas without evaluation annotations retain their existing behavior. Responses
from Jev and an LLM need not choose the same valid answer.

### Task options, placement and preflight

Jev tasks currently support top-level placement only. Parallel branches and map
iterators are rejected before execution, including partial-success configurations.
Profiles may select Jev. An explicit model is required. Task `retry` defaults to
zero for Jev; an explicit nonzero value is rejected. `timeout_seconds`, `fork_from`,
`provider_options`, `result_file`, structured-output merge and workflow `providers.jev`
options are unsupported and rejected. The task never uses inherited LLM escalation,
LLM options or extraction fallback. SDK retry and timeout limits below apply;
there is no task-wide deadline guarantee. `max_iterations` still applies.

Schema/options checks run during loading. Key checks run before start hooks and
preceding tasks, including CLI and multi-task preflight. A new run checks all
states, even potentially unexecuted branches. Resume checks reachability from the
saved position or `--from`, including choice alternatives, defaults and loops.
Reading a saved assessment without reaching another Jev task requires no key or
HTTP. Reaching the task again reevaluates it. Existing recovery eligibility rules
still apply: `--from` does not reopen a successfully completed run. Exactly-once
HTTP is not guaranteed across pre-save interruption or failing end hooks.

### Task records and information boundaries

A task-specific adapter bypasses ordinary LLM streaming, retry and escalation.
The Jev provider and legacy evaluate both call the same SDK/answer validator.
SDK failures propagate as safe evaluation failures, without fake successful values.
No raw input, SDK response or exception is sent to task stream logs. State hooks
receive only `{state, status}` summaries, as for legacy evaluate. Existing workflow
input/checkpoint storage is unchanged; this is not a promise to erase author inputs.

Run state records carry a separate `evaluation` diagnostic object: actual provider,
service provenance, requested/reported model, question name/type, validated confidence
and distributions. Labels are printable and bounded to 128 characters; distributions
use a list so shortened labels cannot overwrite each other. Rubric text, SDK payloads
and invented request counts are excluded. LLM self-reported metrics are described as
such in schema guidance, not presented as service-calibrated accuracy.

Mocked tests establish format and execution contracts, not real-service accuracy,
data retention or compatibility with an installed LLM CLI version.

Both Jev entry points encode materials with Unicode preserved, without translating,
summarizing or truncating input. The SDK still receives one JSON state string;
its outer HTTP JSON envelope does not change the decoded state.

Successful calls record `evaluation.usage.input_tokens` and
`evaluation.usage.output_tokens` in each state's `run.json` diagnostics.
These are service-reported counts; missing counts are `null`, not zero or estimates.
They do not change the answer/result shape used for routing and do not measure
failed requests or predict whether a future request will fit a model limit.

### Failure diagnostics

Both Jev tasks and legacy evaluate states retain safe diagnostics in the error
message, CLI output, saved state error and structured `evaluation_failed` log:

- `category`: `http`, `connection`, `timeout`, `response_validation`, `encoding`
  or `sdk`. A malformed successful HTTP response is `response_validation`, not
  an HTTP rejection.
- `exception_type`: a known SDK/Python exception class, never an arbitrary
  subclass name or exception message.
- `http_status`: when available, an integer HTTP status (100–599).
- `error_type`: the service's `detail.error_type`, when it is a bounded identifier
  rather than free text or echoed input/credentials. New service codes are accepted
  without a fixed vocabulary. `max_tokens_exceeded` adds guidance that retrying the
  same input will not resolve the error; revise materials or questions first.
- `request_id_sha256`: an optional SHA-256 fingerprint of the SDK's request ID.
  Only 1–128 ASCII letters, digits, dots, underscores and hyphens qualify.
  Invalid/missing IDs are omitted. The original ID is never copied: even a
  well-formed header could contain private material. The fingerprint supports
  correlation with an independently known ID, not recovery of the original ID.

For example, a rejected request can report `Jev request failed (category=http,
exception_type=TypeSafeUnprocessableEntityError, http_status=422)`.
Full bodies, headers, URLs, validation field paths, exception text and exception
chains are not included. SDK wire logging stays suppressed. Retry counts, delays, success
results and routing are unchanged; a diagnostic is not a fallback result.

These guarantees concern evaluation diagnostics. Existing author input/state
persistence (including `run.json` final variables and checkpoints) is unchanged.
Old runs that saved only `Jev request failed` cannot recover a status or cause
from these new diagnostics.

## Legacy evaluate example

The [complete example](../src/fdsx/examples/workflows/evaluate-review.yaml)
takes `review` as an execution input:

```sh
fdsx run src/fdsx/examples/workflows/evaluate-review.yaml --input 'review=The implementation meets the requirements.'
```

It asks three independent questions in a single SDK call:

```yaml
assess:
  type: evaluate
  evaluator: jev
  input:
    requirements:
      literal: Do not put secret material in logs.
    review:
      ref: $.review
  questions:
    action:
      type: choice
      instructions: Choose the next action.
      criteria:
        fix: Problems need correction.
        investigate: Information is missing.
        proceed: No blocking problem was found.
    ambiguity:
      type: noul
      instructions: Are the requirements ambiguous?
    quality:
      type: score
      instructions: Assess compliance with the requirements.
      criteria: [Not met, Partially met, Fully met]
  result_path: $.assessment
  next: route
```

Every evaluation requires exactly one of `next` or `end: true`.
Parallel branches and map iterators cannot contain evaluations. The required
`result_path` must be one top-level key, such as `$.assessment`;
nested paths and internal keys are rejected. In particular, keys beginning with `_meta`, `__`, `_br_`, or `_state_`, and the keys
`_session_references`, `remaining_steps`, `run_path`, and `state` cannot be destinations.

## Materials and questions

`input` and `questions` are nonempty named mappings. Their names match
`[A-Za-z_][A-Za-z0-9_]*`. Each material specifies exactly one of `literal`
or `ref`. References are concrete JSONPaths, supporting identifier fields,
nonnegative array indices, and quoted object keys supported by the existing
resolver. Empty paths, the whole root, wildcards, filters, and ambiguous syntax
are rejected. Missing references fail before transmission.

Every material is required. Null, empty or whitespace-only strings, empty
lists, and empty objects fail. Zero and false are valid. Only each material's
immediate value is checked for emptiness; null or empty children within a
nonempty list/object are allowed. All nested values must be JSON-compatible,
with finite numbers and string object keys. Errors identify the declared
material location without printing its contents.

The resolved named object is encoded once as a JSON string for the SDK.
Literal braces and `ref` keys inside literal content are not expanded.
No other input, history, state, or execution metadata is appended.
HTTP authentication and SDK-version headers are transport metadata.

Instructions are fixed nonblank strings. Choice requires at least two named
candidates with nonblank descriptions. Score requires an ordered list of at
least two nonblank descriptions. Noul optionally accepts descriptions under
the string keys `"true"` and `"false"` (quote them in YAML).
Questions are independent; one answer cannot supply another question in the
same call.

## Answers and validation

The saved object contains only `model` and `answers`:

- `model.requested`: the requested model name.
- `model.reported`: the exact name returned by the service, without inferring a version.
- Choice: `{type, choice, probabilities, confidence}`.
- Noul: `{type, noul}`, where `noul` is the probability of Yes.
- Score: `{type, score, legend, probabilities, confidence}`.

Answers are under their question names. Score levels start at zero; both
legend and probability keys are saved as strings, including across resume.
The SDK's raw response, materials, HTTP headers, and usage are not added to
this result. Existing workflow/input/checkpoint persistence remains in use.

For example, compare `$.assessment.answers.action.choice` with `proceed`,
or use numeric comparisons on `$.assessment.answers.ambiguity.noul` and
`$.assessment.answers.quality.score`.

All answers must be valid before any are saved. Missing/extra names, mismatched
types, unknown candidates, or mismatched Score legends fail the state. Probabilities
and confidence must be finite numbers in [0, 1]; bool is not a number here.
Distributions must sum to one within `1e-6`, without normalization.
Choice must select a maximum-probability candidate; ties preserve the service's
selection. Score must be in [0, levels−1] and match its distribution's weighted
mean within `1e-6 × max(1, levels−1)`.

Confidence is preserved from the service, not recalculated. It is not an
accuracy guarantee. Noul has no confidence field. Low confidence alone does
not fail an otherwise valid response; authors may route on it themselves.
Invalid responses fail immediately, without retry or partial result storage.

## Model, transport, and failures

The default model is `jev-1.13.0`. Each state may override `model` with a
nonblank string; explicit aliases such as `jev-latest` do not guarantee a
fixed version. fdsx explicitly passes the resolved model and the fixed endpoint
`https://api.typesafe.ai`; SDK model/base-URL environment defaults cannot
silently override them.

Only the SDK retries communication, using
`RetryPolicy(max_retries=2, backoff_initial=1, backoff_max=2, backoff_jitter=0, timeout=60)`.
Connection failures, communication timeouts, HTTP 408/429 and 5xx (including 529)
allow at most three total attempts. Normal waits are one then two seconds;
`Retry-After` takes precedence. A wait that would exhaust the 60-second retry
window stops retrying. Authentication/configuration failures such as 401/403/422
and invalid responses are not retried. There is no second fdsx retry loop,
other-AI fallback, or replay of preceding tasks.

Connect/read/write/pool HTTP timeouts are each 30 seconds. These are not a
whole-operation deadline. The 60-second window decides whether another retry
may begin; it does not cancel an in-progress request or guarantee completion
within 60 seconds. Retry settings are not author-configurable in this version.

Failures identify the state and expose a safe fdsx error. Logs contain summaries
only; SDK body logging is suppressed even at DEBUG. Evaluation state-hook files
contain only state name and status on both success and failure. Other states'
hook data contracts are unchanged. CLI human-facing failures go to stderr.

## Start and resume

Fresh execution checks for a nonblank key if any evaluation exists in the
definition, including a branch that might never execute. This local check runs
before `on_run_start`, `on_workflow_start`, and preceding tasks. Tasks-directory
execution resolves and checks the selected definitions before starting the
batch, including automatic workflow selection. Neither key validity nor model
availability is probed over the network.

Resume rereads the workflow definition and checks possible reachability from
the saved next execution position, or the explicit `resume --from` target.
It follows every choice alternative, default, and loop edge without executing
a state or a hook to decide reachability. If evaluation cannot be reached,
saved answers can be reused without a key or a new SDK call.

Actually returning to an evaluation, or explicitly restarting before it, performs
a new evaluation. A failed evaluation resends all questions when resumed.
There is no guarantee of exactly-once communication: interruption before
checkpointing or an end-hook failure can cause a completed request to be
repeated. Offline fake-response tests verify these behaviors, not real-service
accuracy.

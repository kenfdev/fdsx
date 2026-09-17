# Jev evaluation steps

Use a top-level `type: evaluate`, `evaluator: jev` state to assess explicit
materials. It returns answers; use an ordinary `choice` state for routing.
It does not perform work, approve actions, or grant permissions. Jev is not a
task provider and does not change existing extraction or extraction fallback.
System output is still excluded from automatic external extraction assistance;
an author may explicitly name it as an evaluation material.

The official `typesafe-sdk>=0.6.0,<0.7` is a normal dependency (locked to 0.6.0).
No extra installation option or Jev CLI is needed. Set `TYPESAFE_API_KEY` in
the execution environment when using evaluation. Installation or execution of
a workflow without evaluation does not contact Jev or require its key.

## Example

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

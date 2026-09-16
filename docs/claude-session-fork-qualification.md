# Claude session fork qualification

Status as of 2026-09-16: **local implementation complete; native verification pending**. Mocked checks establish the adapter contract, not verified native support.

## Approved CLI boundary

Use `claude -p --resume <source-session-id> --fork-session` through the existing subprocess adapter. Do not switch to the Agent SDK.

FDSX leaves a completed source session unchanged. Destinations and retries create independent native children. Replanning creates a new session, or a new child of its own selected source, and publishes the new reference only after output validation. A saved native session ID is sufficient under this lifecycle.

External resumption, editing, replacement, or concurrent mutation of source history is outside the guarantee. Historical-message selection, transcript hashing, mutation detection, and snapshotting are not requirements for Claude. Missing/unusable sessions and native fork failures must still fail without starting fresh. Child execution must preserve the parent conversation.

This user-approved boundary replaces the earlier qualification gate that required saved-message selection or an integrity contract against external changes. Existing Pi behavior and other provider tickets are unchanged.

## Evidence

- The [official CLI reference](https://code.claude.com/docs/en/cli-reference) documents `--resume` with `--fork-session`, creating a new session ID.
- The [headless guide](https://code.claude.com/docs/en/headless) documents session metadata in JSON/stream output and resuming a captured session ID.
- The [session guide](https://code.claude.com/docs/en/sessions) documents branching, parent preservation, native storage, and retention. Native transcript entry formats are internal and should not become a supported parsing dependency.
- Complete source snapshots and the [implementation handoff](../.ai-tmp/state-session-fork-providers/research/claude-handoff.md) are stored under `.ai-tmp/state-session-fork-providers/research/`.
- Public SDK `resumeSessionAt` is not a public CLI flag contract and is irrelevant to the approved implementation. No SDK dependency is proposed.

Documentation establishes a candidate invocation, not verified runtime behavior or a minimum supported version. The previously observed installation 2.1.271 is not a tested baseline.

## Local assignment and remaining verification

The adapter, mocked workflow tests, and documentation implement the local assignment. Existing shared session contracts, source ordering, retry isolation, checkpoint recovery, Pi references, and ordinary non-fork invocation are preserved.

The native portions of AC01, AC14, and AC16 remain pending until separately approved real checks establish capture, distinct siblings, unchanged parent, concurrency, retry isolation, fresh replanning, durable resume, and any claimed model switching. Prepare exact commands with a disposable workspace, model, costs, session/file writes, and risks. Obtain approval for each real invocation. Do not claim verified support based on mocked tests.

## Historical run

Run `2026-09-16-135445-ed2e10` stopped at `environment_blocked` under the superseded requirements. Its report recorded 104 existing offline tests passing; those tests did not verify native Claude forks. That result is historical and was not rerun during this preparation.

Implementation proceeded in fresh run `2026-09-16-141445-1cfb25`, without reusing the superseded checkpoint or plan. No real provider was executed.

## Local implementation (2026-09-16)

The adapter now implements the approved CLI boundary. Native qualification is
still pending; the historical run above is retained as history. No tested baseline
has been established, including 2.1.271.

For a workflow using `provider: claude`, ordinary sources are captured only when
referenced. Destinations use `--resume <source-session-id> --fork-session` on
every attempt. Session-aware calls use `--output-format stream-json --verbose
--include-partial-messages`. The completion event supplies the source/child UUID;
checkpoints hold only `{provider: claude, session_id: <UUID>}` as native metadata.
Missing, malformed, ambiguous or parent-reusing IDs fail closed. The existing
non-session invocation remains unchanged. Structured results take precedence over
streamed prose; native metadata is not output. No daemon or SDK is introduced.

Both endpoints and effective retry escalation must have the same Claude provider
and identical model strings after profile resolution. Cross-model operation is
not qualified and is rejected; model aliases must resolve consistently in the
native installation. Other provider tickets are independent. Pi compatibility
and reference formats remain unchanged.

FDSX publishes a source reference only with a successful validated task update.
Replanning starts fresh, or forks its selected upstream source; it never resumes
its old published session in place. Children share current files. Interrupted
resume uses saved references; explicit recovery clears them, so rerun the source
before its dependent destination. A crash between native completion and checkpoint
publication can leave an orphan session or repeat execution. There is no exactly-once
guarantee, backup, rollback, or protection from external native-history changes.

The cached official session guide documents local transcripts under
`~/.claude/projects/<project>/<session-id>.jsonl`, configurable native storage,
and a default 30-day retention policy (`cleanupPeriodDays`). These are documentation
claims awaiting runtime qualification. Keep native history and its storage context
available for the entire workflow/recovery lifetime. A copied FDSX checkpoint is
not a portable history backup. Do not disable native session persistence. Native
settings, cleanup, deletion, corruption, unavailable models, or CLI incompatibility
can make a saved ID unusable. FDSX does not inspect native transcripts or change
retention settings.

Errors identify the destination state and direct the operator to verify CLI
compatibility/retained history and rerun the source. Failed subprocess diagnostics
are sanitized rather than copying native stderr or error-result bodies into logs.
A missing checkpoint reference names the required source. There is no fallback
invocation without `--resume`; normal configured retries may attempt new children
of the same source. Missing metadata raises a provider session domain error.

## Proposed real checks — not executed

Each numbered invocation needs separate approval in a later phase. Before approval,
review local/managed Claude settings, hooks and MCP configuration for effects; do
not retrieve credentials. Use an empty disposable workspace
`/tmp/fdsx-claude-forks-qualification-20260916`, with that directory as cwd for
every invocation below. Do not reuse these IDs if history already exists. No
commands in this section were run by this implementation assignment.

The proposed model is `claude-sonnet-4-6`; availability and actual native version
remain unverified. Each inference invocation sends the synthetic prompt/context
to the configured Claude service, writes native session history and a local NDJSON
result file, and consumes billed tokens (proposed $1 CLI budget per call, not a
guarantee of exact price or billing mode). Intended workspace writes are only the
redirected evidence files. `--tools '' --disallowedTools 'mcp__*'` denies tools,
but settings/hooks must still be reviewed before execution. Do not log credentials
or raw transcripts in the final qualification report.

1. Record actual executable version; no inference intended:

   `claude --version`

2. Capture A, confirm the result event contains the selected UUID:

   `claude -p 'Remember PLAN_TOKEN_742. Reply READY.' --model claude-sonnet-4-6 --session-id 86e1d8b4-9db6-4c32-b74b-000000000001 --output-format stream-json --verbose --include-partial-messages --tools '' --disallowedTools 'mcp__*' --max-budget-usd 1 > source-a.ndjson`

3. Implementation child B; require a new ID:

   `claude -p 'Recall the plan token. Remember CHILD_B_ONLY. Reply with the plan token.' --model claude-sonnet-4-6 --resume 86e1d8b4-9db6-4c32-b74b-000000000001 --fork-session --output-format stream-json --verbose --include-partial-messages --tools '' --disallowedTools 'mcp__*' --max-budget-usd 1 > child-b.ndjson`

4. Independent review C; verify it recalls the plan and does not report B's marker:

   `claude -p 'Report the plan token and any child-only marker in the inherited conversation.' --model claude-sonnet-4-6 --resume 86e1d8b4-9db6-4c32-b74b-000000000001 --fork-session --output-format stream-json --verbose --include-partial-messages --tools '' --disallowedTools 'mcp__*' --max-budget-usd 1 > child-c.ndjson`

5. Retry isolation, new D from A; no B/C continuation:

   `claude -p 'Report the plan token and any child-only marker in the inherited conversation. Return JSON.' --model claude-sonnet-4-6 --resume 86e1d8b4-9db6-4c32-b74b-000000000001 --fork-session --output-format stream-json --verbose --include-partial-messages --tools '' --disallowedTools 'mcp__*' --max-budget-usd 1 > retry-d.ndjson`

6. Replan E, independent of A:

   `claude -p 'Remember PLAN_TOKEN_953. Reply READY.' --model claude-sonnet-4-6 --session-id 86e1d8b4-9db6-4c32-b74b-000000000005 --output-format stream-json --verbose --include-partial-messages --tools '' --disallowedTools 'mcp__*' --max-budget-usd 1 > source-e.ndjson`

7. Child from E must report the new token:

   `claude -p 'Report the plan token.' --model claude-sonnet-4-6 --resume 86e1d8b4-9db6-4c32-b74b-000000000005 --fork-session --output-format stream-json --verbose --include-partial-messages --tools '' --disallowedTools 'mcp__*' --max-budget-usd 1 > child-e.ndjson`

8. Concurrent child one (separate terminal, separate approval):

   `claude -p 'Recall the plan token. Remember CONCURRENT_ONE. Reply with the plan token.' --model claude-sonnet-4-6 --resume 86e1d8b4-9db6-4c32-b74b-000000000001 --fork-session --output-format stream-json --verbose --include-partial-messages --tools '' --disallowedTools 'mcp__*' --max-budget-usd 1 > concurrent-one.ndjson`

9. Concurrent child two (overlap invocation 8, separate approval):

   `claude -p 'Report the plan token and any child-only marker in inherited history.' --model claude-sonnet-4-6 --resume 86e1d8b4-9db6-4c32-b74b-000000000001 --fork-session --output-format stream-json --verbose --include-partial-messages --tools '' --disallowedTools 'mcp__*' --max-budget-usd 1 > concurrent-two.ndjson`

10. Durable selection and parent preservation: after all processes exit, in a fresh
    terminal use A again. Require the original plan token, no child markers, and a
    new child ID. Record native parent-preservation evidence without exporting its
    contents; model answers alone are not conclusive proof of parent immutability.

    `claude -p 'Report the plan token and any child-only marker in inherited history.' --model claude-sonnet-4-6 --resume 86e1d8b4-9db6-4c32-b74b-000000000001 --fork-session --output-format stream-json --verbose --include-partial-messages --tools '' --disallowedTools 'mcp__*' --max-budget-usd 1 > durable-parent-check.ndjson`

Record version, model, storage/settings metadata, child-ID distinctness, selected
source, overlap evidence and pass/fail/inconclusive per check. Do not mark parent
preservation proven solely from mocked fixtures. Cross-model checks are excluded
until a separately agreed compatibility expansion. FDSX checkpoint selection and
validation retry wiring are tested offline; these native checks qualify the CLI
assumptions those paths rely on. AC01/AC14/AC16 native portions remain open.

## Offline verification record

Implementation run `2026-09-16-141445-1cfb25`, 2026-09-16. Applied the local fdsx
skill guidance; no workflow orchestration or real providers were launched. All
Python commands used existing dependencies with `--offline --no-sync`:

```text
uv run --offline --no-sync pytest tests/integration/test_claude_session_forks.py tests/integration/test_session_forks.py tests/unit/test_session_fork_ancestry.py tests/unit/test_pi_session_references.py tests/integration/test_pi_fork_bridge.py tests/integration/test_claude_streaming.py tests/unit/test_claude_stream_parser.py tests/unit/test_claude_options.py tests/integration/test_provider_options.py tests/unit/test_provider_options.py tests/integration/test_retry_escalation.py -q
369 passed in 5.82s

uv run --offline --no-sync ruff check src/ tests/
All checks passed!

uv run --offline --no-sync mypy src/
Success: no issues found in 71 source files

uv run --offline --no-sync ruff format --check .
262 files already formatted

git diff --check
Passed
```

Commands were invoked through `rtk proxy`. The new Claude workflow suite covers
ordinary siblings/current files/fork chains (AC02/07/10), execution and structured
retries in ordinary/parallel/map destinations (AC06/09), validated publication and
fresh replanning including forked sources (AC05), failed-replan recovery and
reference-only checkpoint resume/missing metadata (AC11–13), parallel gates/map
failure policies/map visit progress (AC06), effective profiles/escalation/model
restrictions and thin CLI rejection (AC03/08/15), malformed metadata/privacy and
large-prompt/timeout wiring (AC11/18). Shared ancestry cases now run for both Pi
and Claude across all three destination kinds (AC04). Existing Pi session/bridge,
Claude streaming/options and retry suites provide regression evidence (AC17/18).
These assertions establish FDSX request and state behavior against synthetic
native output; they do not prove native conversation inheritance or immutability.

## Review repair evidence (2026-09-16)

F-ACC-1: Added session-aware text/tool stream replay before completion. Workflow
checks cover accumulated output and keyword extraction, structured output taking
precedence over streamed prose, and exclusion of session metadata. Focused adapter
checks assert text/summary callback delivery, final-message selection, and tool
inactivity suspend/resume forwarding for both plain and structured output.

F-SEC-1: The session boundary now translates ValueError and RecursionError from
both envelope decoding and delegated stream parsing into the sanitized session
error. Regression checks replay deeply nested accumulated tool JSON and an
oversized integer on a reader thread, followed by valid completion. They assert
continued consumption, no thread exception or traceback/private diagnostic,
caller-thread ProviderSessionError, and exactly one subprocess invocation.
The integer-limit case ran (not skipped) on this environment.

Repair checks (all commands prefixed with `rtk proxy`):

```text
uv run --offline --no-sync pytest tests/integration/test_claude_session_forks.py tests/integration/test_session_forks.py tests/unit/test_session_fork_ancestry.py tests/unit/test_pi_session_references.py tests/integration/test_pi_fork_bridge.py tests/integration/test_claude_streaming.py tests/unit/test_claude_stream_parser.py tests/unit/test_claude_options.py tests/integration/test_provider_options.py tests/unit/test_provider_options.py tests/integration/test_retry_escalation.py -q
375 passed in 5.99s
uv run --offline --no-sync ruff check src/ tests/
All checks passed!
uv run --offline --no-sync mypy src/
Success: no issues found in 71 source files
uv run --offline --no-sync ruff format --check .
262 files already formatted
```

`rtk git diff --check` also passed. No native providers were executed; native
qualification remains pending. These are implementer check results for independent
verification, not review approval. The index was left untouched.

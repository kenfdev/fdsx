# Grok session forks: local implementation, qualification pending

The Grok adapter implements the existing `fork_from` interface for ordinary
tasks, parallel branches and map items. Offline tests exercise the native CLI
contract through `run_flow` and `resume_flow`. **Grok native support is not yet
verified.** An individually approved version probe returned `grok 1.0.30
(04b7ffed98c6) [stable]`, and the separately approved smoke run
`2026-09-17-024651-135626` completed successfully. Those checks cover observed
recall and sibling isolation only; the remaining native qualification checks
are pending. This status is independent of Claude, Codex and Cursor
qualification.

## Evidence and candidate boundary

Local first-party documentation inspected on 2026-09-17:

- `/home/ubuntu/.grok/docs/user-guide/17-sessions.md`, “Headless Session
  Management”: `--resume <UUID> --fork-session` creates a new child; optional
  `--session-id <UUID>` names that child. Same-ID concurrent creation is only
  best effort, so FDSX generates a new UUID for every attempt.
- That document's storage sections describe sessions grouped by working directory
  under `$GROK_HOME/sessions` (default `~/.grok/sessions`). `updates.jsonl` is
  authoritative conversation history; `summary.json` records parent relationships.
- `/home/ubuntu/.grok/docs/user-guide/14-headless-mode.md`, flag table and session
  persistence sections: missing resume sessions error; the fork flag creates a
  new session instead of appending to the original. JSON completion carries
  `sessionId`; streaming `end` carries completion metadata.
- `/home/ubuntu/.grok/docs/user-guide/26-config-reference.md`: model and storage
  configuration are installation dependent. These docs do not qualify saved-fork
  model switching.
- Read-only inspection of `/home/ubuntu/.grok/downloads/grok-linux-x86_64`
  found repeated embedded `x-grok-client-version1.0.30` strings. This identifies
  **candidate 1.0.30**, not a tested release, earliest supporting version, or a
  guarantee that the adjacent documentation matches the executable.

Session execution requires candidate version `grok 1.0.30`, checked
with `grok --no-auto-update --version`. Both bare output and a hexadecimal
build ID followed by `[stable]` are accepted. The approved version probe on
2026-09-17 returned `grok 1.0.30 (04b7ffed98c6) [stable]`.
The first approved smoke run (`2026-09-17-024349-8e049c`) stopped at `seed`
before conversation execution because the original exact-string check rejected
that suffix. The parser was corrected and the Grok integration suite passed
72 offline tests, including this observed version and rejection of other
versions/preview channels. Ruff lint, formatting, and adapter type checks passed.
The corrected smoke test passed in the separately approved run below. Full
native qualification remains pending.
Unknown versions fail closed before a conversation call. Do not broaden this
guard merely because a version is newer. Ordinary execution without session
capture retains its prior command and parser behavior.

Public native-interface evidence is sufficient for the approved local work.
Unattended execution, durable completion publication, parent preservation,
independent concurrent children, storage lookup across resume, and the candidate
baseline still require native checks. No upstream incompatibility is proven.

## Invocation and semantics

A referenced source receives `--session-id <fresh-UUID>`. A destination receives
`--resume <saved-source-UUID> --fork-session --session-id <fresh-child-UUID>`.
Existing unattended flags, configured model/options, streaming parser,
large-prompt file handling, timeouts and callbacks remain in use. There is no
new daemon, history copying, transcript replay, worktree, or filesystem rollback.

The single terminal `end.sessionId` must equal the requested child UUID. Missing,
malformed, ambiguous, or mismatched metadata produces `ProviderSessionError`.
Successful references contain only `{provider: grok, session_id: <UUID>}`. Shared
execution publishes them after extraction/structured validation succeeds. Every
execution or structured-output retry creates a new child from the selected source.
Crashes between native completion and checkpoint publication can orphan children
or repeat work; no exactly-once guarantee is made.

Only identical effective model IDs are allowed, including retry escalation.
Profiles resolve before validation. Cross-provider forks and escalation reject.
No cross-model compatibility is claimed, even for aliases. Model configuration
must remain usable; native errors never cause fresh-conversation fallback.

FDSX selects a saved session ID, not a historical endpoint. Later external
changes to a usable source are acceptable. There is no transcript hashing,
mutation check, or historical selector. FDSX-owned children must preserve the
source; mocks verify requests, while native preservation remains unverified.

Retain the native session directory and ancestors, not just FDSX checkpoints.
Keep native storage and working directory accessible across execution and resume,
including any configured `cwd`. Portability to another workspace or GROK_HOME is
unqualified. FDSX does not back up, migrate, or read native history.
Deletion, corruption, missing metadata, incompatible versions and failed forks
report the affected destination state with guidance to verify CLI compatibility
and retained history, then rerun the source. Missing checkpoint references name
both source and destination. Explicit recovery clears references; jumping past
planning fails until necessary sources rerun. Native error bodies and stderr
are excluded from session error diagnostics.

## Offline evidence

`tests/integration/test_grok_session_forks.py` mocks only the Grok subprocess
boundary (including the version probe). It exercises:

| Criteria | Observable checks |
| --- | --- |
| AC02, AC07, AC10 | Independent implementation/review children, current files, fork chains, fresh replanning IDs, saved parent selection, synthetic external changes and concurrent siblings |
| AC03, AC04, AC08, AC15 | Loader/CLI rejection, unsupported and mixed providers, invalid source scope, effective profiles, inherited escalation and model restrictions; shared ancestry matrix additionally covers Grok task/parallel/map destinations |
| AC05, AC09 | Execution/structured retries in all three modes, publication after validation, failed replanning, crash after native completion before publication, bounded shared retry policies |
| AC06 | Required/advisory gates, map fail-fast, resumed item progress and a new map visit after replanning |
| AC11–AC13 | Invalid references/metadata, malformed/deep/oversized-integer JSON, incompatible versions, native errors/timeouts, checkpoint restoration/erasure, explicit recovery and reference-only persistence |
| AC17, AC18 | Pi/Claude/Codex regression suites, ordinary Grok tests, extraction/structured output, callbacks, inactivity hooks, large prompts and unchanged empty CLI stdout |

Final targeted command (all commands run locally with installed dependencies):

```sh
rtk proxy uv run --offline --no-sync pytest tests/integration/test_grok_session_forks.py tests/integration/test_session_forks.py tests/integration/test_claude_session_forks.py tests/integration/test_codex_session_forks.py tests/integration/test_grok_provider.py tests/unit/test_grok_stream_parser.py tests/unit/test_session_fork_ancestry.py -q
```

Result: **418 passed**. The obsolete Grok entry was removed from Codex's
untouched-unsupported-provider test; cross-provider rejection including Grok
remains covered. `uv run --offline --no-sync mypy src/` passed for 71 source files;
`ruff check src/ tests/` passed and `ruff format --check .` passed for 264 files
(both also invoked through `rtk proxy uv run --offline --no-sync`).

Final repository check: `rtk proxy uv run --offline --no-sync pytest tests/ -q`
completed with **3150 passed, 2 warnings** in 262.32 seconds. The warnings are
Pydantic serialization warnings from the existing tasks-directory mocks.
An earlier run exposed a missing `result_path` in the new extraction test fixture;
the fixture was corrected and both targeted and complete suites were rerun.
`git diff --check` passed. No files were staged or committed.

Mocked history and subprocess results establish FDSX behavior, not native Grok
preservation or durability. AC01/AC14/AC16 native qualification remains pending.

## Approved real smoke check (2026-09-17)

- Command: `uv run --offline --no-sync fdsx run examples/session_fork_smoke_grok.yaml`.
- Workspace: `/fdsx/.wt/fdsx/state-session-fork`.
- Run: `2026-09-17-024651-135626`; completed successfully in 33 seconds.
- CLI: `grok 1.0.30 (04b7ffed98c6) [stable]`; model: `grok-4.6`.
- Seven model calls; retries disabled, memory off, subagents/plan/web search
  disabled, tool allowlist/deny rules configured in the example.
- Every exact-output assertion passed: ordinary, concurrent parallel, and map
  children recalled the outer source. A final sibling recalled the original
  source label rather than any child's changed label.
- This establishes observed recall and sibling conversation isolation for the
  tested configuration, not a verified minimum version or full qualification.
- Still unverified by this smoke check: native parent-storage preservation,
  distinct persisted child IDs, retry isolation, durable interrupted resume,
  and missing/deleted native history behavior. No model switching is claimed.
- Native conversation storage and FDSX run records were created. This evidence
  summary intentionally excludes raw conversation contents and credentials.

## Pending additional real checks (no authorization granted)

Each invocation needs separate explicit approval, including the version probe.
Proposed workspace: `/tmp/fdsx-grok-qualification-20260917`, disposable and prepared
only after approval. Proposed model: `grok-4.6`; availability and pricing remain
unverified. Do not switch models implicitly or change global configuration.

The version probe should not create conversations or incur model costs, but its
startup behavior remains unverified. All other commands can contact model services,
incur token costs including inherited context, write provider sessions/metadata,
run configured hooks/tools and change workspace files. Maximum turns limits rounds,
not monetary cost. No infrastructure, remote writes, deployment or publishing is
authorized. Inspect configured provider hooks through an approved process before
execution. Re-present the exact command, workspace, effects, costs and risks for
each approval. The proposed UUIDs below must be unused; collisions require new
exact commands and approval.

1. Identify candidate, in the proposed workspace:

   ```sh
   grok --no-auto-update --version
   ```

2. Capture source:

   ```sh
   grok --no-auto-update --no-ask-user --permission-mode dontAsk --no-subagents --no-plan --no-memory --verbatim --disable-web-search --max-turns 1 --model grok-4.6 --output-format streaming-json --session-id 01994fed-0000-7000-8000-000000000001 --single 'Remember marker FDSX_GROK_PARENT. Reply READY. Do not use tools or modify files.'
   ```

3. Create first independent child:

   ```sh
   grok --no-auto-update --no-ask-user --permission-mode dontAsk --no-subagents --no-plan --no-memory --verbatim --disable-web-search --max-turns 1 --model grok-4.6 --output-format streaming-json --resume 01994fed-0000-7000-8000-000000000001 --fork-session --session-id 01994fed-0000-7000-8000-000000000002 --single 'Recall the marker. Remember CHILD_A only here. Do not use tools or modify files.'
   ```

4. Create independent sibling, also probing retry isolation:

   ```sh
   grok --no-auto-update --no-ask-user --permission-mode dontAsk --no-subagents --no-plan --no-memory --verbatim --disable-web-search --max-turns 1 --model grok-4.6 --output-format streaming-json --resume 01994fed-0000-7000-8000-000000000001 --fork-session --session-id 01994fed-0000-7000-8000-000000000003 --single 'Recall the parent marker. Report whether a prior CHILD_A turn exists. Do not use tools or modify files.'
   ```

After separately approving two additional child invocations, run them concurrently
with UUIDs ending `0004` and `0005`, using command 4's remaining arguments. Expand
each full exact command before approval. Compare disposable parent native history
before/after child activity; record equality, parent links and IDs, not conversation
bodies. This is a qualification observation, not an FDSX mutation-detection feature.
Check independent durable child storage and the selected parent.

After process exit, approve a command 4 variant with child UUID ending `0006` to
test durable reuse. For FDSX recovery, prepare a minimal plan/child workflow with
a local wait between tasks; separately approve exact `fdsx run` and `fdsx resume`
commands, explicitly listing their version probes and native task calls. Verify
planning does not rerun, the saved parent is selected, and failed/invalid child
retries create fresh IDs. Concrete workflow files and commands must be reviewed
before approval. Also qualify deleted/unusable disposable history failing closed;
prepare exact deletion scope and the following invocation for separate approval.

Record version output, model/configuration names, storage/workspace, checks,
outcomes and limitations without credentials or raw conversations. Do not declare
a tested baseline until all relied-on native behaviors pass. Model switching
remains rejected and is excluded from this proposed check set.

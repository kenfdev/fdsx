# Tasks: fdsx Framework — Phase 3 (Wait + Checkpoint/Resume)

**Spec**: [spec.md](spec.md)
**Plan**: [plan/impl-plan.md](plan/impl-plan.md)
**Scope**: Phase 3 — Wait state with interrupt/resume, webhook notifications, checkpoint persistence with SqliteSaver, `fdsx list` command, and `prompt_file` support
**Primary Scenarios**: Scenario 3 (Human-in-the-Loop Approval Gate), Scenario 4 (Resumption from Interruption)
**Prerequisite**: All Phase 1-2 tasks (T001–T035) completed

---

## Phase 8: US4 — Wait State + Webhook Notifications (Scenario 3)

**Story Goal**: Pause flow execution at a Wait state, display a terminal prompt with choices, optionally send a webhook notification (e.g., Slack), and store the user's selection in `result_path`. Combined with a subsequent Choice state, this enables human-in-the-loop approval gates.

**Independent Test Criteria**:
- Wait state compiles to a LangGraph node that calls `interrupt()` with message and choices
- Terminal displays the wait prompt with numbered choices and reads user input
- User's selection is stored at `result_path` in the state dict
- Webhook notification is sent via httpx POST when `notify` is configured
- Webhook failure logs a warning but does not block the flow
- A flow with Wait → Choice routes correctly based on the user's selection

- [x] T036 [US4] Implement webhook notification module in `src/fdsx/notify/webhook.py`
  - `send_webhook(url: str, message: str) -> bool`: POST JSON payload `{"text": message}` to URL using httpx (sync client)
  - Set timeout of 10 seconds for the HTTP request
  - Return `True` on success (2xx status), `False` on any failure (network error, non-2xx, timeout)
  - Log warning on failure using structlog (do not raise exceptions — notification is auxiliary)
  - `send_notification(notify: NotifyConfig, state_dict: dict) -> None`: resolve `{variable}` references in `webhook.template` using `resolve_template`, then call `send_webhook`

- [x] T037 [P] [US4] Write unit tests for webhook notification in `tests/unit/test_webhook.py`
  - Test `send_webhook`: successful POST → returns True (mock httpx)
  - Test `send_webhook`: network error → returns False, logs warning
  - Test `send_webhook`: non-2xx status → returns False, logs warning
  - Test `send_webhook`: timeout → returns False, logs warning
  - Test `send_notification`: template variables are resolved before sending
  - Test `send_notification`: when webhook fails, no exception is raised

- [x] T038 [US4] Implement Wait state terminal display in `src/fdsx/display/terminal.py`
  - `display_wait_prompt(state_name: str, message: str, choices: list[str]) -> str`: display the wait prompt per CLI contract format:
    - Print `[HH:MM:SS] ⏸ {state_name} (waiting for input)` to stderr
    - Print blank line + message + blank line to stderr
    - Print numbered choices `[1] choice1`, `[2] choice2`, etc. to stderr
    - Print `Select (1-N): ` prompt and read from stdin
    - Validate input is a valid number in range; re-prompt on invalid input
    - Return the selected choice string (not the number)
  - Reference: contracts/cli.md "Wait State Prompt" for exact format

- [x] T039 [US4] Implement Wait state node using LangGraph `interrupt()` in `src/fdsx/core/compiler.py`
  - Modify `_create_wait_node` to implement the full Wait state logic:
    - Resolve `{variable}` references in `state.message` using `resolve_template`
    - If `state.notify` is configured: call `send_notification` (from webhook module) before prompting
    - Call `interrupt({"message": resolved_message, "choices": state.choices, "state_name": state_name})` to pause the graph
    - The interrupt payload is used by the resume handler to display the prompt
    - On resume, `interrupt()` returns the user's selection value
    - Store the selection at `state.result_path` in the state dict
  - Reference: research.md "Interrupt/Resume for Wait State" for LangGraph interrupt/Command pattern

- [x] T040 [US4] Update engine to handle interrupt and resume for Wait states in `src/fdsx/core/engine.py`
  - Modify `run_flow` to detect when the graph is interrupted (Wait state reached):
    - When `graph.stream()` yields an interrupt event, extract the interrupt payload (message, choices, state_name)
    - Call `display_wait_prompt` to show the terminal prompt and get user input
    - Resume the graph with `Command(resume=user_selection)` using `graph.invoke()`
    - Continue streaming until the graph completes or hits another interrupt
  - Handle multiple Wait states in a single flow (loop until graph completes)

- [x] T041 [US4] Create Wait state test fixtures in `tests/fixtures/`
  - `tests/fixtures/wait_approval.yaml`: Plan → Wait (approve/reject/retry choices) → Choice (routes on selection) → end states. Uses system provider for Plan state
  - `tests/fixtures/wait_webhook.yaml`: Same as above but with `notify.webhook` configured (URL can be a placeholder for testing)

- [x] T042 [US4] Write integration test for Wait state flow in `tests/integration/test_wait_resume.py`
  - Test Wait state prompt display: mock stdin to provide selection input, verify correct choice is stored at `result_path`
  - Test Wait → Choice routing: mock stdin to select "approve" → verify flow takes approve branch
  - Test Wait → Choice routing: mock stdin to select "reject" → verify flow takes reject branch
  - Test webhook notification: mock httpx to verify POST is sent with resolved template when `notify` is configured
  - Test webhook failure: mock httpx to fail → verify flow continues normally (warning logged, prompt still shown)

## Phase 9: US5 — Checkpoint/Resume + List + prompt_file (Scenario 4)

**Story Goal**: Enable crash-resilient execution by persisting checkpoints to SQLite after each state. Support resuming interrupted flows with `fdsx resume --thread-id`. Provide `fdsx list` to show all known flow executions. Support loading prompts from external files.

**Independent Test Criteria**:
- Checkpoints are saved to `.fdsx/checkpoints/checkpoints.db` via SqliteSaver
- `fdsx resume --thread-id <id>` restores state variables and resumes from the correct state
- PID-based lock files prevent concurrent execution of the same thread ID
- Stale locks (dead PIDs) are automatically cleaned up
- `fdsx list` displays thread IDs with status (running/waiting/stopped/completed)
- `prompt_file` loads prompt content from an external file with variable substitution
- A flow interrupted mid-execution can be resumed and produces the same result

- [ ] T043 [US5] Implement checkpoint manager in `src/fdsx/checkpoint/manager.py`
  - `CheckpointManager` class wrapping LangGraph's `SqliteSaver`:
    - `__init__(base_dir: Path | None = None)`: default base_dir is `.fdsx/` relative to CWD. Create `.fdsx/checkpoints/` and `.fdsx/locks/` directories if they don't exist
    - `get_checkpointer() -> SqliteSaver`: return `SqliteSaver.from_conn_string(str(base_dir / "checkpoints" / "checkpoints.db"))`
    - `acquire_lock(thread_id: str) -> bool`: create PID lock file at `.fdsx/locks/{thread_id}.lock` containing current PID. If lock file exists, check if PID is alive (via `os.kill(pid, 0)`). If alive → return False (concurrent execution). If dead → remove stale lock, acquire new lock → return True
    - `release_lock(thread_id: str) -> None`: remove lock file
    - `is_locked(thread_id: str) -> tuple[bool, int | None]`: check if thread is locked and return (is_locked, pid)
    - `list_threads() -> list[dict]`: scan checkpoint DB for known thread IDs, check lock status for each, return list of `{thread_id, status, flow_name}`
    - `verify_checkpoint(thread_id: str) -> bool`: verify checkpoint integrity (DB readable, thread_id exists in checkpoint store)
  - Reference: impl-plan.md "Checkpoint Directory Layout" for file structure

- [ ] T044 [P] [US5] Write unit tests for checkpoint manager in `tests/unit/test_checkpoint.py`
  - Test `acquire_lock`: new thread → acquires lock, returns True
  - Test `acquire_lock`: same thread, same PID → returns False (already locked)
  - Test `acquire_lock`: stale lock (dead PID) → removes stale lock, acquires new, returns True
  - Test `release_lock`: removes lock file
  - Test `is_locked`: locked thread → returns (True, pid)
  - Test `is_locked`: unlocked thread → returns (False, None)
  - Test directory creation: `.fdsx/checkpoints/` and `.fdsx/locks/` created on init
  - Test `verify_checkpoint`: valid checkpoint → True, missing thread → False

- [ ] T045 [US5] Integrate checkpoint manager into engine in `src/fdsx/core/engine.py`
  - Modify `run_flow` to use `CheckpointManager`:
    - Acquire lock before execution; raise error if locked by another process
    - Pass `SqliteSaver` checkpointer to `graph.compile(checkpointer=checkpointer)` (modify `compile_flow` to accept optional checkpointer)
    - Include `thread_id` in LangGraph config: `{"configurable": {"thread_id": thread_id}}`
    - Release lock on completion (success or error) using try/finally
  - Modify `compile_flow` in `src/fdsx/core/compiler.py` to accept an optional checkpointer parameter and pass it to `graph.compile(checkpointer=checkpointer)`
  - On error: print "Checkpoint saved. Resume with: fdsx resume --thread-id {thread_id}" to stderr

- [ ] T046 [US5] Implement `resume_flow` function in `src/fdsx/core/engine.py`
  - `resume_flow(thread_id: str, base_dir: Path | None = None) -> dict`: resume a flow from checkpoint
    - Create `CheckpointManager` and verify checkpoint integrity; raise error if corrupt
    - Acquire lock; raise error if locked by another process
    - Get the checkpointer and load the graph state for the given thread_id
    - Detect if the flow was interrupted at a Wait state: check for pending interrupts in the checkpoint
    - If interrupted at Wait: call `display_wait_prompt` with the interrupt payload, then resume with `Command(resume=user_selection)`
    - Continue streaming the graph until completion or next interrupt
    - Release lock on completion
    - Return final state variables
  - Print `Resuming from state: {state_name}` to stderr at start

- [ ] T047 [US5] Implement `fdsx resume` and `fdsx list` CLI commands in `src/fdsx/cli/main.py`
  - Add `resume` command:
    - `fdsx resume --thread-id TEXT`: call `engine.resume_flow(thread_id)`
    - Exit codes: 0=success, 1=flow error, 2=validation error (corrupt checkpoint)
    - Error messages: "No checkpoint found for thread ID {id}", "Thread {id} is locked by PID {pid}"
  - Add `list` command:
    - `fdsx list`: call `CheckpointManager().list_threads()`
    - Display table format per CLI contract: `THREAD_ID  FLOW_NAME  STATUS  CURRENT_STATE  STARTED_AT`
    - Status detection: check PID lock → running; check for pending interrupt → waiting; no lock + completed → completed; no lock + not completed → stopped
    - If no threads found, print "No flow executions found."

- [ ] T048 [P] [US5] Implement `prompt_file` support in `src/fdsx/core/compiler.py` and `src/fdsx/core/loader.py`
  - In `loader.py`: `prompt_file` paths are already validated as relative to YAML file location (check existing code)
  - In `compiler.py` `_create_task_node`: if `state.prompt_file` is set (and `state.prompt_template` is not):
    - Read the file content from the resolved path
    - Apply `resolve_template` to the file content (same as prompt_template)
    - Use the result as the prompt for the provider
  - Same for `_create_branch_executor`: handle `branch.prompt_file` in parallel branches
  - Store the YAML file path in `Flow` or pass it through compilation so file paths can be resolved at runtime
  - Reference: spec.md FR-2.1 for prompt_file behavior

- [ ] T049 [P] [US5] Write unit tests for `prompt_file` support in `tests/unit/test_prompt_file.py`
  - Test: prompt_file content is read and variable references are resolved
  - Test: prompt_file path is resolved relative to YAML file location
  - Test: prompt_file not found → clear error message
  - Test: prompt_file in parallel branch works the same as in Task state

- [ ] T050 [US5] Create checkpoint/resume test fixtures in `tests/fixtures/`
  - `tests/fixtures/checkpoint_flow.yaml`: 3-state linear flow (plan → implement → review) using system provider, suitable for interrupt/resume testing
  - `tests/fixtures/wait_resume_flow.yaml`: Plan → Wait → Choice → end flow, designed to test checkpoint saving at Wait state and resume after process restart

- [ ] T051 [US5] Write integration test for checkpoint/resume in `tests/integration/test_checkpoint_resume.py`
  - Test checkpoint save: run a flow → verify `.fdsx/checkpoints/checkpoints.db` exists
  - Test resume: run a flow with a Wait state → interrupt at Wait → resume with `resume_flow` → verify flow completes
  - Test PID lock: start a flow → attempt concurrent execution with same thread_id → verify lock error
  - Test stale lock cleanup: create a lock file with a dead PID → verify it's cleaned up on next acquire
  - Test checkpoint integrity: corrupt the checkpoint DB → verify `resume_flow` raises clear error
  - Test full Scenario 4: run a multi-state flow → simulate interrupt after first state → resume → verify all remaining states execute and final result is correct
  - Use tmp_path fixture for `.fdsx/` directory isolation between tests

## Phase 10: Polish & Cross-Cutting

- [ ] T052 Verify ruff and mypy pass with Phase 3 code in `pyproject.toml`
  - Run `uv run ruff check src/ tests/` — fix any lint issues in new files
  - Run `uv run mypy src/fdsx/` — fix any type errors in new files
  - Ensure all Phase 1-2 tests still pass: `uv run pytest tests/`

- [ ] T053 Write CLI e2e test for Phase 3 scenarios in `tests/integration/test_cli_e2e_phase3.py`
  - Test `fdsx run` with Wait state flow → mock stdin → exit code 0, JSON output contains selection result
  - Test `fdsx resume --thread-id` with a previously interrupted flow → exit code 0
  - Test `fdsx resume --thread-id` with non-existent thread → exit code 2, error message
  - Test `fdsx list` → verify table output format with at least one known thread
  - Test `fdsx list` with no threads → "No flow executions found."
  - Test `fdsx run` with `prompt_file` → verify prompt loaded from file and variables resolved
  - Use `subprocess.run` to invoke the actual CLI entry point

---

## Dependencies

```
Phase 1-2 (T001-T035) completed
  ↓
T036 → T037 (webhook module + tests)
T038 (wait display — independent)
T036 + T038 → T039 (wait node with interrupt + webhook)
T039 → T040 (engine interrupt/resume handling)
T040 → T041 → T042 (wait fixtures + integration test)
  ↓
T043 → T044 (checkpoint manager + tests)
T043 + T040 → T045 (integrate checkpoint into engine + compiler)
T045 → T046 (resume_flow function)
T046 → T047 (resume + list CLI commands)
T048 → T049 (prompt_file + tests — independent of checkpoint work)
T047 → T050 → T051 (checkpoint/resume fixtures + integration test)
  ↓
T042 + T051 + T049 → T052 → T053
```

**Critical path**: T036 → T039 → T040 → T043 → T045 → T046 → T047 → T050 → T051 → T052 → T053

**Parallel opportunities**:
- T036 (webhook) and T038 (wait display) and T043 (checkpoint manager) can all start in parallel
- T037 (webhook tests) can run in parallel with T038
- T048 (prompt_file) is independent and can run in parallel with all US4/checkpoint work
- T044 (checkpoint tests) can run in parallel with T040 (engine interrupt handling)

## Implementation Strategy

- **MVP**: Complete through T042 (Wait state with interrupt/resume in-memory). This enables Scenario 3 (human-in-the-loop approval gate) without persistent checkpointing.
- **Incremental delivery**: T036-T042 deliver Wait state + webhook (Scenario 3). T043-T051 add checkpoint persistence + resume + list (Scenario 4). T048-T049 add prompt_file. Each group is independently testable.
- **Key integration point**: T045 is the critical integration task — wiring SqliteSaver into the existing engine/compiler. The compiler's `graph.compile()` call changes from no-args to `compile(checkpointer=checkpointer)`.
- **No real LLMs needed**: All tests use `system` provider (echo commands). Webhook tests use mocked httpx.
- **stdin mocking**: Wait state tests require mocking stdin for user input. Use `unittest.mock.patch("builtins.input")` or similar.

## Summary

| Metric | Value |
|---|---|
| Total tasks | 18 (T036-T053) |
| US4 tasks (Wait + Webhook) | 7 (T036-T042) |
| US5 tasks (Checkpoint + List + prompt_file) | 9 (T043-T051) |
| Polish tasks | 2 (T052-T053) |
| Unit test tasks | 3 (T037, T044, T049) |
| Integration test tasks | 3 (T042, T051, T053) |
| Parallelizable tasks | 3 (T037, T044, T048) |

## Suggested takt Usage

```bash
# Phase 8: US4 — Webhook module + tests
takt run code "Implement webhook notification module (httpx POST, template variables, failure handling) in src/fdsx/notify/webhook.py and unit tests in tests/unit/test_webhook.py"

# Phase 8: US4 — Wait state display
takt run code "Implement Wait state terminal display (numbered choices, stdin input, re-prompt on invalid) in src/fdsx/display/terminal.py"

# Phase 8: US4 — Wait state node + engine interrupt handling
takt run code "Implement Wait state using LangGraph interrupt() in src/fdsx/core/compiler.py, update engine to handle interrupt/resume with Command(resume=value) in src/fdsx/core/engine.py"

# Phase 8: US4 — Wait state integration tests
takt run code "Create wait_approval.yaml and wait_webhook.yaml fixtures in tests/fixtures/, write integration tests for Wait state flow in tests/integration/test_wait_resume.py"

# Phase 9: US5 — Checkpoint manager + tests
takt run code "Implement CheckpointManager (SqliteSaver wrapper, PID lock files, stale lock detection) in src/fdsx/checkpoint/manager.py and unit tests in tests/unit/test_checkpoint.py"

# Phase 9: US5 — Checkpoint integration into engine + resume + list CLI
takt run code "Integrate CheckpointManager into engine, implement resume_flow in src/fdsx/core/engine.py, add fdsx resume and fdsx list commands in src/fdsx/cli/main.py"

# Phase 9: US5 — prompt_file support
takt run code "Implement prompt_file support (read file, resolve relative to YAML, variable substitution) in src/fdsx/core/compiler.py and src/fdsx/core/loader.py, write unit tests in tests/unit/test_prompt_file.py"

# Phase 9: US5 — Checkpoint/resume integration tests
takt run code "Create checkpoint_flow.yaml and wait_resume_flow.yaml fixtures in tests/fixtures/, write integration tests for checkpoint/resume in tests/integration/test_checkpoint_resume.py"

# Phase 10: Polish
takt run code "Verify ruff/mypy pass, run all tests, write CLI e2e tests for Phase 3 scenarios in tests/integration/test_cli_e2e_phase3.py"
```

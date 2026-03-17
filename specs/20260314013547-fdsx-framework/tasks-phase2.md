# Tasks: fdsx Framework — Phase 2 (Extraction + Parallel + Pass)

**Spec**: [spec.md](spec.md)
**Plan**: [plan/impl-plan.md](plan/impl-plan.md)
**Scope**: Phase 2 — Output extraction, true parallel execution via Send API, Pass state aggregation, and loop control
**Primary Scenarios**: Scenario 2 (Parallel Review + Majority Vote), Scenario 5 (Decision Value Extraction)
**Prerequisite**: All Phase 1 tasks (T001–T020) completed

---

## Phase 5: US2 — Output Extraction & Decision Routing (Scenario 5)

**Story Goal**: Reliably extract decision values (e.g., APPROVED/REJECTED) from LLM output using a deterministic fallback chain (json → regex → keyword), with optional 2-phase LLM classification. Extracted values drive Choice state routing.

**Independent Test Criteria**:
- json extraction parses ```json code blocks and raw JSON, retrieving a field by name
- regex extraction applies a pattern and returns the first match
- keyword extraction scans for pipe-delimited keywords (case-insensitive)
- Fallback chain tries strategies in order, stopping at first success
- LLM classify fallback is invoked only when all deterministic strategies fail
- Extraction integrates with Task state: extracted value stored at `extract.result_path`
- A flow with extraction + Choice state routes correctly based on extracted value

- [x] T021 [US2] Implement output extraction module in `src/fdsx/core/extraction.py`
  - `extract_value(output: str, extract_rule: ExtractRule, provider_factory: Callable | None = None, state_dict: dict | None = None) -> str | None`: run strategies in order, return first successful extraction
  - **json strategy**: Find ` ```json...``` ` code block → `json.loads` → lookup `pattern` as field name. If no code block, try `json.loads` on entire output → lookup `pattern`. Return `None` on failure
  - **regex strategy**: Apply `pattern` as regex to raw output → return first capture group (or full match if no groups). Return `None` on failure
  - **keyword strategy**: Split `pattern` by `|` → scan output for first occurrence (case-insensitive). Return the matched keyword. Return `None` on failure
  - **Fallback chain**: Iterate `strategy` list, call each strategy function. Return first non-None result
  - **LLM classify fallback**: If all strategies return None and `fallback` is configured, invoke `fallback.provider` with `fallback.prompt` (substitute `{output}` with the raw output). Parse LLM response as the extracted value. Return `None` if LLM call fails
  - Reference: spec.md FR-6, contracts/yaml-schema.md "Extraction Contract"

- [x] T022 [US2] Write unit tests for extraction in `tests/unit/test_extraction.py`
  - Test json strategy: output with ```json code block containing target field → extracts value
  - Test json strategy: output is raw JSON object → extracts field value
  - Test json strategy: output has no JSON → returns None
  - Test regex strategy: pattern with capture group → returns first group
  - Test regex strategy: pattern without groups → returns full match
  - Test regex strategy: no match → returns None
  - Test keyword strategy: `"APPROVED|REJECTED"` with output containing "approved" (case-insensitive) → returns "APPROVED"
  - Test keyword strategy: no keywords found → returns None
  - Test fallback chain: `[json, regex, keyword]` tries in order, first success wins
  - Test fallback chain: all fail, no LLM fallback → returns None
  - Test LLM classify fallback: all strategies fail, LLM fallback configured → calls provider and returns result (mock provider)
  - Test LLM classify fallback: LLM also fails → returns None

- [x] T023 [US2] Integrate extraction into Task state execution in `src/fdsx/core/compiler.py`
  - Modify `_create_task_node` to check if `state.extract` is defined
  - After provider execution, if `extract` exists: call `extract_value(output, state.extract)`
  - If extraction succeeds: store extracted value at `state.extract.result_path` in state dict
  - If extraction fails (returns None): treat as error, trigger retry (existing retry logic applies)
  - Also integrate extraction into `_create_parallel_node` for branch-level `extract` rules: each branch result object includes both `output` (raw) and extracted fields at `extract.result_path` key

- [x] T024 [US2] Create extraction test fixtures and integration test in `tests/fixtures/extraction_flow.yaml` and `tests/integration/test_extraction_flow.py`
  - Create `tests/fixtures/extraction_flow.yaml`: 3-state flow using system provider: (1) echo state outputs text containing "APPROVED", (2) extract state with `extract: {strategy: [keyword], pattern: "APPROVED|REJECTED", result_path: $.decision}`, (3) Choice state routes on `$.decision`
  - Integration test: run extraction flow → verify `$.decision` is set to "APPROVED" → verify correct branch taken
  - Integration test: test regex extraction with system provider echoing structured output
  - Integration test: test json extraction with system provider echoing JSON in a code block

## Phase 6: US3 — Parallel Execution + Aggregation + Loop Control (Scenario 2)

**Story Goal**: Execute multiple LLM branches in true parallel via LangGraph Send API, aggregate results with majority/all/any voting in Pass state, and enforce loop control for review-replan cycles.

**Independent Test Criteria**:
- Parallel state uses LangGraph Send API for true fan-out (not sequential)
- Each branch result is collected into an array at `result_path`
- `min_success` allows partial failure (flow continues if enough branches succeed)
- Failed branches are retried individually (successful branches preserved)
- Pass state `aggregate` computes majority/all/any vote and stores result
- Loop control enforces `max_loop` and stops gracefully as "loop completed"
- Full Scenario 2 flow: parallel review → aggregate → choice → loop or complete

- [x] T025 [US3] Implement aggregation logic in Pass state in `src/fdsx/core/compiler.py`
  - Modify `_create_pass_node` to handle `state.aggregate` when defined
  - `_aggregate(source_data: list[dict], rule: AggregateRule) -> str`: resolve `source` JSONPath to get array, extract `field` from each element, apply strategy:
    - **majority**: count matches for `rule.match` value → if > len/2, return `rule.match`, else return `rule.no_match`
    - **all**: all elements match `rule.match` → return `rule.match`, else `rule.no_match`
    - **any**: at least one matches `rule.match` → return `rule.match`, else `rule.no_match`
  - Store result at `aggregate.result_path` in state dict
  - Reference: spec.md FR-4 for aggregation strategy definitions

- [x] T026 [US3] Write unit tests for aggregation strategies in `tests/unit/test_aggregation.py`
  - Test majority strategy: 2/3 match → returns match value
  - Test majority strategy: 1/3 match → returns no_match value
  - Test majority strategy: 0 match → returns no_match value
  - Test all strategy: 3/3 match → returns match, 2/3 → returns no_match
  - Test any strategy: 1/3 match → returns match, 0/3 → returns no_match
  - Test with empty source array → returns no_match
  - Test integration with Pass node: aggregate block processes parallel results correctly

- [x] T027 [P] [US3] Refactor parallel execution to use LangGraph Send API in `src/fdsx/core/compiler.py`
  - Replace sequential branch execution in `_create_parallel_node` with LangGraph Send API fan-out/fan-in
  - Create a `_create_branch_node(branch_index: int, branch: Branch, parent_state_name: str) -> Callable`: node function for a single branch execution (provider call, extraction if configured)
  - Create fan-out function: `_create_fan_out(state_name: str, state: ParallelState) -> Callable` that returns `list[Send]` — one `Send("_branch_{state_name}_{i}", {...})` per branch
  - Modify graph construction: for ParallelState, add branch nodes + conditional edges for fan-out + fan-in collector node
  - Fan-in collector: gather branch results into array at `result_path`, enforce `min_success` (count successful branches, error if below threshold)
  - Branch results format: `{output: str, exit_code: int, error: str | None}` plus extracted fields if `extract` is configured
  - Update `_extract_result_paths` to handle ParallelState extraction result paths
  - Reference: research.md "Parallel Execution via Send API" for LangGraph Send pattern

- [x] T028 [US3] Implement per-branch retry with min_success enforcement in `src/fdsx/core/compiler.py`
  - In the branch node function: retry failed branches up to `branch.retry` times (existing retry pattern)
  - In the fan-in collector: count branches with `exit_code == 0`
  - If successful count < `min_success` (default: total branch count): raise error with details of which branches failed
  - If successful count >= `min_success`: proceed normally, include all results (both success and failure) in the array
  - Failed branch results in array: `{output: "", exit_code: N, error: "message"}`

- [x] T029 [P] [US3] Add parallel execution display to terminal in `src/fdsx/display/terminal.py`
  - `display_parallel_start(state_name: str, branch_count: int)`: print `[HH:MM:SS] ▶ state_name (parallel, N branches)`
  - `display_branch_status(state_name: str, branch_index: int, provider: str, status: str, duration: float | None)`: print branch status line per CLI contract format: `  [branch-N] provider/model  status`
  - Status values: `⏳ running...`, `✓ completed (Xs)`, `✗ failed`
  - Reference: contracts/cli.md "Parallel Execution Status" for format

- [x] T030 [US3] Implement loop control in `src/fdsx/core/engine.py`
  - The current `recursion_limit` calculation in `run_flow` already maps `max_loop` to LangGraph's `recursion_limit`
  - Add graceful handling: catch LangGraph's `GraphRecursionError` and convert to a "Loop completed" message rather than a raw error
  - Display "Loop completed after N iterations" on stderr when max_loop is reached
  - Return partial results (last state before loop limit) rather than raising an error
  - Reference: spec.md FR-2.6 for loop behavior

- [x] T031 [US3] Create parallel + aggregation test fixtures in `tests/fixtures/`
  - `tests/fixtures/parallel_review.yaml`: Parallel state with 3 system provider branches (echo commands outputting APPROVED/REJECTED), followed by Pass state with majority aggregation, followed by Choice state routing on decision
  - `tests/fixtures/loop_flow.yaml`: Plan → Implement → Review → Choice (APPROVED → end, REJECTED → back to Plan) flow with `max_loop: 3`, using system provider
  - `tests/fixtures/parallel_min_success.yaml`: Parallel state with 3 branches, one designed to fail (exit code 1), `min_success: 2` — tests partial failure tolerance

- [x] T032 [US3] Write integration test for parallel flow in `tests/integration/test_parallel_flow.py`
  - Test end-to-end: load `parallel_review.yaml` → execute → verify all 3 branches ran
  - Verify branch results array at `result_path` contains 3 elements with `output` field
  - Verify Pass state aggregation produces correct `$.decision` value
  - Verify Choice state routes correctly based on aggregated decision
  - Test `min_success`: load `parallel_min_success.yaml` → verify flow continues despite 1 failed branch
  - Test `min_success` failure: all branches fail → flow errors

- [x] T033 [US3] Write integration test for loop flow in `tests/integration/test_loop_flow.py`
  - Test loop execution: load `loop_flow.yaml` → verify flow loops back to planner on REJECTED
  - Test max_loop enforcement: flow with always-REJECTED review → verify graceful stop after max_loop iterations
  - Verify state variables are retained across loop iterations (previous review results available in next plan prompt)

## Phase 7: Polish & Cross-Cutting

- [ ] T034 Verify ruff and mypy pass with Phase 2 code in `pyproject.toml`
  - Run `uv run ruff check src/ tests/` — fix any lint issues in new files
  - Run `uv run mypy src/fdsx/` — fix any type errors in new files
  - Ensure all Phase 1 tests still pass: `uv run pytest tests/`

- [ ] T035 Write CLI e2e test for Phase 2 scenarios in `tests/integration/test_cli_e2e_phase2.py`
  - Test `fdsx run` with `parallel_review.yaml` → exit code 0, JSON output contains decision
  - Test `fdsx run` with `extraction_flow.yaml` → exit code 0, extracted value in output
  - Test `fdsx run` with `loop_flow.yaml` → verify loop behavior from CLI
  - Use `subprocess.run` to invoke the actual CLI entry point

---

## Dependencies

```
Phase 1 (T001-T020) completed
  ↓
T021 → T022 (extraction + tests)
T021 → T023 (integrate extraction into compiler)
T023 → T024 (extraction integration test)
  ↓
T025 → T026 (aggregation + tests)
T027 → T028 (parallel Send API + min_success)
T029 (parallel display — independent after T027)
T030 (loop control — independent)
  ↓
T025 + T027 + T028 → T031 → T032, T033
T030 → T033
  ↓
T024 + T032 + T033 → T034 → T035
```

**Critical path**: T021 → T023 → T027 → T028 → T031 → T032 → T034 → T035

**Parallel opportunities**:
- T021 (extraction) and T025 (aggregation) can start in parallel
- T027 (Send API refactor) can start once T021 is done (needs extraction integration)
- T029 (parallel display) can run in parallel with T028 (min_success)
- T030 (loop control) is independent of extraction/parallel work

## Implementation Strategy

- **MVP**: Complete through T032 (parallel flow with aggregation). This enables the full Scenario 2 (parallel review + majority vote) workflow.
- **Incremental delivery**: T021-T024 deliver extraction (Scenario 5). T025-T033 add parallel + aggregation + loop (Scenario 2). Each group is independently testable.
- **Refactoring scope**: T027 is the largest task — refactoring sequential parallel to Send API. The existing `_create_parallel_node` provides the branch execution logic to reuse.
- **No real LLMs needed**: All tests use `system` provider (echo commands). LLM classify fallback tests use mock providers.
- **Provider tasks skipped**: opencode and codex providers already implemented in Phase 1.

## Summary

| Metric | Value |
|---|---|
| Total tasks | 15 (T021-T035) |
| US2 tasks (Extraction) | 4 (T021-T024) |
| US3 tasks (Parallel+Aggregation+Loop) | 9 (T025-T033) |
| Polish tasks | 2 (T034-T035) |
| Unit test tasks | 2 (T022, T026) |
| Integration test tasks | 4 (T024, T032, T033, T035) |
| Parallelizable tasks | 2 (T027, T029) |

## Suggested takt Usage

```bash
# Phase 5: US2 — Extraction module + tests
takt run code "Implement output extraction (json/regex/keyword strategies + LLM classify fallback) in src/fdsx/core/extraction.py and unit tests in tests/unit/test_extraction.py"

# Phase 5: US2 — Extraction integration into compiler
takt run code "Integrate extraction into Task and Parallel state execution in src/fdsx/core/compiler.py, create extraction_flow.yaml fixture and integration test in tests/integration/test_extraction_flow.py"

# Phase 6: US3 — Aggregation in Pass state
takt run code "Implement majority/all/any aggregation in Pass state in src/fdsx/core/compiler.py and unit tests in tests/unit/test_aggregation.py"

# Phase 6: US3 — Parallel Send API refactor + min_success + display
takt run code "Refactor _create_parallel_node to use LangGraph Send API for true fan-out/fan-in in src/fdsx/core/compiler.py, implement per-branch retry with min_success, add parallel display in src/fdsx/display/terminal.py"

# Phase 6: US3 — Loop control
takt run code "Implement graceful loop control (catch GraphRecursionError, return partial results) in src/fdsx/core/engine.py"

# Phase 6: US3 — Parallel + Loop integration tests
takt run code "Create parallel_review.yaml, loop_flow.yaml, parallel_min_success.yaml fixtures in tests/fixtures/, write integration tests in tests/integration/test_parallel_flow.py and test_loop_flow.py"

# Phase 7: Polish
takt run code "Verify ruff/mypy pass, run all tests, write CLI e2e tests for Phase 2 scenarios in tests/integration/test_cli_e2e_phase2.py"
```

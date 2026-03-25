# Agent Instructions

## Testing Guidelines

### Test Trophy Strategy

This project follows the test trophy pattern. Tests are organized into three layers:

#### Directory Structure
- `tests/unit/` — Unit tests for complex pure logic
- `tests/integration/` — Integration tests (primary confidence layer)
- `tests/e2e/` — CLI end-to-end tests (thinnest layer)

#### What belongs at each level

**Unit tests** (`tests/unit/`):
- Complex parsing logic (YAML parsing, JSONPath resolution, variable substitution)
- State transition rules and algorithms
- Pure functions with non-trivial logic
- NOT: Pydantic model field assignments, default value checks, isinstance checks

**Integration tests** (`tests/integration/`):
- Complete workflow execution via `engine.run_flow()`
- Feature-centered tests: each file answers "Does feature X work correctly?"
- Checkpoint persistence and recovery
- State variable mutations and result paths
- Tests using `CliRunner` for testing CLI behavior with mocked internals

**E2E tests** (`tests/e2e/`):
- CLI surface tests via `run_fdsx()` subprocess calls
- Exit codes, stderr/stdout format validation
- CLI argument parsing and mutual exclusion
- Signal handling

#### Anti-patterns to avoid

- **Trivial field assertion tests**: Don't test that `TaskState(type="task").type == "task"`. Pydantic guarantees this.
- **isinstance checks**: Don't test `isinstance(generate_thread_id(), str)`. The type system handles this.
- **Default value tests**: Don't test that `ClaudeOptions().permission_mode is None`. This is framework behavior.
- **Unnecessary real-time waits**: Mock `time.sleep` for in-process delays. Minimize subprocess sleep durations.
- **Writing artifacts to project root**: Tests must never create `.fdsx/` artifacts in the project root. Always use `monkeypatch.chdir(tmp_path)` or `cwd=tmp_dir` for subprocess tests.

#### Naming conventions

- Test files: `test_<feature>.py` (never `test_phase1.py` or `test_e2e_phase2.py`)
- Test functions: `test_<scenario>_<expected_outcome>`
- Test classes: `Test<Feature><Aspect>` (e.g., `TestCheckpointResume`, `TestChoiceStateValidation`)

#### Integration Test Feature-Centeredness Assessment

All integration tests are already feature-centered. No files need restructuring beyond the e2e moves. Each integration test file is organized around a specific feature:

- `test_checkpoint_resume.py` — checkpoint and resume behavior
- `test_choice_flow.py` — choice state routing
- `test_parallel_flow.py` — parallel execution
- `test_linear_flow.py` — linear workflow execution
- `test_loop_flow.py` — loop state behavior
- `test_extraction_flow.py` — data extraction
- `test_quiet_mode.py` — quiet mode flag
- `test_result_file.py` — result file output
- `test_resume_interrupt.py` — interrupted workflow recovery
- `test_split.py` — batch split behavior
- `test_tasks_dir.py` — tasks directory handling
- `test_auto_select.py` — auto-select logic
- `test_lock_atomicity.py` — lock file atomicity
- `test_workflow_persistence.py` — workflow state persistence
- `test_inactivity_timeout.py` — inactivity timeout handling
- `test_scenario_flows.py` — cross-cutting scenario flows

The integration test suite is well-organized around features (checkpoint, choice, parallel, loop, extraction, etc.) rather than implementation phases. No restructuring was required.

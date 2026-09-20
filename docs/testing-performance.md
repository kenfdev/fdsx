# Test suite performance

## Running tests

```sh
uv sync --extra dev
uv run pytest tests/ -q
```

The default uses pytest-xdist with work stealing and half the CPUs available to
this process (rounded down, minimum one worker). On platforms supporting CPU
affinity, the affinity mask determines availability; otherwise `os.cpu_count()`
is used. Container CPU quotas are not detected. Use an explicit worker count
when a quota is lower than the visible CPU count:

```sh
uv run pytest tests/ -n 2 -q
```

For a serial baseline or debugging:

```sh
uv run pytest tests/ -n 0 -q --durations=40
```

## Measurement

Local Python 3.12 environment with 16 available CPUs, hence eight workers.
These are individual runs, not statistical benchmarks; other machines and CI
will have different timings. The 60-second target applies to this local full
suite without coverage instrumentation.

| Suite | Workers | Tests passed | Seconds |
| --- | ---: | ---: | ---: |
| Before cleanup | 0 | 3655 | 326.51 |
| Before cleanup | 8 | 3655 | 56.09 |
| After cleanup | 0 | 3634 | 215.49 |
| After cleanup | 8 | 3634 | 37.99 |
| After cleanup, with coverage | 8 | 3634 | 44.59 |

Coverage after cleanup: 93.07%, above the existing 80% requirement.
No production files were changed. Timeouts and completion grace periods in
production remain unchanged.

Do not attribute the entire serial speedup to cleanup. The four changed
subprocess/signal files fell from 98.53 seconds to 30.95 seconds in aggregate,
about 67.58 seconds saved. Unchanged files also became faster between runs,
so cache state or system load may explain part of the remaining difference.
The large end-to-end speedup also depends on eight-worker parallelism.

### Execution evidence

JUnit XML was checked by test identity, not just the terminal summary:

- Original serial run: 3,655 unique tests, zero skips, zero failures/errors.
- Final serial run: 3,634 unique tests, zero skips, zero failures/errors.
- Final parallel run: the exact same 3,634 identities, each executed once.
- A fresh serial `--collect-only -q` run matched those 3,634 executed identities.
- There are 24 removed identities and three added consolidated/parameterized
  identities, for a net reduction of 21. The mappings below explain the cleanup.

### CI

`.github/workflows/test.yml` retains all four Python versions (3.10–3.13), type
checks, dependency audits, and the 80% coverage gate. It now uses cached uv
installs from `uv.lock`, runs lint/format only in the dedicated lint job, and
explicitly enables the same half-CPU parallel test configuration.

Each Python job uploads `test-results.xml` and `coverage.xml` as a
`test-evidence-python-<version>` artifact, even if the test step fails. The
JUnit report records each test, its elapsed time, and any skips or failures.
The job log also shows the 20 slowest tests. Reports are retained for 14 days.

This workflow change has not been executed on GitHub Actions yet. Hosted runner
CPU counts, dependency installation, and instrumentation affect elapsed time.
A four-CPU runner uses two workers; a two-CPU runner uses one. The local
38-second result is not a CI runtime promise.

## Consolidated guarantees

Integration tests are the primary confidence layer. Tests are removed only when
another test covers the same behavior, or the assertion only checks a type or
constant. Unique subprocess and signal behavior continues to use real local
processes, never real AI provider binaries.

| Removed or reduced work | Retained guarantee |
| --- | --- |
| Five provider-option unit tests | `tests/integration/test_provider_options.py` covers `test_workflow_options_override_config`, `test_get_provider_with_none_options_returns_default_provider`, `test_system_provider_unaffected_by_options`, `test_claude_dangerously_skip_permissions_flag`, and `test_opencode_provider_flags_applied`. These exercise factory wiring or the generated command rather than repeating field assertions. |
| Unit hanging-completion and response-preservation runs, plus a duplicate integration hanging run | `tests/integration/test_subprocess_completion.py::TestHangingProvider::test_hanging_provider_terminates_with_output_preserved` checks the time bound, non-timeout result, complete stdout, and streamed callback data in one run. |
| Separate forced-termination logging run | `tests/unit/test_subprocess_completion.py::TestCompletionEvent::test_sigterm_resistant_process_force_killed` also checks the debug message. The real SIGTERM-resistant child and full termination cascade remain. |
| Unit unset-completion-event run | `tests/integration/test_subprocess_completion.py::TestNoCompletionSignal::test_no_completion_signal_unset_event_behaves_like_none` also checks the latency bound. |
| Five unit inactivity-parameter runs | `tests/integration/test_inactivity_timeout.py` covers zero disabling the watchdog, inactivity exit code, timeout text including the threshold, and discarded partial stdout. The default `None` path remains exercised in the no-completion integration tests. |
| Two constant/type assertions | Removed assertions of `DEFAULT_INACTIVITY_TIMEOUT == 300` and `isinstance(..., int)`. Provider behavior tests remain; these assertions do not exercise behavior. |
| Twenty FD-cleanup iterations per path | Two calls per path retain the actual `Popen` objects and assert their pipes are closed. Retention exposes a missing close on the first call, so twenty repetitions add no distinct guarantee. Normal exit, timeout, inactivity, and completion paths all remain. |
| Six CLI signal runs | Two parameterized runs retain SIGINT/SIGTERM exit codes and lock removal, SIGINT messaging, and child cleanup. Child cleanup now also covers SIGTERM. |
| Fixed 1.5-second delay before each signal | Poll a child-created readiness file, with a bounded startup deadline. Assert that descendants existed so cleanup cannot pass without interrupting an active child. |

The runtime reduction is not solely from deleting test cases: fewer repeated
subprocess calls and readiness-based waits remove work within retained tests.

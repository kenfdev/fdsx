"""TDD tests for stdout-in-daemon-thread refactor (T001, T002) and
completion_event termination cascade (T003, T004).

These tests verify that _run_subprocess correctly collects stdout and stderr via
daemon threads, preserving all existing behavioral contracts:
  - ProviderResult.stdout / .stderr / .exit_code correctness
  - output_callback / stderr_callback called once per line, no trailing newline
  - Timeout still kills the process and returns exit_code=124
  - Both stdout and stderr collected correctly when both are present

T001: Write these tests first (TDD — they describe the expected behavior).
T002: Refactor _run_subprocess to run stdout reading in a daemon thread.

T003: Write tests for completion_event parameter and termination cascade.
T004: Implement completion_event support and termination cascade in _run_subprocess.
"""

import logging
import sys
import threading
import time

from fdsx.providers.base import _run_subprocess

# Use sys.executable so tests run with the same Python interpreter as the test
# runner, regardless of PATH or virtualenv configuration.
_PYTHON = sys.executable


class TestStdoutCollection:
    """ProviderResult.stdout correctness with stdout-in-daemon-thread."""

    def test_stdout_single_line(self):
        """Single stdout line is returned in ProviderResult.stdout."""
        result = _run_subprocess(args=["echo hello"], shell=True)

        assert result.exit_code == 0
        assert result.stdout == "hello"

    def test_stdout_multiline_joined_by_newline(self):
        """Multiple stdout lines are joined with newlines in ProviderResult.stdout."""
        result = _run_subprocess(
            args=["printf 'line1\\nline2\\nline3\\n'"],
            shell=True,
        )

        assert result.exit_code == 0
        assert result.stdout == "line1\nline2\nline3"

    def test_stdout_empty(self):
        """Process with no stdout produces empty string in ProviderResult.stdout."""
        result = _run_subprocess(args=["true"], shell=True)

        assert result.exit_code == 0
        assert result.stdout == ""

    def test_stdout_preserved_with_stderr_also_present(self):
        """ProviderResult.stdout is correct when stderr is also produced."""
        result = _run_subprocess(
            args=["echo out_line; echo err_line >&2"],
            shell=True,
        )

        assert result.exit_code == 0
        assert result.stdout == "out_line"
        assert result.stderr == "err_line"


class TestStderrCollection:
    """ProviderResult.stderr correctness with stdout-in-daemon-thread."""

    def test_stderr_single_line(self):
        """Single stderr line is returned in ProviderResult.stderr."""
        result = _run_subprocess(args=["echo error_msg >&2"], shell=True)

        assert result.exit_code == 0
        assert result.stderr == "error_msg"

    def test_stderr_multiline_joined_by_newline(self):
        """Multiple stderr lines are joined with newlines in ProviderResult.stderr."""
        result = _run_subprocess(
            args=["printf 'err1\\nerr2\\nerr3\\n' >&2"],
            shell=True,
        )

        assert result.exit_code == 0
        assert result.stderr == "err1\nerr2\nerr3"

    def test_stderr_empty(self):
        """Process with no stderr produces empty string in ProviderResult.stderr."""
        result = _run_subprocess(args=["echo stdout_only"], shell=True)

        assert result.exit_code == 0
        assert result.stderr == ""


class TestExitCodes:
    """Exit code correctness with stdout-in-daemon-thread."""

    def test_exit_code_zero(self):
        """Successful process returns exit_code=0."""
        result = _run_subprocess(args=["true"], shell=True)
        assert result.exit_code == 0

    def test_exit_code_nonzero(self):
        """Process exiting with non-zero code is reflected in result."""
        result = _run_subprocess(args=["exit 42"], shell=True)
        assert result.exit_code == 42

    def test_exit_code_one(self):
        """Process exiting with code 1 is reflected in result."""
        result = _run_subprocess(args=["false"], shell=True)
        assert result.exit_code == 1

    def test_exit_code_with_stdout_output(self):
        """Non-zero exit code is preserved even when stdout was produced."""
        result = _run_subprocess(
            args=["echo output_before_fail; exit 5"],
            shell=True,
        )
        assert result.exit_code == 5
        assert result.stdout == "output_before_fail"


class TestOutputCallback:
    """output_callback is called per stdout line with daemon-thread reader."""

    def test_output_callback_called_for_each_line(self):
        """output_callback is invoked once per stdout line."""
        received: list[str] = []

        result = _run_subprocess(
            args=["printf 'a\\nb\\nc\\n'"],
            shell=True,
            output_callback=received.append,
        )

        assert result.exit_code == 0
        assert received == ["a", "b", "c"]

    def test_output_callback_lines_have_no_trailing_newline(self):
        """Lines delivered to output_callback do not include trailing newline."""
        received: list[str] = []

        _run_subprocess(
            args=["printf 'lineA\\nlineB\\n'"],
            shell=True,
            output_callback=received.append,
        )

        for line in received:
            assert not line.endswith("\n"), f"Line has trailing newline: {line!r}"

    def test_output_callback_none_does_not_raise(self):
        """output_callback=None (default) works without errors."""
        result = _run_subprocess(
            args=["echo some_output"],
            shell=True,
            output_callback=None,
        )

        assert result.exit_code == 0
        assert result.stdout == "some_output"

    def test_output_callback_and_result_stdout_consistent(self):
        """output_callback lines match ProviderResult.stdout when joined."""
        received: list[str] = []

        result = _run_subprocess(
            args=["printf 'x\\ny\\nz\\n'"],
            shell=True,
            output_callback=received.append,
        )

        assert result.exit_code == 0
        assert "\n".join(received) == result.stdout


class TestStderrCallbackThreaded:
    """stderr_callback is called per stderr line with daemon-thread reader."""

    def test_stderr_callback_called_for_each_line(self):
        """stderr_callback is invoked once per stderr line."""
        received: list[str] = []

        result = _run_subprocess(
            args=["printf 'e1\\ne2\\n' >&2"],
            shell=True,
            stderr_callback=received.append,
        )

        assert result.exit_code == 0
        assert received == ["e1", "e2"]

    def test_stderr_callback_lines_have_no_trailing_newline(self):
        """Lines delivered to stderr_callback do not include trailing newline."""
        received: list[str] = []

        _run_subprocess(
            args=["printf 'errA\\nerrB\\n' >&2"],
            shell=True,
            stderr_callback=received.append,
        )

        for line in received:
            assert not line.endswith("\n"), f"Line has trailing newline: {line!r}"

    def test_both_callbacks_independent(self):
        """output_callback and stderr_callback are called independently."""
        stdout_received: list[str] = []
        stderr_received: list[str] = []

        result = _run_subprocess(
            args=["echo stdout_val; echo stderr_val >&2"],
            shell=True,
            output_callback=stdout_received.append,
            stderr_callback=stderr_received.append,
        )

        assert result.exit_code == 0
        assert stdout_received == ["stdout_val"]
        assert stderr_received == ["stderr_val"]


class TestTimeout:
    """Timeout still works correctly with stdout-in-daemon-thread."""

    def test_timeout_kills_process_and_returns_124(self):
        """Process exceeding timeout is killed and result has exit_code=124."""
        result = _run_subprocess(
            args=["sleep 60"],
            shell=True,
            timeout=1,
        )

        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()

    def test_timeout_not_triggered_for_fast_process(self):
        """Fast process completes normally and is not killed by timeout."""
        result = _run_subprocess(
            args=["echo fast"],
            shell=True,
            timeout=10,
        )

        assert result.exit_code == 0
        assert result.stdout == "fast"

    def test_timeout_output_collected_before_kill(self):
        """Output emitted before timeout fires is discarded (result stdout is empty on timeout)."""
        result = _run_subprocess(
            args=["echo before_timeout; sleep 60"],
            shell=True,
            timeout=1,
        )

        assert result.exit_code == 124
        assert result.stdout == ""

    def test_elapsed_time_is_bounded_by_timeout(self):
        """_run_subprocess returns within a reasonable multiple of the timeout value."""
        start = time.time()
        result = _run_subprocess(
            args=["sleep 60"],
            shell=True,
            timeout=1,
        )
        elapsed = time.time() - start

        assert result.exit_code == 124
        # timeout(1s) + reader thread join timeouts (2x1s) + overhead
        assert elapsed < 6, f"Took {elapsed:.1f}s — too slow"


class TestCompletionEvent:
    """completion_event triggers termination cascade and preserves collected data."""

    def test_completion_event_terminates_hanging_process(self):
        """Firing completion_event terminates a hanging process and returns collected data."""
        event = threading.Event()
        timer = threading.Timer(0.5, event.set)
        timer.start()
        try:
            start = time.time()
            result = _run_subprocess(
                args=[_PYTHON, "-c", "import sys,time; print('output',flush=True); time.sleep(999)"],
                completion_event=event,
            )
            elapsed = time.time() - start
        finally:
            timer.cancel()

        assert result.exit_code != 124, "Should not be a timeout result"
        assert "output" in result.stdout
        # Should terminate well within the cascade max time (5s wait + 5s after SIGTERM)
        assert elapsed < 20, f"Took {elapsed:.1f}s — too slow"

    def test_process_exits_voluntarily_after_completion_event(self):
        """Process that exits within 5s of completion_event is not force-killed."""
        event = threading.Event()
        # Fire event at 0.3s; process sleeps only 1s so it exits voluntarily
        timer = threading.Timer(0.3, event.set)
        timer.start()
        try:
            start = time.time()
            result = _run_subprocess(
                args=[_PYTHON, "-c", "import time; print('voluntary',flush=True); time.sleep(1)"],
                completion_event=event,
            )
            elapsed = time.time() - start
        finally:
            timer.cancel()

        assert result.exit_code != 124
        assert "voluntary" in result.stdout
        # Should complete well under the 5s voluntary-exit window
        assert elapsed < 8, f"Took {elapsed:.1f}s — too slow"

    def test_sigterm_resistant_process_force_killed(self):
        """Process that ignores SIGTERM is force-killed via SIGKILL after cascade."""
        event = threading.Event()
        # Fire immediately; process ignores SIGTERM and hangs
        timer = threading.Timer(0.2, event.set)
        timer.start()
        try:
            start = time.time()
            result = _run_subprocess(
                args=[
                    _PYTHON, "-c",
                    "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                    " print('output',flush=True); time.sleep(999)",
                ],
                completion_event=event,
            )
            elapsed = time.time() - start
        finally:
            timer.cancel()

        assert result.exit_code != 124
        assert "output" in result.stdout
        # Cascade: 5s voluntary wait + SIGTERM + 5s SIGTERM wait + SIGKILL
        assert elapsed < 20, f"Took {elapsed:.1f}s — too slow"

    def test_completion_event_not_set_waits_for_eof(self):
        """An unset completion_event behaves identically to no event (waits for EOF)."""
        event = threading.Event()  # never set

        result = _run_subprocess(
            args=[_PYTHON, "-c", "print('fast_exit',flush=True)"],
            completion_event=event,
        )

        assert result.exit_code == 0
        assert result.stdout == "fast_exit"

    def test_response_data_preserved_after_completion(self):
        """All lines emitted before the hang are present in result.stdout."""
        event = threading.Event()
        timer = threading.Timer(0.5, event.set)
        timer.start()
        try:
            result = _run_subprocess(
                args=[
                    _PYTHON, "-c",
                    "import time;"
                    " print('line1',flush=True);"
                    " print('line2',flush=True);"
                    " print('line3',flush=True);"
                    " time.sleep(999)",
                ],
                completion_event=event,
            )
        finally:
            timer.cancel()

        assert "line1" in result.stdout
        assert "line2" in result.stdout
        assert "line3" in result.stdout

    def test_debug_log_on_forced_termination(self, caplog):
        """Debug log is emitted when process does not exit voluntarily."""
        event = threading.Event()
        # Process ignores SIGTERM → SIGKILL path; should produce debug log
        timer = threading.Timer(0.2, event.set)
        timer.start()
        try:
            with caplog.at_level(logging.DEBUG, logger="fdsx.providers.base"):
                _run_subprocess(
                    args=[
                        _PYTHON, "-c",
                        "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                        " print('x',flush=True); time.sleep(999)",
                    ],
                    completion_event=event,
                )
        finally:
            timer.cancel()

        assert "did not exit voluntarily" in caplog.text

    def test_no_debug_log_when_process_exits_voluntarily(self, caplog):
        """No forced-termination log when process exits cleanly within 5s window."""
        event = threading.Event()
        timer = threading.Timer(0.2, event.set)
        timer.start()
        try:
            with caplog.at_level(logging.DEBUG, logger="fdsx.providers.base"):
                _run_subprocess(
                    args=[_PYTHON, "-c", "print('ok',flush=True); import time; time.sleep(1)"],
                    completion_event=event,
                )
        finally:
            timer.cancel()

        assert "did not exit voluntarily" not in caplog.text
        assert "SIGTERM" not in caplog.text


class TestCompletionEventTimeoutInteraction:
    """Correct behavior when both completion_event and timeout are provided."""

    def test_timeout_fires_before_completion_event(self):
        """When timeout fires first, exit_code=124 (timeout behavior wins)."""
        event = threading.Event()
        # Timeout=1s, event fires at 5s → timeout wins
        timer = threading.Timer(5.0, event.set)
        timer.start()
        try:
            result = _run_subprocess(
                args=[_PYTHON, "-c", "import time; time.sleep(999)"],
                timeout=1,
                completion_event=event,
            )
        finally:
            timer.cancel()

        assert result.exit_code == 124
        assert "timed out" in result.stderr.lower()

    def test_completion_fires_before_timeout(self):
        """When completion fires first, result has collected data (not timeout)."""
        event = threading.Event()
        # Timeout=30s, event fires at 0.5s → completion wins
        timer = threading.Timer(0.5, event.set)
        timer.start()
        try:
            result = _run_subprocess(
                args=[
                    _PYTHON, "-c",
                    "import time; print('data',flush=True); time.sleep(999)",
                ],
                timeout=30,
                completion_event=event,
            )
        finally:
            timer.cancel()

        assert result.exit_code != 124
        assert "data" in result.stdout

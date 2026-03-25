"""TDD tests for stdout-in-daemon-thread refactor (T001, T002),
completion_event termination cascade (T003, T004), and
Claude provider completion signal wiring (T005, T006).

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

T005: Write tests for Claude provider completion signal.
T006: Wire completion event in Claude provider _make_stream_callback and execute().
"""

import json
import logging
import sys
import threading
import time
from unittest.mock import patch

from fdsx.providers.base import (
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderResult,
    _run_subprocess,
)
from fdsx.providers.claude import ClaudeProvider

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
                args=[
                    _PYTHON,
                    "-c",
                    "import sys,time; print('output',flush=True); time.sleep(999)",
                ],
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
                args=[
                    _PYTHON,
                    "-c",
                    "import time; print('voluntary',flush=True); time.sleep(1)",
                ],
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
                    _PYTHON,
                    "-c",
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
                    _PYTHON,
                    "-c",
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
                        _PYTHON,
                        "-c",
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
                    args=[
                        _PYTHON,
                        "-c",
                        "print('ok',flush=True); import time; time.sleep(1)",
                    ],
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
                    _PYTHON,
                    "-c",
                    "import time; print('data',flush=True); time.sleep(999)",
                ],
                timeout=30,
                completion_event=event,
            )
        finally:
            timer.cancel()

        assert result.exit_code != 124
        assert "data" in result.stdout


# ---------------------------------------------------------------------------
# T005: Claude provider completion signal wiring tests
# ---------------------------------------------------------------------------


# Mirror the constant from the production module so tests don't import internals.
_EVENT_RESULT_TYPE = "result"


def _make_result_ndjson(result_value: str = "ok") -> str:
    """Return a stream-json NDJSON line for a result event."""
    return json.dumps({"type": _EVENT_RESULT_TYPE, "result": result_value})


def _make_content_block_delta_ndjson(text: str = "hello") -> str:
    """Return a stream-json NDJSON line for a content_block_delta event."""
    return json.dumps(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        }
    )


class TestMakeStreamCallbackCompletionEvent:
    """_make_stream_callback sets completion_event only on result events (T005)."""

    def test_completion_event_set_on_result_event(self):
        """completion_event is set when a result NDJSON event is parsed."""
        provider = ClaudeProvider()
        event = threading.Event()
        output_lines: list[str] = []

        stream_callback, _get_result, _flush = provider._make_stream_callback(
            output_lines.append, completion_event=event
        )

        assert not event.is_set(), "Event should not be set before any events"

        stream_callback(_make_result_ndjson("final text"))

        assert event.is_set(), "Event should be set after result event"

    def test_completion_event_not_set_for_content_block_delta(self):
        """completion_event is NOT set when non-result events are parsed."""
        provider = ClaudeProvider()
        event = threading.Event()
        output_lines: list[str] = []

        stream_callback, _get_result, _flush = provider._make_stream_callback(
            output_lines.append, completion_event=event
        )

        stream_callback(_make_content_block_delta_ndjson("some text"))

        assert not event.is_set(), "Event should not be set for content_block_delta"

    def test_completion_event_not_set_for_other_event_types(self):
        """completion_event is NOT set for content_block_start/stop events."""
        provider = ClaudeProvider()
        event = threading.Event()
        output_lines: list[str] = []

        stream_callback, _get_result, _flush = provider._make_stream_callback(
            output_lines.append, completion_event=event
        )

        stream_callback(
            json.dumps(
                {
                    "type": "content_block_start",
                    "content_block": {"type": "text", "text": ""},
                }
            )
        )
        stream_callback(json.dumps({"type": "content_block_stop"}))

        assert not event.is_set(), (
            "Event should not be set for content_block_start/stop"
        )

    def test_completion_event_set_exactly_once_on_multiple_result_events(self):
        """Event.set() is idempotent — multiple result events do not raise errors."""
        provider = ClaudeProvider()
        event = threading.Event()
        output_lines: list[str] = []

        stream_callback, _get_result, _flush = provider._make_stream_callback(
            output_lines.append, completion_event=event
        )

        stream_callback(_make_result_ndjson("first"))
        assert event.is_set()

        # Second result event should not raise; event is already set (idempotent)
        stream_callback(_make_result_ndjson("second"))
        assert event.is_set()

    def test_completion_event_none_does_not_raise_on_result_event(self):
        """When completion_event=None, result events are handled without errors."""
        provider = ClaudeProvider()
        output_lines: list[str] = []

        stream_callback, get_result, _flush = provider._make_stream_callback(
            output_lines.append, completion_event=None
        )

        # Should not raise
        stream_callback(_make_result_ndjson("some result"))

        assert get_result() == "some result"


class TestClaudeProviderExecuteCompletionEvent:
    """ClaudeProvider.execute() wires completion_event correctly (T005)."""

    def test_execute_with_output_callback_passes_completion_event(self):
        """execute() creates a threading.Event and passes it to _run_subprocess."""
        provider = ClaudeProvider()
        output_lines: list[str] = []
        captured_kwargs: dict = {}

        def fake_run_subprocess(**kwargs):  # type: ignore[return]
            captured_kwargs.update(kwargs)
            return ProviderResult(exit_code=0, stdout="", stderr="")

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello", output_callback=output_lines.append)

        assert "completion_event" in captured_kwargs, (
            "_run_subprocess should be called with completion_event kwarg"
        )
        assert isinstance(captured_kwargs["completion_event"], threading.Event), (
            "completion_event should be a threading.Event instance"
        )

    def test_execute_without_output_callback_does_not_pass_completion_event(self):
        """execute() without output_callback does not pass completion_event."""
        provider = ClaudeProvider()
        captured_kwargs: dict = {}

        def fake_run_subprocess(**kwargs):  # type: ignore[return]
            captured_kwargs.update(kwargs)
            return ProviderResult(exit_code=0, stdout="done", stderr="")

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello")

        # No completion_event key, or it is None
        completion_event = captured_kwargs.get("completion_event")
        assert completion_event is None, (
            "completion_event should not be passed when output_callback is None"
        )


# ---------------------------------------------------------------------------
# Phase 2: Inactivity timeout unit tests
# ---------------------------------------------------------------------------


class TestDefaultInactivityTimeoutConstant:
    """DEFAULT_INACTIVITY_TIMEOUT is exported with the expected value."""

    def test_default_inactivity_timeout_value(self):
        """DEFAULT_INACTIVITY_TIMEOUT equals 300 (5 minutes)."""
        assert DEFAULT_INACTIVITY_TIMEOUT == 300

    def test_default_inactivity_timeout_is_int(self):
        """DEFAULT_INACTIVITY_TIMEOUT is an integer."""
        assert isinstance(DEFAULT_INACTIVITY_TIMEOUT, int)


class TestInactivityTimeoutParameter:
    """inactivity_timeout parameter accepted by _run_subprocess without error."""

    def test_inactivity_timeout_none_default(self):
        """inactivity_timeout=None (default) — fast process completes normally."""
        result = _run_subprocess(
            args=[_PYTHON, "-c", "print('ok')"],
            inactivity_timeout=None,
        )
        assert result.exit_code == 0
        assert result.stdout == "ok"

    def test_inactivity_timeout_zero_disables_watchdog(self):
        """inactivity_timeout=0 — process completes without being killed."""
        result = _run_subprocess(
            args=[_PYTHON, "-c", "print('ok')"],
            inactivity_timeout=0,
        )
        assert result.exit_code == 0
        assert result.stdout == "ok"

    def test_inactivity_timeout_result_exit_code_124(self):
        """Process killed by inactivity returns exit_code=124."""
        result = _run_subprocess(
            args=[_PYTHON, "-c", "import time; time.sleep(999)"],
            inactivity_timeout=2,
        )
        assert result.exit_code == 124

    def test_inactivity_timeout_result_stderr_message(self):
        """Inactivity kill message includes threshold duration."""
        result = _run_subprocess(
            args=[_PYTHON, "-c", "import time; time.sleep(999)"],
            inactivity_timeout=2,
        )
        assert "2" in result.stderr
        assert "inactivity timeout" in result.stderr.lower()

    def test_inactivity_timeout_result_stdout_empty(self):
        """Process killed by inactivity has empty stdout (like explicit timeout)."""
        result = _run_subprocess(
            args=[_PYTHON, "-c", "import time; time.sleep(999)"],
            inactivity_timeout=2,
        )
        assert result.stdout == ""

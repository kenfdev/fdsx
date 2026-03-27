"""Integration tests for inactivity timeout watchdog (Phase 2: T001).

These tests verify that _run_subprocess correctly kills subprocesses that go
silent for longer than the configured inactivity threshold, while allowing
active processes (those that produce output regularly) to run to completion.

All tests use real subprocesses via sys.executable to ensure realistic behavior.
Short thresholds (2s) keep the test suite fast while providing sufficient margin.

Test criteria (T003): python -m pytest tests/integration/test_inactivity_timeout.py -v
"""

import json
import sys
import threading
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

from fdsx.providers.base import (
    DEFAULT_EXECUTION_TIMEOUT,
    ProviderResult,
    _run_subprocess,
)
from fdsx.providers.claude import (
    _CONTENT_TYPE_TOOL_USE,
    _EVENT_CONTENT_BLOCK_START,
    _EVENT_CONTENT_BLOCK_STOP,
    ClaudeOptions,
    ClaudeProvider,
)
from fdsx.providers.codex import CodexProvider
from fdsx.providers.gemini import GeminiProvider
from fdsx.providers.opencode import OpenCodeProvider

# Use the same Python interpreter as the test runner.
_PYTHON = sys.executable

# Short inactivity threshold for tests (seconds). The watchdog polls every 1s,
# so a process is killed within ~(threshold + 1)s of going silent.
_INACTIVITY_THRESHOLD = 2

# Upper bound on how long each test may take: threshold + watchdog poll + cascade overhead.
_TEST_TIMEOUT = 15


class TestProcessKilledAfterInactivityPeriod:
    """Process goes silent after initial output → killed after threshold."""

    def test_process_killed_after_inactivity_period(self):
        """Process outputs once then goes silent; killed after threshold with
        exit_code=124 and 'inactivity timeout' in stderr."""
        start = time.time()
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import sys, time; print('output', flush=True); time.sleep(5)",
            ],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
        )
        elapsed = time.time() - start

        assert result.exit_code == 124, (
            f"Expected exit_code=124 (inactivity kill), got {result.exit_code}"
        )
        assert "inactivity timeout" in result.stderr.lower(), (
            f"Expected 'inactivity timeout' in stderr, got: {result.stderr!r}"
        )
        assert elapsed < _TEST_TIMEOUT, (
            f"Test took {elapsed:.1f}s — exceeds {_TEST_TIMEOUT}s limit"
        )


class TestActiveProcessNotKilled:
    """Process that outputs continuously beyond threshold completes normally."""

    def test_active_process_not_killed(self):
        """Process emitting output every 0.5s (within 2s threshold) is not killed."""
        # Output 6 lines at 0.5s intervals → runs for ~3s, well beyond threshold
        # but never silent for more than 0.5s
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import time;"
                " [(__import__('sys').stdout.write('line\\n'), __import__('sys').stdout.flush(), time.sleep(0.5)) for _ in range(6)]",
            ],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
        )

        assert result.exit_code == 0, (
            f"Expected exit_code=0 (normal completion), got {result.exit_code}"
        )
        assert "inactivity" not in result.stderr.lower(), (
            f"Process should not be killed by inactivity, stderr: {result.stderr!r}"
        )


class TestStartupHangKilled:
    """Process that never produces any output is killed after threshold."""

    def test_startup_hang_killed(self):
        """Process that hangs immediately (no output) is killed after threshold."""
        start = time.time()
        result = _run_subprocess(
            args=[_PYTHON, "-c", "import time; time.sleep(5)"],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
        )
        elapsed = time.time() - start

        assert result.exit_code == 124, (
            f"Expected exit_code=124 (inactivity kill), got {result.exit_code}"
        )
        assert "inactivity timeout" in result.stderr.lower(), (
            f"Expected 'inactivity timeout' in stderr, got: {result.stderr!r}"
        )
        assert elapsed < _TEST_TIMEOUT, (
            f"Test took {elapsed:.1f}s — exceeds {_TEST_TIMEOUT}s limit"
        )


class TestInactivityTimeoutDisabledWithZero:
    """inactivity_timeout=0 disables the watchdog; process is NOT killed."""

    def test_inactivity_timeout_disabled_with_zero(self):
        """Process with inactivity_timeout=0 completes normally even with silence."""
        # Process produces no output, then exits after 1s.
        # With inactivity_timeout=0 (disabled), it should NOT be killed early.
        result = _run_subprocess(
            args=[_PYTHON, "-c", "import time; time.sleep(1)"],
            inactivity_timeout=0,
        )

        assert result.exit_code == 0, (
            f"Expected exit_code=0 (no inactivity kill), got {result.exit_code}"
        )
        assert "inactivity" not in result.stderr.lower(), (
            f"Unexpected inactivity kill with timeout=0, stderr: {result.stderr!r}"
        )


class TestStderrResetsInactivityTimer:
    """Stderr output resets the inactivity timer; process is not killed."""

    def test_stderr_resets_inactivity_timer(self):
        """Process writing to stderr every 0.5s (no stdout) is not killed by inactivity."""
        # 6 stderr lines at 0.5s intervals → runs for ~3s, timer reset each time
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import sys, time;"
                " [(sys.stderr.write('err\\n'), sys.stderr.flush(), time.sleep(0.5))"
                "  for _ in range(6)]",
            ],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
        )

        assert result.exit_code == 0, (
            f"Expected exit_code=0 (timer reset by stderr), got {result.exit_code}"
        )
        # The collected stderr should contain our expected lines, not an inactivity message
        assert "inactivity timeout" not in result.stderr.lower(), (
            f"Process should not be killed by inactivity, stderr: {result.stderr!r}"
        )


class TestInactivityTimeoutErrorDistinguishable:
    """Inactivity timeout and explicit timeout produce different stderr messages."""

    def test_inactivity_timeout_error_distinguishable_from_explicit_timeout(self):
        """inactivity vs explicit timeout errors have distinct stderr messages."""
        # Inactivity timeout: process goes silent
        inactivity_result = _run_subprocess(
            args=[_PYTHON, "-c", "import time; time.sleep(5)"],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
        )
        assert inactivity_result.exit_code == 124
        assert "inactivity timeout" in inactivity_result.stderr.lower(), (
            f"Expected 'inactivity timeout' in inactivity result, got: {inactivity_result.stderr!r}"
        )

        # Explicit timeout: process simply takes too long
        explicit_result = _run_subprocess(
            args=[_PYTHON, "-c", "import time; time.sleep(5)"],
            timeout=1,
        )
        assert explicit_result.exit_code == 124
        assert "timed out" in explicit_result.stderr.lower(), (
            f"Expected 'timed out' in explicit timeout result, got: {explicit_result.stderr!r}"
        )

        # The messages must be different from each other
        assert inactivity_result.stderr != explicit_result.stderr, (
            "Inactivity and explicit timeout should produce different error messages"
        )
        assert "inactivity" not in explicit_result.stderr.lower(), (
            f"Explicit timeout result should not mention 'inactivity': {explicit_result.stderr!r}"
        )
        assert "timed out" not in inactivity_result.stderr.lower(), (
            f"Inactivity result should not say 'timed out': {inactivity_result.stderr!r}"
        )


class TestCompletionEventSuppressesInactivityTimeout:
    """completion_event fires → inactivity watchdog is suppressed, no inactivity error."""

    def test_completion_event_suppresses_inactivity_timeout(self):
        """When completion_event fires, inactivity watchdog is suppressed.

        Process emits a 'ready' line then hangs. The output_callback sets
        completion_event which sets _suppressed, causing the inactivity watchdog
        to exit without killing. The termination cascade (from completion_event)
        kills the hanging process. Result must not show an inactivity timeout error.
        """
        completion_event = threading.Event()

        def on_output(line: str) -> None:
            if "ready" in line:
                completion_event.set()

        start = time.time()
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import sys, time; print('ready', flush=True); time.sleep(5)",
            ],
            completion_event=completion_event,
            inactivity_timeout=_INACTIVITY_THRESHOLD,
            output_callback=on_output,
        )
        elapsed = time.time() - start

        assert "inactivity timeout" not in result.stderr.lower(), (
            f"completion_event should suppress inactivity kill; stderr: {result.stderr!r}"
        )
        assert elapsed < _TEST_TIMEOUT, (
            f"Test took {elapsed:.1f}s — exceeds {_TEST_TIMEOUT}s limit"
        )


class TestInactivityTimeoutWithExplicitTimeout:
    """Both inactivity_timeout and explicit timeout set; inactivity fires first."""

    def test_inactivity_timeout_with_explicit_timeout(self):
        """Both timeouts configured; inactivity fires first.

        Process outputs once then goes silent. Inactivity threshold (2s) is much
        shorter than explicit timeout (30s). The inactivity watchdog fires first,
        producing an inactivity error (exit_code=124, 'inactivity timeout' in
        stderr) — not an explicit timeout error ('timed out').
        """
        start = time.time()
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import sys, time; print('output', flush=True); time.sleep(5)",
            ],
            timeout=30,
            inactivity_timeout=_INACTIVITY_THRESHOLD,
        )
        elapsed = time.time() - start

        assert result.exit_code == 124, (
            f"Expected exit_code=124 (inactivity kill), got {result.exit_code}"
        )
        assert "inactivity timeout" in result.stderr.lower(), (
            f"Expected inactivity error, got: {result.stderr!r}"
        )
        assert "timed out" not in result.stderr.lower(), (
            f"Should be inactivity error, not explicit timeout; stderr: {result.stderr!r}"
        )
        assert elapsed < _TEST_TIMEOUT, (
            f"Test took {elapsed:.1f}s — exceeds {_TEST_TIMEOUT}s limit"
        )


class TestToolInProgressSuspendsInactivity:
    """on_inactivity_hooks can suspend/resume the inactivity watchdog timer."""

    def test_suspended_process_not_killed(self):
        """Process goes silent but suspend_fn called immediately → still killed.

        A single suspend call only resets the timer once. After that, if the
        process remains silent beyond the threshold, it is killed.
        """
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import sys, time; print('output', flush=True); time.sleep(4)",
            ],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
            on_inactivity_hooks=lambda suspend, resume: suspend(),
        )

        assert result.exit_code == 124, (
            f"Expected exit_code=124 (killed despite single suspend), got {result.exit_code}"
        )
        assert "inactivity timeout" in result.stderr.lower(), (
            f"Expected 'inactivity timeout' in stderr, got: {result.stderr!r}"
        )

    def test_resumed_timer_kills_after_threshold(self):
        """Suspend immediately, resume after 0.5s, then stay silent → killed."""
        resume_time = 0.5

        def schedule_resume(suspend, resume):
            suspend()
            threading.Timer(resume_time, resume).start()

        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import sys, time; print('output', flush=True); time.sleep(4)",
            ],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
            on_inactivity_hooks=schedule_resume,
        )

        assert result.exit_code == 124, (
            f"Expected exit_code=124 (killed after resumed inactivity), got {result.exit_code}"
        )
        assert "inactivity timeout" in result.stderr.lower(), (
            f"Expected 'inactivity timeout' in stderr after resume, got: {result.stderr!r}"
        )

    def test_resume_resets_activity_timestamp(self):
        """Suspend near threshold, resume, then continue → not killed (clock reset)."""
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                (
                    "import sys, time;"
                    " print('output', flush=True);"
                    " time.sleep(1.5);"  # suspend for ~1.5s (close to 2s threshold)
                    " print('more output', flush=True);"
                    " time.sleep(1.5)"  # then silent for ~1.5s more (total ~3s)
                ),
            ],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
            on_inactivity_hooks=lambda suspend, resume: (
                suspend(),
                threading.Timer(1.5, resume).start(),
            ),
        )

        assert result.exit_code == 0, (
            f"Expected exit_code=0 (clock reset on resume), got {result.exit_code}"
        )
        assert "inactivity" not in result.stderr.lower(), (
            f"Process should not be killed; clock reset on resume, stderr: {result.stderr!r}"
        )

    def test_periodic_suspend_calls_keep_process_alive(self):
        """Repeated suspend calls (each resetting timer) keep process alive.

        Process outputs once then sleeps for 4s. With 2s threshold, it would be
        killed. But periodic suspend calls (every 1s) keep resetting the timer,
        allowing the process to complete.
        """
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import sys, time; print('output', flush=True); time.sleep(4)",
            ],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
            on_inactivity_hooks=lambda suspend, resume: (
                suspend(),
                threading.Timer(1.0, suspend).start(),
                threading.Timer(2.0, suspend).start(),
                threading.Timer(3.0, suspend).start(),
            ),
        )

        assert result.exit_code == 0, (
            f"Expected exit_code=0 (periodic suspends keep alive), got {result.exit_code}"
        )
        assert "inactivity" not in result.stderr.lower(), (
            f"Process should not be killed when timer keeps being reset, stderr: {result.stderr!r}"
        )

    def test_suspend_resets_timer_preventing_premature_kill(self):
        """A well-timed _suspend_inactivity call gives the process a full new timeout window.

        Process outputs once then goes silent. At ~1.5s (close to the 2s threshold),
        a suspend call resets the timer, giving another 2s window. Process exits
        normally at ~3s total — proving the timer was reset, not just bypassed.
        """

        def schedule_suspend_near_threshold(suspend, resume):
            threading.Timer(1.5, suspend).start()

        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import sys, time; print('output', flush=True); time.sleep(3)",
            ],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
            on_inactivity_hooks=schedule_suspend_near_threshold,
        )

        assert result.exit_code == 0, (
            f"Expected exit_code=0 (timer reset prevented kill), got {result.exit_code}"
        )
        assert "inactivity" not in result.stderr.lower(), (
            f"Process should not be killed when timer is reset near threshold, stderr: {result.stderr!r}"
        )

    def test_tool_hanging_beyond_timeout_kills_process(self):
        """Even when _suspend_inactivity is called, process IS killed if tool hangs
        beyond the inactivity_timeout window.

        This verifies the bug-fix behavior: a single suspend call only resets the
        timer once. If the tool hangs longer than the threshold after that, the
        process is killed.
        """
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "import sys, time; print('output', flush=True); time.sleep(5)",
            ],
            inactivity_timeout=_INACTIVITY_THRESHOLD,
            on_inactivity_hooks=lambda suspend, resume: suspend(),
        )

        assert result.exit_code == 124, (
            f"Expected exit_code=124 (killed despite suspend), got {result.exit_code}"
        )
        assert "inactivity timeout" in result.stderr.lower(), (
            f"Expected 'inactivity timeout' in stderr, got: {result.stderr!r}"
        )


class TestDefaultExecutionTimeout:
    """LLM providers apply DEFAULT_EXECUTION_TIMEOUT when no explicit timeout is set."""

    def _capture_timeout(self, provider_cls, provider_module_path, **execute_kwargs):
        """Run provider.execute() with a mocked _run_subprocess, return the timeout kwarg."""
        captured: dict[str, Any] = {}

        def mock_run_subprocess(**kwargs):
            captured.update(kwargs)
            return ProviderResult(exit_code=0, stdout="done", stderr="")

        with patch(provider_module_path, mock_run_subprocess):
            provider_cls().execute(prompt="test", **execute_kwargs)

        return captured.get("timeout")

    def test_claude_applies_default_execution_timeout(self):
        """ClaudeProvider uses DEFAULT_EXECUTION_TIMEOUT when timeout=None."""
        timeout = self._capture_timeout(
            ClaudeProvider, "fdsx.providers.claude._run_subprocess"
        )
        assert timeout == DEFAULT_EXECUTION_TIMEOUT

    def test_claude_respects_explicit_timeout(self):
        """ClaudeProvider uses explicit timeout when provided."""
        timeout = self._capture_timeout(
            ClaudeProvider, "fdsx.providers.claude._run_subprocess", timeout=60
        )
        assert timeout == 60

    def test_gemini_applies_default_execution_timeout(self):
        """GeminiProvider uses DEFAULT_EXECUTION_TIMEOUT when timeout=None."""
        timeout = self._capture_timeout(
            GeminiProvider, "fdsx.providers.gemini._run_subprocess"
        )
        assert timeout == DEFAULT_EXECUTION_TIMEOUT

    def test_codex_applies_default_execution_timeout(self):
        """CodexProvider uses DEFAULT_EXECUTION_TIMEOUT when timeout=None."""
        timeout = self._capture_timeout(
            CodexProvider, "fdsx.providers.codex._run_subprocess"
        )
        assert timeout == DEFAULT_EXECUTION_TIMEOUT

    def test_opencode_applies_default_execution_timeout(self):
        """OpenCodeProvider uses DEFAULT_EXECUTION_TIMEOUT when timeout=None."""
        timeout = self._capture_timeout(
            OpenCodeProvider, "fdsx.providers.opencode._run_subprocess"
        )
        assert timeout == DEFAULT_EXECUTION_TIMEOUT


class TestClaudeToolInProgressSuspendsInactivity:
    """T014: Claude tool_use events trigger suspend/resume to prevent false inactivity kills."""

    def test_tool_use_event_suspends_timer(self):
        """content_block_start(tool_use) triggers suspend; content_block_stop triggers resume."""
        provider = ClaudeProvider(ClaudeOptions(inactivity_timeout=60))

        captured_hooks: list[tuple[MagicMock, MagicMock]] = []
        captured_callback: list[Callable[[str], None]] = []

        def mock_run_subprocess(**kwargs):
            captured_callback.append(kwargs["output_callback"])
            hooks = kwargs.get("on_inactivity_hooks")
            if hooks:
                suspend_fn = MagicMock()
                resume_fn = MagicMock()
                hooks(suspend_fn, resume_fn)
                captured_hooks.append((suspend_fn, resume_fn))
            return ProviderResult(exit_code=0, stdout="done", stderr="")

        with patch("fdsx.providers.claude._run_subprocess", mock_run_subprocess):
            result = provider.execute(
                prompt="test",
                output_callback=MagicMock(),
            )

        assert result.exit_code == 0
        assert len(captured_hooks) == 1
        suspend_fn, resume_fn = captured_hooks[0]

        output_cb = captured_callback[0]
        output_cb(
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": _EVENT_CONTENT_BLOCK_START,
                        "index": 1,
                        "content_block": {
                            "type": _CONTENT_TYPE_TOOL_USE,
                            "id": "tu_001",
                            "name": "Bash",
                        },
                    },
                }
            )
        )
        suspend_fn.assert_called_once()

        output_cb(
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {"type": _EVENT_CONTENT_BLOCK_STOP, "index": 1},
                }
            )
        )
        resume_fn.assert_called_once()

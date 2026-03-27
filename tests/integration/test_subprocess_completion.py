"""Integration tests for completion signal with real subprocesses (T007).

These tests exercise the end-to-end behavior of _run_subprocess with the
completion_event parameter using crafted Python scripts that simulate the
stream-json NDJSON protocol used by the Claude CLI.

Scenarios:
1. Hanging provider: subprocess emits NDJSON with a 'result' event then hangs.
   The completion_event is set by the output_callback upon seeing the result
   event → the step should complete within ~15s (termination cascade max).

2. Clean exit provider: subprocess emits a 'result' event and exits immediately.
   The completion_event fires, the process exits voluntarily before the 5s
   voluntary-wait window expires → no extra latency.

3. No completion signal: subprocess without stream protocol (plain stdout then
   exit). No completion_event is used → current behavior (wait for stdout EOF).
"""

import json
import sys
import threading
import time
from collections.abc import Callable

from fdsx.providers.base import _run_subprocess

# Use the same Python interpreter as the test runner.
_PYTHON = sys.executable

# NDJSON event type constant — mirrors _EVENT_RESULT_TYPE in unit tests and
# _EVENT_RESULT in claude.py without importing private internals.
_EVENT_RESULT_TYPE = "result"


def _make_result_ndjson(result_value: str = "ok") -> str:
    """Return a stream-json NDJSON line for a result event."""
    return json.dumps({"type": _EVENT_RESULT_TYPE, "result": result_value})


def _make_content_block_delta_ndjson(text: str) -> str:
    """Return a stream-json NDJSON line for a content_block_delta event."""
    return json.dumps(
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        }
    )


def _make_ndjson_callback(
    completion_event: threading.Event,
) -> tuple[Callable[[str], None], list[str], list[str]]:
    """Return an output_callback, all received lines, and parsed result values.

    The callback parses each line as NDJSON and sets ``completion_event``
    when it encounters a ``result`` event — mirroring what ClaudeProvider
    does internally via _make_stream_callback.

    Returns:
        (output_callback, all_lines, result_values)
        where ``all_lines`` contains every raw line delivered to the callback
        and ``result_values`` is populated with the ``result`` field from
        each ``result`` event.
    """
    all_lines: list[str] = []
    result_values: list[str] = []

    def output_callback(line: str) -> None:
        all_lines.append(line)
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if event.get("type") == _EVENT_RESULT_TYPE:
            result_values.append(event.get("result", ""))
            completion_event.set()

    return output_callback, all_lines, result_values


class TestHangingProvider:
    """Simulated hanging provider completes within ~15s via termination cascade."""

    def test_hanging_provider_completes_within_cascade_timeout(self):
        """Subprocess emits NDJSON result event then hangs; completes within ~15s.

        The output_callback sets the completion_event when the result event
        is parsed.  _run_subprocess then initiates the termination cascade:
        wait 5s for voluntary exit → SIGTERM → wait 5s → SIGKILL.
        Total worst-case: ~10s cascade + small overhead < 15s.
        """
        completion_event = threading.Event()
        output_callback, _, result_values = _make_ndjson_callback(completion_event)

        # Script: emit content delta, then result event, then hang.
        result_line = _make_result_ndjson("integration test result")
        delta_line = _make_content_block_delta_ndjson("some text")
        script = (
            "import sys, time\n"
            f"print({delta_line!r}, flush=True)\n"
            f"print({result_line!r}, flush=True)\n"
            "time.sleep(999)\n"
        )

        start = time.time()
        result = _run_subprocess(
            args=[_PYTHON, "-c", script],
            output_callback=output_callback,
            completion_event=completion_event,
        )
        elapsed = time.time() - start

        assert result.exit_code != 124, "Should not be a timeout result"
        assert result_values == ["integration test result"], (
            f"Expected result event value to be captured; got {result_values!r}"
        )
        assert elapsed < 15, (
            f"Hanging provider took {elapsed:.1f}s — exceeds 15s cascade budget"
        )

    def test_hanging_provider_result_data_preserved(self):
        """All NDJSON lines emitted before the hang are delivered to the callback."""
        completion_event = threading.Event()
        output_callback, all_lines, result_values = _make_ndjson_callback(
            completion_event
        )

        delta1 = _make_content_block_delta_ndjson("hello ")
        delta2 = _make_content_block_delta_ndjson("world")
        result_line = _make_result_ndjson("final answer")
        script = (
            "import sys, time\n"
            f"print({delta1!r}, flush=True)\n"
            f"print({delta2!r}, flush=True)\n"
            f"print({result_line!r}, flush=True)\n"
            "time.sleep(999)\n"
        )

        _run_subprocess(
            args=[_PYTHON, "-c", script],
            output_callback=output_callback,
            completion_event=completion_event,
        )

        assert result_values == ["final answer"], (
            f"result_values mismatch: {result_values!r}"
        )
        assert len(all_lines) == 3, (
            f"Expected 3 lines (2 deltas + 1 result); got {len(all_lines)}: {all_lines!r}"
        )
        assert delta1 in all_lines, f"delta1 not delivered to callback: {all_lines!r}"
        assert delta2 in all_lines, f"delta2 not delivered to callback: {all_lines!r}"


class TestCleanExitProvider:
    """Clean exit provider completes with no extra latency."""

    def test_clean_exit_provider_no_extra_latency(self):
        """Subprocess emits result event and exits immediately; no forced kill needed.

        The process exits voluntarily well within the 5s voluntary-exit window,
        so the cascade does not fire SIGTERM/SIGKILL.  Total elapsed time should
        be well under 5s.
        """
        completion_event = threading.Event()
        output_callback, _, result_values = _make_ndjson_callback(completion_event)

        result_line = _make_result_ndjson("clean exit result")
        script = (
            f"import sys\nprint({result_line!r}, flush=True)\n"
            # Process exits immediately after printing
        )

        start = time.time()
        result = _run_subprocess(
            args=[_PYTHON, "-c", script],
            output_callback=output_callback,
            completion_event=completion_event,
        )
        elapsed = time.time() - start

        assert result.exit_code == 0
        assert result_values == ["clean exit result"], (
            f"result_values mismatch: {result_values!r}"
        )
        # Clean exit should complete well within the 5s voluntary-exit window.
        assert elapsed < 5, (
            f"Clean exit provider took {elapsed:.1f}s — should be near-instant"
        )

    def test_clean_exit_provider_nonzero_exit_code_preserved(self):
        """Non-zero exit code from a clean-exit provider is preserved correctly."""
        completion_event = threading.Event()
        output_callback, _, _ = _make_ndjson_callback(completion_event)

        result_line = _make_result_ndjson("done")
        script = f"import sys\nprint({result_line!r}, flush=True)\nsys.exit(42)\n"

        result = _run_subprocess(
            args=[_PYTHON, "-c", script],
            output_callback=output_callback,
            completion_event=completion_event,
        )

        assert result.exit_code == 42


class TestNoCompletionSignal:
    """Subprocess without stream protocol uses current behavior (wait for EOF)."""

    def test_no_completion_signal_waits_for_eof(self):
        """Without completion_event, _run_subprocess waits for stdout EOF.

        This verifies existing behavior is unchanged for non-Claude providers
        that do not use the stream protocol.
        """
        # Simple script: print plain output then exit — no NDJSON, no event.
        result = _run_subprocess(
            args=[_PYTHON, "-c", "print('plain output', flush=True)"],
        )

        assert result.exit_code == 0
        assert result.stdout == "plain output"

    def test_no_completion_signal_collects_all_output(self):
        """Without completion_event, all stdout lines are collected correctly."""
        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "for i in range(5): print(f'line{i}', flush=True)",
            ],
        )

        assert result.exit_code == 0
        assert result.stdout == "line0\nline1\nline2\nline3\nline4"

    def test_no_completion_signal_with_output_callback(self):
        """Without completion_event, output_callback is called for each line."""
        received: list[str] = []

        result = _run_subprocess(
            args=[
                _PYTHON,
                "-c",
                "print('alpha', flush=True); print('beta', flush=True)",
            ],
            output_callback=received.append,
        )

        assert result.exit_code == 0
        assert received == ["alpha", "beta"]

    def test_no_completion_signal_unset_event_behaves_like_none(self):
        """An unset completion_event behaves identically to no event."""
        event = threading.Event()  # never set

        start = time.time()
        result = _run_subprocess(
            args=[_PYTHON, "-c", "print('no_signal', flush=True)"],
            completion_event=event,
        )
        elapsed = time.time() - start

        assert result.exit_code == 0
        assert result.stdout == "no_signal"
        assert elapsed < 2, (
            f"Unset event should not trigger cascade delay; took {elapsed:.1f}s"
        )

"""Regression tests for real-time streaming delivery (T001, T002).

These tests verify that stdout/stderr lines from _run_subprocess are delivered
to callbacks BEFORE the subprocess exits — i.e., in real-time as lines arrive.

These tests are written in RED (failing) state first to confirm the bug:
`for line in process.stdout:` and `for raw_line in process.stderr:` use
Python's iterator with ~8KB read-ahead buffering, causing all lines to be
delivered at once near process exit instead of incrementally.

T001: stdout real-time delivery
T002: stderr real-time delivery
"""

import time

from fdsx.providers.base import _run_subprocess

# Subprocess command that prints 3 lines with 0.3s sleep between each.
# flush=True is critical: ensures the child process flushes to the pipe
# immediately, isolating the Python-side iterator buffering as the sole cause.
_STDOUT_CMD = (
    'python3 -c "'
    "import sys, time; "
    "[print(f'line{i}', flush=True) or time.sleep(0.3) for i in range(3)]"
    '"'
)

_STDERR_CMD = (
    'python3 -c "'
    "import sys, time; "
    "[print(f'line{i}', file=sys.stderr, flush=True) or time.sleep(0.3) for i in range(3)]"
    '"'
)

# A callback firing this many seconds before process completion is evidence of
# real-time delivery (the subprocess sleeps 0.3s between lines, so total
# runtime ≈ 0.9s; the first callback should arrive ~0.6s before completion).
_MIN_LEAD_SECONDS = 0.2


class TestRealtimeStreamingDelivery:
    """Tests for real-time line delivery via output_callback / stderr_callback."""

    def test_stdout_lines_delivered_before_process_exits(self):
        """stdout callback receives lines incrementally BEFORE the process exits.

        The subprocess prints line0, sleeps 0.3s, prints line1, sleeps 0.3s,
        prints line2, then exits. With real-time streaming, at least one
        callback fires >0.2s before _run_subprocess returns.

        With the current buffered iterator the assertion FAILS (RED) because all
        callbacks fire within milliseconds of each other at process exit.
        """
        timestamps: list[float] = []

        def _capture(line: str) -> None:
            timestamps.append(time.time())

        _run_subprocess(
            args=[_STDOUT_CMD],
            shell=True,
            output_callback=_capture,
        )
        completion_time = time.time()

        assert len(timestamps) == 3, f"Expected 3 callbacks, got {len(timestamps)}"

        earliest = min(timestamps)
        lead = completion_time - earliest
        assert lead > _MIN_LEAD_SECONDS, (
            f"First stdout callback fired only {lead:.3f}s before completion "
            f"(need >{_MIN_LEAD_SECONDS}s). Lines were buffered instead of streamed."
        )

    def test_stderr_lines_delivered_before_process_exits(self):
        """stderr callback receives lines incrementally BEFORE the process exits.

        The subprocess writes line0 to stderr, sleeps 0.3s, writes line1,
        sleeps 0.3s, writes line2, then exits. With real-time streaming, at
        least one callback fires >0.2s before _run_subprocess returns.

        With the current buffered iterator the assertion FAILS (RED) because all
        callbacks fire within milliseconds of each other at process exit.
        """
        timestamps: list[float] = []

        def _capture(line: str) -> None:
            timestamps.append(time.time())

        _run_subprocess(
            args=[_STDERR_CMD],
            shell=True,
            stderr_callback=_capture,
        )
        completion_time = time.time()

        assert len(timestamps) == 3, f"Expected 3 callbacks, got {len(timestamps)}"

        earliest = min(timestamps)
        lead = completion_time - earliest
        assert lead > _MIN_LEAD_SECONDS, (
            f"First stderr callback fired only {lead:.3f}s before completion "
            f"(need >{_MIN_LEAD_SECONDS}s). Lines were buffered instead of streamed."
        )

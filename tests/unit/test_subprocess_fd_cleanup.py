"""FD-leak regression tests for _run_subprocess (US1 TDD anchor).

These tests document the current FD leak bug in executable form.
They FAIL on pre-fix HEAD (Popen without context manager / no explicit pipe
close) and PASS after T004 (Popen context manager + nested pipe cleanup added).

Pattern: 20 iterations (iteration 0 is warm-up, iterations 1-19 assert flat).
"""

import threading
from pathlib import Path

from fdsx.providers.base import _run_subprocess

ITERATIONS = 20


def _fd_count() -> int:
    return len(list(Path("/dev/fd").iterdir()))


def test_run_subprocess_fd_count_is_flat_after_warmup():
    """FD count stays flat across 20 normal-exit _run_subprocess calls."""
    baseline: int | None = None
    for i in range(ITERATIONS):
        _run_subprocess(["echo", "x"])
        count = _fd_count()
        if i == 0:
            baseline = count
        else:
            assert count == baseline, (
                f"FD leak at iteration {i + 1}: expected {baseline}, got {count}"
            )


def test_run_subprocess_fd_count_is_flat_after_timeout():
    """FD count stays flat across 20 timeout-path _run_subprocess calls."""
    baseline: int | None = None
    for i in range(ITERATIONS):
        _run_subprocess(["sh", "-c", "sleep 5"], timeout=1)  # type: ignore[arg-type]
        count = _fd_count()
        if i == 0:
            baseline = count
        else:
            assert count == baseline, (
                f"FD leak at iteration {i + 1}: expected {baseline}, got {count}"
            )


def test_run_subprocess_fd_count_is_flat_after_inactivity_kill():
    """FD count stays flat across 20 inactivity-kill _run_subprocess calls."""
    baseline: int | None = None
    for i in range(ITERATIONS):
        _run_subprocess(["sh", "-c", "sleep 10"], inactivity_timeout=1)
        count = _fd_count()
        if i == 0:
            baseline = count
        else:
            assert count == baseline, (
                f"FD leak at iteration {i + 1}: expected {baseline}, got {count}"
            )


def test_run_subprocess_fd_count_is_flat_after_completion_event():
    """FD count stays flat across 20 completion-event _run_subprocess calls."""
    baseline: int | None = None
    for i in range(ITERATIONS):
        event = threading.Event()
        timer = threading.Timer(0.05, event.set)
        timer.start()
        _run_subprocess(["sh", "-c", "sleep 10"], completion_event=event)
        timer.cancel()
        count = _fd_count()
        if i == 0:
            baseline = count
        else:
            assert count == baseline, (
                f"FD leak at iteration {i + 1}: expected {baseline}, got {count}"
            )

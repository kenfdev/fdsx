"""FD-leak regression tests for _run_subprocess (US1 TDD anchor).

These tests document the FD leak that the Popen context-manager fix addresses.
They FAIL on pre-fix HEAD (bare ``process = subprocess.Popen(...)``, pipes only
closed by CPython refcount once ``process`` goes out of scope) and PASS after
T004 (``with subprocess.Popen(...)`` — ``Popen.__exit__`` explicitly calls
``stdout.close()`` / ``stderr.close()``).

The critical test design choice: each iteration retains the ``Popen`` object
via an ``on_process_start`` callback. This prevents refcount-triggered cleanup
from masking the leak, so the only code path that closes the pipe FDs is
``Popen.__exit__`` (present only in the post-fix code). Without retention,
CPython's synchronous refcount cleanup would close the pipes on return and the
tests would pass against either HEAD, making them worthless as a TDD anchor.

Pattern: 20 iterations (iteration 0 is warm-up, iterations 1-19 assert flat).
"""

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from fdsx.providers.base import _run_subprocess

ITERATIONS = 20


def _fd_count() -> int:
    return len(list(Path("/dev/fd").iterdir()))


def _retain(
    bucket: list[subprocess.Popen[str]],
) -> Callable[[subprocess.Popen[str]], None]:
    def _cb(p: subprocess.Popen[str]) -> None:
        bucket.append(p)

    return _cb


def test_run_subprocess_fd_count_is_flat_after_warmup() -> None:
    """FD count stays flat across 20 normal-exit _run_subprocess calls."""
    retained: list[subprocess.Popen[str]] = []
    baseline: int | None = None
    for i in range(ITERATIONS):
        _run_subprocess(["echo", "x"], on_process_start=_retain(retained))
        count = _fd_count()
        if i == 0:
            baseline = count
        else:
            assert count == baseline, (
                f"FD leak at iteration {i + 1}: expected {baseline}, got {count}"
            )


def test_run_subprocess_fd_count_is_flat_after_timeout() -> None:
    """FD count stays flat across 20 timeout-path _run_subprocess calls."""
    retained: list[subprocess.Popen[str]] = []
    baseline: int | None = None
    for i in range(ITERATIONS):
        _run_subprocess(
            ["sh", "-c", "sleep 5"],
            timeout=0.2,  # type: ignore[arg-type]
            on_process_start=_retain(retained),
        )
        count = _fd_count()
        if i == 0:
            baseline = count
        else:
            assert count == baseline, (
                f"FD leak at iteration {i + 1}: expected {baseline}, got {count}"
            )


def test_run_subprocess_fd_count_is_flat_after_inactivity_kill() -> None:
    """FD count stays flat across 20 inactivity-kill _run_subprocess calls."""
    retained: list[subprocess.Popen[str]] = []
    baseline: int | None = None
    for i in range(ITERATIONS):
        _run_subprocess(
            ["sh", "-c", "sleep 10"],
            inactivity_timeout=1,
            on_process_start=_retain(retained),
        )
        count = _fd_count()
        if i == 0:
            baseline = count
        else:
            assert count == baseline, (
                f"FD leak at iteration {i + 1}: expected {baseline}, got {count}"
            )


def test_run_subprocess_fd_count_is_flat_after_completion_event() -> None:
    """FD count stays flat across 20 completion-event _run_subprocess calls."""
    retained: list[subprocess.Popen[str]] = []
    baseline: int | None = None
    for i in range(ITERATIONS):
        event = threading.Event()
        timer = threading.Timer(0.05, event.set)
        timer.start()
        _run_subprocess(
            ["sh", "-c", "sleep 0.3"],
            completion_event=event,
            on_process_start=_retain(retained),
        )
        timer.cancel()
        count = _fd_count()
        if i == 0:
            baseline = count
        else:
            assert count == baseline, (
                f"FD leak at iteration {i + 1}: expected {baseline}, got {count}"
            )

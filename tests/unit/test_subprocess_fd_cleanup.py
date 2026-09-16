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

Pattern: 20 iterations, asserting that every retained process has closed pipes.
"""

import subprocess
import threading
from collections.abc import Callable

from fdsx.providers.base import _run_subprocess

ITERATIONS = 20


def _retain(
    bucket: list[subprocess.Popen[str]],
) -> Callable[[subprocess.Popen[str]], None]:
    def _cb(p: subprocess.Popen[str]) -> None:
        bucket.append(p)

    return _cb


def _assert_pipes_closed(process: subprocess.Popen[str]) -> None:
    assert process.stdout is not None
    assert process.stderr is not None
    assert process.stdout.closed
    assert process.stderr.closed


def test_run_subprocess_closes_pipes_after_normal_exit() -> None:
    """Pipes are closed after normal-exit _run_subprocess calls."""
    retained: list[subprocess.Popen[str]] = []
    for _ in range(ITERATIONS):
        _run_subprocess(["echo", "x"], on_process_start=_retain(retained))
        _assert_pipes_closed(retained[-1])


def test_run_subprocess_closes_pipes_after_timeout() -> None:
    """Pipes are closed after timeout-path _run_subprocess calls."""
    retained: list[subprocess.Popen[str]] = []
    for _ in range(ITERATIONS):
        _run_subprocess(
            ["sh", "-c", "sleep 5"],
            timeout=0.2,  # type: ignore[arg-type]
            on_process_start=_retain(retained),
        )
        _assert_pipes_closed(retained[-1])


def test_run_subprocess_closes_pipes_after_inactivity_kill() -> None:
    """Pipes are closed after inactivity-kill _run_subprocess calls."""
    retained: list[subprocess.Popen[str]] = []
    for _ in range(ITERATIONS):
        _run_subprocess(
            ["sh", "-c", "sleep 10"],
            inactivity_timeout=1,
            on_process_start=_retain(retained),
        )
        _assert_pipes_closed(retained[-1])


def test_run_subprocess_closes_pipes_after_completion_event() -> None:
    """Pipes are closed after completion-event _run_subprocess calls."""
    retained: list[subprocess.Popen[str]] = []
    for _ in range(ITERATIONS):
        event = threading.Event()
        timer = threading.Timer(0.05, event.set)
        timer.start()
        _run_subprocess(
            ["sh", "-c", "sleep 0.3"],
            completion_event=event,
            on_process_start=_retain(retained),
        )
        timer.cancel()
        _assert_pipes_closed(retained[-1])

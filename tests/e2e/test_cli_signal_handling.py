"""Integration tests for signal handling (Phase 5, T036).

Validates:
- SIGINT cleanup: no orphan child processes, lock file cleaned up
- "Workflow interrupted" message printed to stderr on SIGINT
- Exit code 130 (128 + SIGINT=2) on SIGINT
- Exit code 143 (128 + SIGTERM=15) on SIGTERM

These tests spawn fdsx as a real subprocess so that signal delivery, process
group management, and lock-file cleanup all follow the real code path.

All tests use a unique ``sleep 47`` command to detect orphan processes via
``pgrep``, and a fixed ``--thread-id`` so the lock file path is deterministic.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

# ── Constants ──────────────────────────────────────────────────────────────────

# Unique sleep duration so pgrep can reliably identify our child process.
_SLEEP_DURATION = 47

# Thread ID used for runs so the lock file path is deterministic.
_THREAD_ID = "signal-test-thread"

# Startup deadline, not a fixed delay; wait for the real child to report readiness.
_STARTUP_WAIT = 15

# Seconds to wait for fdsx to exit after receiving a signal.
_EXIT_WAIT = 15

# YAML content for a flow that runs sleep for a very long time.
_SLEEP_FLOW_YAML = f"""\
name: Signal Test Flow
description: Integration test flow for signal handling — runs a long sleep.
start_at: long_sleep
states:
  long_sleep:
    type: task
    provider: system
    command: "touch child-ready; exec sleep {_SLEEP_DURATION}"
    result_path: $.result
    end: true
"""

# ── Helpers ────────────────────────────────────────────────────────────────────


def _fdsx_bin() -> str:
    """Return the path to the fdsx executable in the current Python environment."""
    # Prefer the fdsx binary next to the current Python interpreter.
    candidate = Path(sys.executable).parent / "fdsx"
    if candidate.exists():
        return str(candidate)
    return "fdsx"


def _get_descendant_pids(parent_pid: int) -> list[int]:
    """Return PIDs of all descendant processes of *parent_pid*.

    Uses /proc to walk the process tree, avoiding global pgrep which can match
    processes from concurrent CI matrix jobs.
    """
    children: list[int] = []
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                pid = int(line.strip())
                children.append(pid)
                children.extend(_get_descendant_pids(pid))
    except (FileNotFoundError, ValueError):
        pass
    return children


def _is_pid_alive(pid: int) -> bool:
    """Return True if *pid* is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_path(base_dir: Path, thread_id: str) -> Path:
    """Return the expected lock file path for a given thread."""
    return base_dir / ".fdsx" / "locks" / f"{thread_id}.lock"


def _run_fdsx_and_signal(
    tmp_path: Path,
    sig: int,
    *,
    text: bool = False,
) -> "subprocess.Popen[str] | subprocess.Popen[bytes]":
    """Start fdsx with the sleep flow, send *sig*, wait for exit.

    Writes the flow YAML, spawns fdsx, waits for the subprocess to start,
    sends the signal, and waits for fdsx to exit.  Calls ``pytest.fail``
    if fdsx does not exit within ``_EXIT_WAIT`` seconds.

    Args:
        tmp_path: Temporary directory for the flow YAML and lock files.
        sig: Signal number to send (e.g. ``signal.SIGINT``).
        text: When True, open stdout/stderr in text mode (for stderr reading).

    Returns:
        The completed ``Popen`` object.
    """
    flow_path = tmp_path / "sleep_flow.yaml"
    flow_path.write_text(_SLEEP_FLOW_YAML)

    (tmp_path / ".fdsx").mkdir(exist_ok=True)

    proc = subprocess.Popen(
        [_fdsx_bin(), "run", str(flow_path), "--thread-id", _THREAD_ID],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )

    deadline = time.monotonic() + _STARTUP_WAIT
    while not (tmp_path / "child-ready").exists():
        if proc.poll() is not None or time.monotonic() >= deadline:
            proc.kill()
            proc.communicate(timeout=_EXIT_WAIT)
            pytest.fail("fdsx did not start the signal-test child")
        time.sleep(0.01)

    # Snapshot descendant PIDs before sending the signal so we can check
    # specifically *these* processes after fdsx exits, rather than using a
    # global pgrep that could match concurrent CI matrix jobs.
    proc._descendant_pids = _get_descendant_pids(proc.pid)  # type: ignore[attr-defined]

    proc.send_signal(sig)

    try:
        proc.wait(timeout=_EXIT_WAIT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=_EXIT_WAIT)
        pytest.fail(f"fdsx did not exit within {_EXIT_WAIT}s after signal {sig}")

    return proc


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sig, exit_code", [(signal.SIGINT, 130), (signal.SIGTERM, 143)]
)
def test_signal_exits_and_cleans_up(tmp_path: Path, sig: int, exit_code: int) -> None:
    """One run per signal checks exit status, message, locks, and child cleanup."""
    proc = _run_fdsx_and_signal(tmp_path, sig, text=True)
    try:
        _, stderr = proc.communicate(timeout=_EXIT_WAIT)
        assert proc.returncode == exit_code
        assert not _lock_path(tmp_path, _THREAD_ID).exists()
        if sig == signal.SIGINT:
            assert "Workflow interrupted" in stderr

        descendant_pids = getattr(proc, "_descendant_pids", [])
        assert descendant_pids, "Signal must interrupt an active child process"
        deadline = time.monotonic() + 10.0
        while True:
            orphans = [pid for pid in descendant_pids if _is_pid_alive(pid)]
            if not orphans or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        assert not orphans, f"Orphan processes after signal {sig}: {orphans}"
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()

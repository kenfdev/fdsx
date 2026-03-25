"""Integration tests for signal handling (Phase 5, T036).

Validates:
- SIGINT cleanup: no orphan child processes, lock file cleaned up
- "Workflow interrupted" message printed to stderr on SIGINT
- Exit code 130 (128 + SIGINT=2) on SIGINT
- Exit code 143 (128 + SIGTERM=15) on SIGTERM

These tests spawn fdsx as a real subprocess so that signal delivery, process
group management, and lock-file cleanup all follow the real code path.

All tests use a unique ``sleep 9973`` command to detect orphan processes via
``pgrep``, and a fixed ``--thread-id`` so the lock file path is deterministic.
"""
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

# ── Constants ──────────────────────────────────────────────────────────────────

# Unique sleep duration so pgrep can reliably identify our child process.
_SLEEP_DURATION = 9973

# Thread ID used for runs so the lock file path is deterministic.
_THREAD_ID = "signal-test-thread"

# Seconds to wait for the child sleep process to start before sending a signal.
_STARTUP_WAIT = 2.5

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
    command: "sleep {_SLEEP_DURATION}"
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


def _is_sleep_orphan_running() -> bool:
    """Return True if a 'sleep <_SLEEP_DURATION>' process is still alive."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"sleep {_SLEEP_DURATION}"],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # pgrep not available; skip orphan check
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

    proc = subprocess.Popen(
        [_fdsx_bin(), "run", str(flow_path), "--thread-id", _THREAD_ID],
        cwd=str(tmp_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )

    time.sleep(_STARTUP_WAIT)
    proc.send_signal(sig)

    try:
        proc.wait(timeout=_EXIT_WAIT)
    except subprocess.TimeoutExpired:
        proc.kill()
        pytest.fail(f"fdsx did not exit within {_EXIT_WAIT}s after signal {sig}")

    return proc


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestSigintCleanup:
    """SIGINT during active subprocess execution."""

    def test_sigint_exits_with_code_130(self, tmp_path: Path) -> None:
        """fdsx exits with code 130 (128+SIGINT) when SIGINT is sent."""
        proc = _run_fdsx_and_signal(tmp_path, signal.SIGINT)
        assert proc.returncode == 130, (
            f"Expected exit code 130, got {proc.returncode}"
        )

    def test_sigint_no_orphan_processes(self, tmp_path: Path) -> None:
        """No orphan sleep processes remain after SIGINT."""
        _run_fdsx_and_signal(tmp_path, signal.SIGINT)
        # Allow a brief moment for OS process table cleanup.
        time.sleep(0.5)
        assert not _is_sleep_orphan_running(), (
            f"Orphan 'sleep {_SLEEP_DURATION}' process still running after SIGINT"
        )

    def test_sigint_cleans_up_lock_file(self, tmp_path: Path) -> None:
        """Lock file is removed after SIGINT."""
        _run_fdsx_and_signal(tmp_path, signal.SIGINT)
        lock_file = _lock_path(tmp_path, _THREAD_ID)
        assert not lock_file.exists(), (
            f"Lock file still exists after SIGINT: {lock_file}"
        )

    def test_sigint_prints_workflow_interrupted_message(
        self, tmp_path: Path
    ) -> None:
        """'Workflow interrupted' message is printed to stderr on SIGINT."""
        proc = _run_fdsx_and_signal(tmp_path, signal.SIGINT, text=True)
        assert proc.stderr is not None
        stderr_output = proc.stderr.read()
        assert "Workflow interrupted" in stderr_output, (
            f"Expected 'Workflow interrupted' in stderr. Got:\n{stderr_output}"
        )


class TestSigtermCleanup:
    """SIGTERM during active subprocess execution."""

    def test_sigterm_exits_with_code_143(self, tmp_path: Path) -> None:
        """fdsx exits with code 143 (128+SIGTERM) when SIGTERM is sent."""
        proc = _run_fdsx_and_signal(tmp_path, signal.SIGTERM)
        assert proc.returncode == 143, (
            f"Expected exit code 143, got {proc.returncode}"
        )

    def test_sigterm_cleans_up_lock_file(self, tmp_path: Path) -> None:
        """Lock file is removed after SIGTERM."""
        _run_fdsx_and_signal(tmp_path, signal.SIGTERM)
        lock_file = _lock_path(tmp_path, _THREAD_ID)
        assert not lock_file.exists(), (
            f"Lock file still exists after SIGTERM: {lock_file}"
        )

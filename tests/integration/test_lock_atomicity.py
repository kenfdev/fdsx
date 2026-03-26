"""Integration tests for CheckpointManager lock atomicity (Phase 3, T015).

Validates:
- Concurrent lock acquisition: exactly one of two racing processes succeeds
- Stale lock auto-recovery: dead PID is cleaned up with a warning logged
- Release idempotency: releasing a non-held lock raises no error
"""

import multiprocessing
import os
import tempfile
from pathlib import Path

import pytest

from fdsx.checkpoint.manager import CheckpointManager

# ── Constants ──────────────────────────────────────────────────────────────────

_LOCK_THREAD_ID = "race-thread"
_DEAD_PID = 99999


# ── Helpers ────────────────────────────────────────────────────────────────────


def _try_acquire(
    base_dir_str: str,
    thread_id: str,
    barrier: "multiprocessing.Barrier",  # type: ignore[type-arg]
    result_queue: "multiprocessing.Queue[bool]",
) -> None:  # type: ignore[type-arg]
    """Target function for child processes: acquire lock and put result in queue.

    Uses a barrier to synchronize both processes so they attempt acquisition
    while both are still alive — preventing the stale-lock recovery path from
    making both succeed.
    """
    manager = CheckpointManager(base_dir=Path(base_dir_str))
    barrier.wait()  # ensure both processes are alive before racing
    result = manager.acquire_lock(thread_id)
    result_queue.put(result)
    barrier.wait()  # keep both alive until results are collected


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def base_dir() -> Path:
    """Provide a temporary directory that survives across forked processes."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def manager(base_dir: Path) -> CheckpointManager:
    return CheckpointManager(base_dir=base_dir)


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestLockAtomicity:
    def test_concurrent_lock_acquisition(self, base_dir: Path) -> None:
        """Exactly one of two concurrently racing processes must acquire the lock.

        Uses multiprocessing (not threading) because the lock is PID-based —
        threads share a PID and would both succeed or both fail.
        """
        result_queue: "multiprocessing.Queue[bool]" = multiprocessing.Queue()
        barrier = multiprocessing.Barrier(2, timeout=10)

        p1 = multiprocessing.Process(
            target=_try_acquire,
            args=(str(base_dir), _LOCK_THREAD_ID, barrier, result_queue),
        )
        p2 = multiprocessing.Process(
            target=_try_acquire,
            args=(str(base_dir), _LOCK_THREAD_ID, barrier, result_queue),
        )

        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        assert not p1.is_alive(), "Process 1 did not finish in time"
        assert not p2.is_alive(), "Process 2 did not finish in time"

        results = [result_queue.get_nowait(), result_queue.get_nowait()]
        assert sorted(results) == [False, True], (
            f"Expected exactly one True and one False, got {results}"
        )

    def test_stale_lock_auto_recovery(
        self, manager: CheckpointManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """acquire_lock must succeed and log a warning when the lock file holds a dead PID."""
        lock_path = manager._get_lock_path(_LOCK_THREAD_ID)
        lock_path.write_text(str(_DEAD_PID))

        import logging

        with caplog.at_level(logging.WARNING, logger="fdsx.checkpoint.manager"):
            result = manager.acquire_lock(_LOCK_THREAD_ID)

        assert result is True, (
            "Expected acquire_lock to succeed after stale lock removal"
        )

        # Verify the lock now contains our PID
        assert lock_path.exists()
        assert int(lock_path.read_text().strip()) == os.getpid()

        # Verify a warning was logged
        assert any(
            str(_DEAD_PID) in record.getMessage()
            or _LOCK_THREAD_ID in record.getMessage()
            for record in caplog.records
            if record.levelno == logging.WARNING
        ), f"Expected a warning about the dead PID. Got records: {caplog.records}"

    def test_release_idempotency(self, manager: CheckpointManager) -> None:
        """release_lock must not raise an error when no lock is held."""
        # Ensure the lock does not exist beforehand
        lock_path = manager._get_lock_path(_LOCK_THREAD_ID)
        assert not lock_path.exists()

        # Should complete without raising
        manager.release_lock(_LOCK_THREAD_ID)

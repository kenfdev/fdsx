import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.checkpoint.manager import CheckpointManager


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def manager(temp_dir):
    return CheckpointManager(base_dir=temp_dir)


class TestCheckpointManager:
    def test_default_base_dir(self):
        manager = CheckpointManager()
        assert manager.base_dir == Path(".fdsx")
        assert manager.checkpoints_dir == Path(".fdsx/checkpoints")
        assert manager.locks_dir == Path(".fdsx/locks")

    def test_custom_base_dir(self, temp_dir):
        manager = CheckpointManager(base_dir=temp_dir)
        assert manager.base_dir == temp_dir

    def test_directory_creation(self, manager):
        assert manager.checkpoints_dir.exists()
        assert manager.locks_dir.exists()
        assert manager.checkpoints_dir.is_dir()
        assert manager.locks_dir.is_dir()

    def test_get_checkpointer(self, manager):
        checkpointer = manager.get_checkpointer()
        assert checkpointer is not None
        db_path = manager.checkpoints_dir / "checkpoints.db"
        assert db_path.exists()

    def test_acquire_lock_new_thread(self, manager):
        result = manager.acquire_lock("test-thread-1")
        assert result is True
        lock_path = manager._get_lock_path("test-thread-1")
        assert lock_path.exists()

    def test_acquire_lock_same_thread_same_pid(self, manager):
        result1 = manager.acquire_lock("test-thread-2")
        assert result1 is True
        result2 = manager.acquire_lock("test-thread-2")
        assert result2 is False

    def test_acquire_lock_stale_lock_dead_pid(self, manager):
        lock_path = manager._get_lock_path("test-thread-3")
        with lock_path.open("w") as f:
            f.write("99999")
        result = manager.acquire_lock("test-thread-3")
        assert result is True
        with lock_path.open() as f:
            pid = int(f.read().strip())
        assert pid == os.getpid()

    def test_release_lock(self, manager):
        manager.acquire_lock("test-thread-4")
        manager.release_lock("test-thread-4")
        lock_path = manager._get_lock_path("test-thread-4")
        assert not lock_path.exists()

    def test_is_locked_locked_thread(self, manager):
        manager.acquire_lock("test-thread-5")
        is_locked, pid = manager.is_locked("test-thread-5")
        assert is_locked is True
        assert pid == os.getpid()

    def test_is_locked_unlocked_thread(self, manager):
        is_locked, pid = manager.is_locked("nonexistent-thread")
        assert is_locked is False
        assert pid is None

    def test_is_locked_stale_lock(self, manager):
        lock_path = manager._get_lock_path("test-thread-6")
        with lock_path.open("w") as f:
            f.write("99999")
        is_locked, pid = manager.is_locked("test-thread-6")
        assert is_locked is False
        assert pid is None

    def test_verify_checkpoint_missing_db(self, manager):
        result = manager.verify_checkpoint("test-thread")
        assert result is False

    def test_verify_checkpoint_corrupt_db(self, manager):
        db_path = manager.checkpoints_dir / "checkpoints.db"
        db_path.write_text("not a valid database")
        result = manager.verify_checkpoint("any-thread")
        assert result is False

    def test_verify_checkpoint_valid_checkpoint(self, manager):
        """T044: verify_checkpoint returns True for a valid checkpoint."""
        import sqlite3

        db_path = manager.checkpoints_dir / "checkpoints.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS checkpoints (
                thread_id TEXT PRIMARY KEY,
                checkpoint BLOB,
                metadata BLOB
            )
            """
        )
        cursor.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint, metadata) VALUES (?, ?, ?)",
            ("valid-thread", b"fake-checkpoint", b"fake-metadata"),
        )
        conn.commit()
        conn.close()

        result = manager.verify_checkpoint("valid-thread")
        assert result is True

    def test_list_threads_empty(self, manager):
        threads = manager.list_threads()
        assert threads == []

    def test_list_threads_includes_run_log_only_threads(self, manager, temp_dir):
        """T003: list_threads must include threads that have a run log but no checkpoint."""
        import json

        from fdsx.logging.recorder import RUN_FILENAME, RUNS_DIR_NAME

        # Create a run log directory for a thread with no checkpoint
        runs_dir = manager.base_dir / RUNS_DIR_NAME
        thread_dir = runs_dir / "run-log-only-thread"
        thread_dir.mkdir(parents=True, exist_ok=True)
        run_log = {
            "thread_id": "run-log-only-thread",
            "flow_name": "my_flow",
            "started_at": "2026-03-21T10:00:00+00:00",
            "status": "completed",
            "states": [],
        }
        run_log_path = thread_dir / RUN_FILENAME
        run_log_path.write_text(json.dumps(run_log))

        threads = manager.list_threads()

        thread_ids = [t["thread_id"] for t in threads]
        assert "run-log-only-thread" in thread_ids

        entry = next(t for t in threads if t["thread_id"] == "run-log-only-thread")
        assert entry["flow_name"] == "my_flow"

    def test_list_threads_no_duplicate_for_run_log_and_checkpoint(
        self, manager, temp_dir
    ):
        """T003: a thread present in both DB and run log must appear only once."""
        import json
        import sqlite3

        from fdsx.logging.recorder import RUN_FILENAME, RUNS_DIR_NAME

        # Add thread to checkpoint DB
        db_path = manager.checkpoints_dir / "checkpoints.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS checkpoints (
                thread_id TEXT PRIMARY KEY, checkpoint BLOB, metadata BLOB
            )"""
        )
        cursor.execute(
            "INSERT INTO checkpoints (thread_id, checkpoint, metadata) VALUES (?, ?, ?)",
            ("dual-thread", b"fake", b"fake"),
        )
        conn.commit()
        conn.close()

        # Also create a run log for the same thread
        runs_dir = manager.base_dir / RUNS_DIR_NAME
        thread_dir = runs_dir / "dual-thread"
        thread_dir.mkdir(parents=True, exist_ok=True)
        run_log = {
            "thread_id": "dual-thread",
            "flow_name": "my_flow",
            "started_at": "2026-03-21T10:00:00+00:00",
            "status": "completed",
            "states": [],
        }
        (thread_dir / RUN_FILENAME).write_text(json.dumps(run_log))

        threads = manager.list_threads()

        dual_entries = [t for t in threads if t["thread_id"] == "dual-thread"]
        assert len(dual_entries) == 1, (
            "Thread must appear exactly once even if in both DB and run log"
        )

    def test_list_threads_ignores_dirs_without_run_json(self, manager, temp_dir):
        """T003: directories in runs/ without a run.json file must not appear."""
        from fdsx.logging.recorder import RUNS_DIR_NAME

        runs_dir = manager.base_dir / RUNS_DIR_NAME
        # A directory without run.json
        empty_dir = runs_dir / "no-run-json-thread"
        empty_dir.mkdir(parents=True, exist_ok=True)

        threads = manager.list_threads()

        thread_ids = [t["thread_id"] for t in threads]
        assert "no-run-json-thread" not in thread_ids

    def test_list_threads_tolerates_corrupt_run_json(self, manager, temp_dir):
        """F3: corrupt run.json must not crash list_threads — targeted exception handling."""
        from fdsx.logging.recorder import RUN_FILENAME, RUNS_DIR_NAME

        runs_dir = manager.base_dir / RUNS_DIR_NAME
        thread_dir = runs_dir / "corrupt-thread"
        thread_dir.mkdir(parents=True, exist_ok=True)
        # Write invalid JSON
        (thread_dir / RUN_FILENAME).write_text("not valid json {{{")

        # Must not raise; thread appears with fallback values
        threads = manager.list_threads()
        thread_ids = [t["thread_id"] for t in threads]
        assert "corrupt-thread" in thread_ids
        entry = next(t for t in threads if t["thread_id"] == "corrupt-thread")
        # flow_name falls back to thread_id when JSON cannot be parsed
        assert entry["flow_name"] == "corrupt-thread"


class TestCheckpointManagerWithMock:
    def test_acquire_lock_concurrent_execution(self, manager):
        with patch("os.kill") as mock_kill:
            mock_kill.side_effect = OSError("Process not found")
            lock_path = manager._get_lock_path("concurrent-thread")
            with lock_path.open("w") as f:
                f.write("12345")
            result = manager.acquire_lock("concurrent-thread")
            assert result is True

    def test_verify_checkpoint_corrupt_db(self, manager):
        db_path = manager.checkpoints_dir / "checkpoints.db"
        db_path.write_text("not a valid database")
        result = manager.verify_checkpoint("any-thread")
        assert result is False

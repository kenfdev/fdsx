import os
import tempfile
from pathlib import Path

import pytest

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core import engine
from tests import FIXTURES_DIR


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def checkpoint_flow_path():
    return FIXTURES_DIR / "checkpoint_flow.yaml"


@pytest.fixture
def wait_resume_flow_path():
    return FIXTURES_DIR / "wait_resume_flow.yaml"


class TestPIDLock:
    def test_concurrent_execution_locked(self, temp_dir, checkpoint_flow_path):
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-concurrent-lock"

        manager = CheckpointManager(base_dir=base_dir)
        assert manager.acquire_lock(thread_id) is True

        with pytest.raises(RuntimeError, match="locked by PID"):
            engine.run_flow(
                checkpoint_flow_path,
                thread_id=thread_id,
                base_dir=base_dir,
            )

        manager.release_lock(thread_id)

    def test_stale_lock_cleanup(self, temp_dir):
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-stale-lock"

        manager = CheckpointManager(base_dir=base_dir)
        lock_path = manager._get_lock_path(thread_id)

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as f:
            f.write("99999")

        assert manager.acquire_lock(thread_id) is True

        with open(lock_path) as f:
            pid = int(f.read().strip())
        assert pid == os.getpid()


class TestCheckpointVerify:
    def test_verify_checkpoint_missing(self, temp_dir):
        base_dir = temp_dir / ".fdsx"
        manager = CheckpointManager(base_dir=base_dir)
        assert manager.verify_checkpoint("nonexistent") is False


class TestListThreads:
    def test_list_threads_empty(self, temp_dir):
        base_dir = temp_dir / ".fdsx"
        manager = CheckpointManager(base_dir=base_dir)
        threads = manager.list_threads()
        assert threads == []


class TestResumeFlow:
    def test_resume_nonexistent_thread(self, temp_dir):
        base_dir = temp_dir / ".fdsx"

        with pytest.raises(RuntimeError, match="No checkpoint found"):
            engine.resume_flow("nonexistent-thread", base_dir)

    def test_resume_corrupted_checkpoint(self, temp_dir, checkpoint_flow_path):
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-corrupt"

        engine.run_flow(
            checkpoint_flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
        )

        db_path = base_dir / "checkpoints" / "checkpoints.db"
        # Delete ALL SQLite sidecar files (WAL mode creates .db-wal and .db-shm)
        for suffix in ["", "-wal", "-shm"]:
            p = db_path.parent / (db_path.name + suffix)
            if p.exists():
                p.unlink()
        db_path.write_text("not a valid database")

        with pytest.raises(RuntimeError, match="checkpoint"):
            engine.resume_flow(thread_id, base_dir)


class TestCheckpointIntegrity:
    def test_checkpoint_integrity_missing_db(self, temp_dir):
        base_dir = temp_dir / ".fdsx"
        manager = CheckpointManager(base_dir=base_dir)
        assert manager.verify_checkpoint("any-thread") is False


class TestCLICommands:
    def test_run_with_checkpoint_flag(self, temp_dir, checkpoint_flow_path):
        """Test that --checkpoint flag creates checkpoint directory."""
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-cli-checkpoint"

        result = engine.run_flow(
            checkpoint_flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
        )

        assert result.get("plan_output") == "plan output"
        assert result.get("implement_output") == "implement output"
        assert result.get("review_output") == "review output"


class TestCheckpointSave:
    def test_checkpoint_save_creates_db(self, temp_dir, checkpoint_flow_path):
        """T051: run a flow → verify checkpoints.db exists and verify_checkpoint returns True."""
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-save"

        engine.run_flow(
            checkpoint_flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
        )

        db_path = base_dir / "checkpoints" / "checkpoints.db"
        assert db_path.exists()

        manager = CheckpointManager(base_dir=base_dir)
        assert manager.verify_checkpoint(thread_id) is True

    def test_list_threads_after_run(self, temp_dir, checkpoint_flow_path):
        """T051: list shows correct metadata after flow run."""
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-list-meta"

        engine.run_flow(
            checkpoint_flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
        )

        manager = CheckpointManager(base_dir=base_dir)
        threads = manager.list_threads()
        assert len(threads) >= 1
        thread_info = next(t for t in threads if t["thread_id"] == thread_id)
        assert thread_info["flow_name"] == "Checkpoint Test Flow"
        assert thread_info["status"] == "completed"
        assert thread_info["current_state"] != ""
        assert thread_info["started_at"] != ""


class TestResumeSuccess:
    def test_resume_wait_state_flow(self, temp_dir, wait_resume_flow_path):
        """T051: interrupt at Wait → resume with resume_flow → verify completion."""
        from unittest.mock import patch

        base_dir = temp_dir / ".fdsx"
        thread_id = "test-resume-wait"

        # Step 1: Run flow until Wait state, simulate crash at prompt
        with pytest.raises(RuntimeError, match="Flow execution failed"):
            with patch(
                "fdsx.core.engine.display_wait_prompt",
                side_effect=Exception("simulated crash"),
            ):
                engine.run_flow(
                    wait_resume_flow_path,
                    thread_id=thread_id,
                    base_dir=base_dir,
                )

        # Verify checkpoint was saved before the crash
        manager = CheckpointManager(base_dir=base_dir)
        assert manager.verify_checkpoint(thread_id) is True

        # Step 2: Resume from checkpoint with stdin mocked to select "1" (approve)
        with patch("builtins.input", return_value="1"):
            result = engine.resume_flow(thread_id, base_dir, wait_resume_flow_path)

        # Verify flow completed through the approve branch
        assert "status" in result, f"Expected 'status' in result, got: {result}"
        assert result["status"] == "approved"
        assert result.get("plan_output") == "plan output"


class TestScenario4FullResume:
    """T051 Scenario 4: Full resume from mid-execution crash (not at Wait state)."""

    def test_resume_after_crash_completes_remaining_states(
        self, temp_dir, checkpoint_flow_path
    ):
        """T051: crash mid-flow (implement state) → resume → all remaining states execute."""
        from unittest.mock import patch
        from fdsx.providers.system import SystemProvider

        base_dir = temp_dir / ".fdsx"
        thread_id = "test-scenario4"

        call_count = 0
        original_execute = SystemProvider().execute

        def crash_on_second_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("simulated crash on implement state")
            return original_execute(*args, **kwargs)

        # Step 1: Run flow until implement state crashes
        with pytest.raises(RuntimeError, match="Flow execution failed"):
            with patch.object(
                SystemProvider, "execute", side_effect=crash_on_second_call
            ):
                engine.run_flow(
                    checkpoint_flow_path,
                    thread_id=thread_id,
                    base_dir=base_dir,
                )

        # Verify checkpoint was saved (plan completed before crash)
        manager = CheckpointManager(base_dir=base_dir)
        assert manager.verify_checkpoint(thread_id) is True

        # Step 2: Resume WITHOUT mock — implement and review should now succeed
        result = engine.resume_flow(thread_id, base_dir, checkpoint_flow_path)

        assert result.get("plan_output") == "plan output"
        assert result.get("implement_output") == "implement output"
        assert result.get("review_output") == "review output"

    def test_list_shows_stopped_after_crash(self, temp_dir, checkpoint_flow_path):
        """T051: crash mid-flow → fdsx list shows 'stopped' not 'waiting'."""
        from unittest.mock import patch
        from fdsx.providers.system import SystemProvider

        base_dir = temp_dir / ".fdsx"
        thread_id = "test-scenario4-list"

        call_count = 0
        original_execute = SystemProvider().execute

        def crash_on_second_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("simulated crash")
            return original_execute(*args, **kwargs)

        with pytest.raises(RuntimeError, match="Flow execution failed"):
            with patch.object(
                SystemProvider, "execute", side_effect=crash_on_second_call
            ):
                engine.run_flow(
                    checkpoint_flow_path,
                    thread_id=thread_id,
                    base_dir=base_dir,
                )

        manager = CheckpointManager(base_dir=base_dir)
        threads = manager.list_threads()
        thread_info = next((t for t in threads if t["thread_id"] == thread_id), None)
        assert thread_info is not None
        assert thread_info["status"] == "stopped"
        assert thread_info["current_state"] == "implement"

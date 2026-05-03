"""Integration tests for resume_flow updating task YAML entry status.

Tests that when run_flow is called with task_file_path and task_entry_index,
the checkpoint stores this metadata and resume_flow uses it to update the
task entry status after completion.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.core import engine
from fdsx.core.engine import AbortInfo
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file
from tests import FIXTURES_DIR


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def checkpoint_flow_path():
    return FIXTURES_DIR / "checkpoint_flow.yaml"


def _make_task_file(path: Path, status: str = "pending") -> None:
    """Write a single-entry task YAML to path."""
    task_file = TaskFile(
        entries=[TaskEntry(description="Test task", status=status)]  # type: ignore[arg-type]
    )
    save_task_file(path, task_file)


class TestResumeUpdatesTaskEntry:
    def test_resume_updates_entry_to_completed(self, temp_dir, checkpoint_flow_path):
        """After resuming a flow started via run_flow with task_file_path, the
        task entry status should be updated to 'completed'."""
        base_dir = temp_dir / ".fdsx"
        task_file_path = temp_dir / "task.yaml"
        _make_task_file(task_file_path, status="running")

        thread_id = "test-resume-task-completed"

        # Run the flow with task_file_path stored in _meta
        engine.run_flow(
            checkpoint_flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
            task_file_path=task_file_path,
            task_entry_index=0,
        )

        # Simulate that the task file entry is still "running" (e.g. crash before update)
        _make_task_file(task_file_path, status="running")

        # Resume the flow — it should update the task entry to "completed"
        result = engine.resume_flow(thread_id, base_dir=base_dir)

        assert result.status == "completed"

        reloaded = load_task_file(task_file_path)
        assert reloaded.entries[0].status == "completed"
        assert reloaded.entries[0].thread_id == thread_id
        assert reloaded.entries[0].error is None

    def test_resume_updates_entry_to_failed_on_abort(
        self, temp_dir, checkpoint_flow_path
    ):
        """After resuming a flow that is detected as aborted, the task entry
        status should be updated to 'failed' with an appropriate error message."""
        base_dir = temp_dir / ".fdsx"
        task_file_path = temp_dir / "task.yaml"
        _make_task_file(task_file_path, status="running")

        thread_id = "test-resume-task-aborted"

        engine.run_flow(
            checkpoint_flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
            task_file_path=task_file_path,
            task_entry_index=0,
        )

        # Reset entry to running
        _make_task_file(task_file_path, status="running")

        # Patch _detect_abort_status to simulate an aborted flow
        with patch(
            "fdsx.core.engine.resume._detect_abort_status",
            return_value=(
                "aborted",
                AbortInfo(
                    state_name="abort_blocked", error_name=None, error_cause=None
                ),
            ),
        ):
            result = engine.resume_flow(thread_id, base_dir=base_dir)

        assert result.status == "aborted"

        reloaded = load_task_file(task_file_path)
        assert reloaded.entries[0].status == "failed"
        assert reloaded.entries[0].thread_id == thread_id
        assert reloaded.entries[0].error is not None
        assert "abort_blocked" in reloaded.entries[0].error

    def test_resume_missing_task_file_no_error(self, temp_dir, checkpoint_flow_path):
        """When the task file no longer exists at resume time, resume_flow must
        not raise — the update is best-effort."""
        base_dir = temp_dir / ".fdsx"
        task_file_path = temp_dir / "task.yaml"
        _make_task_file(task_file_path, status="running")

        thread_id = "test-resume-task-missing-file"

        engine.run_flow(
            checkpoint_flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
            task_file_path=task_file_path,
            task_entry_index=0,
        )

        # Delete the task file before resuming
        task_file_path.unlink()

        # resume_flow must complete normally without raising
        result = engine.resume_flow(thread_id, base_dir=base_dir)
        assert result.status == "completed"

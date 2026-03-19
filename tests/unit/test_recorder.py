import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from fdsx.logging.recorder import RunRecorder, OUTPUT_PREVIEW_MAX_LENGTH


class TestRunRecorder:
    def test_init(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
            flow_version="1.0",
        )

        assert recorder.thread_id == "test-123"
        assert recorder.flow_name == "test_flow"
        assert recorder.flow_version == "1.0"
        assert recorder.status == "running"
        assert recorder.states == []
        assert recorder.started_at is not None

        dt = datetime.fromisoformat(recorder.started_at.replace("Z", "+00:00"))
        assert dt.tzinfo is not None

    def test_init_without_version(self):
        recorder = RunRecorder(
            thread_id="test-456",
            flow_name="another_flow",
        )

        assert recorder.flow_version is None

    def test_record_state_start(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        recorder.record_state_start("planner", "task")

        assert len(recorder.states) == 1
        state = recorder.states[0]
        assert state["name"] == "planner"
        assert state["type"] == "task"
        assert state["started_at"] is not None

    def test_record_state_complete(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        recorder.record_state_start("planner", "task")
        recorder.record_state_complete(
            "planner",
            "success",
            "Test output",
            ["$.plan"],
        )

        state = recorder.states[0]
        assert state["completed_at"] is not None
        assert state["duration_seconds"] >= 0
        assert state["status"] == "success"
        assert state["output_preview"] == "Test output"
        assert state["variables_set"] == ["$.plan"]

    def test_output_preview_truncation(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        long_output = "x" * (OUTPUT_PREVIEW_MAX_LENGTH + 100)
        recorder.record_state_start("planner", "task")
        recorder.record_state_complete(
            "planner",
            "success",
            long_output,
            ["$.plan"],
        )

        state = recorder.states[0]
        assert len(state["output_preview"]) == OUTPUT_PREVIEW_MAX_LENGTH

    def test_record_state_error(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        recorder.record_state_start("planner", "task")
        recorder.record_state_error("planner", "Something went wrong")

        state = recorder.states[0]
        assert state["status"] == "error"
        assert state["error"] == "Something went wrong"
        assert state["completed_at"] is not None
        assert "duration_seconds" in state
        assert "output_preview" in state
        assert "variables_set" in state
        assert state["variables_set"] == []

    def test_record_state_complete_with_branches(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        branches = [
            {
                "index": 0,
                "provider": "claude",
                "status": "success",
                "duration_seconds": 10,
            },
            {
                "index": 1,
                "provider": "opencode",
                "status": "success",
                "duration_seconds": 5,
            },
        ]

        recorder.record_state_start("parallel_review", "parallel")
        recorder.record_state_complete(
            "parallel_review",
            "success",
            "",
            ["$.reviews"],
            branches,
        )

        state = recorder.states[0]
        assert state["branches"] == branches

    def test_finalize(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        recorder.finalize({"plan": "my plan"}, "completed")

        assert recorder.completed_at is not None
        assert recorder.status == "completed"
        assert recorder.final_variables == {"plan": "my plan"}

    def test_finalize_with_error_status(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        recorder.finalize({}, "error")

        assert recorder.status == "error"

    def test_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                thread_id="test-123",
                flow_name="test_flow",
            )

            recorder.record_state_start("planner", "task")
            recorder.record_state_complete(
                "planner",
                "success",
                "Test output",
                ["$.plan"],
            )
            recorder.finalize({"plan": "my plan"}, "completed")

            file_path = recorder.save(base_dir=Path(tmpdir))

            assert file_path == Path(tmpdir) / "runs" / "test-123.json"
            assert file_path.exists()

            with open(file_path, "r") as f:
                data = json.load(f)

            assert data["thread_id"] == "test-123"
            assert data["flow_name"] == "test_flow"
            assert data["status"] == "completed"
            assert len(data["states"]) == 1
            assert data["final_variables"] == {"plan": "my plan"}

    def test_save_creates_runs_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                thread_id="test-123",
                flow_name="test_flow",
            )

            recorder.save(base_dir=Path(tmpdir))

            runs_dir = Path(tmpdir) / "runs"
            assert runs_dir.exists()
            assert runs_dir.is_dir()

    def test_save_resume_append(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            runs_dir.mkdir()

            existing_log = {
                "thread_id": "test-123",
                "flow_name": "test_flow",
                "started_at": "2026-03-14T10:00:00Z",
                "status": "running",
                "states": [
                    {
                        "name": "planner",
                        "type": "task",
                        "started_at": "2026-03-14T10:00:00Z",
                        "completed_at": "2026-03-14T10:01:00Z",
                        "duration_seconds": 60,
                        "status": "success",
                        "output_preview": "Old output",
                        "variables_set": ["$.plan"],
                    }
                ],
            }

            existing_file = runs_dir / "test-123.json"
            with open(existing_file, "w") as f:
                json.dump(existing_log, f)

            recorder = RunRecorder(
                thread_id="test-123",
                flow_name="test_flow",
            )

            recorder.record_state_start("implementer", "task")
            recorder.record_state_complete(
                "implementer",
                "success",
                "New output",
                ["$.implementation"],
            )
            recorder.finalize(
                {"plan": "my plan", "implementation": "code"}, "completed"
            )

            recorder.save(base_dir=Path(tmpdir))

            with open(existing_file, "r") as f:
                data = json.load(f)

            assert len(data["states"]) == 2
            assert data["states"][0]["name"] == "planner"
            assert data["states"][1]["name"] == "implementer"
            assert data["started_at"] == "2026-03-14T10:00:00Z"

    def test_to_dict(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
            flow_version="1.0",
        )

        recorder.record_state_start("planner", "task")
        recorder.record_state_complete(
            "planner",
            "success",
            "Test output",
            ["$.plan"],
        )
        recorder.finalize({"plan": "my plan"}, "completed")

        data = recorder.to_dict()

        assert data["thread_id"] == "test-123"
        assert data["flow_name"] == "test_flow"
        assert data["flow_version"] == "1.0"
        assert data["status"] == "completed"
        assert "started_at" in data
        assert "completed_at" in data
        assert len(data["states"]) == 1
        assert data["final_variables"] == {"plan": "my plan"}

    def test_to_dict_without_finalize(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        recorder.record_state_start("planner", "task")

        data = recorder.to_dict()

        assert "completed_at" not in data
        assert "final_variables" not in data

    def test_record_state_complete_updates_existing(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        recorder.record_state_start("planner", "task")
        recorder.record_state_complete(
            "planner",
            "success",
            "Test output",
            ["$.plan"],
        )

        assert len(recorder.states) == 1

    def test_record_state_error_updates_existing(self):
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        recorder.record_state_start("planner", "task")
        recorder.record_state_error("planner", "Error message")

        assert len(recorder.states) == 1
        assert recorder.states[0]["status"] == "error"


class TestRunRecorderSecurity:
    def test_invalid_thread_id_with_dot_dot_slash(self):
        with pytest.raises(ValueError, match="Invalid thread_id"):
            RunRecorder(
                thread_id="../../../etc/passwd",
                flow_name="test_flow",
            )

    def test_invalid_thread_id_with_slash(self):
        with pytest.raises(ValueError, match="Invalid thread_id"):
            RunRecorder(
                thread_id="test/thread",
                flow_name="test_flow",
            )

    def test_invalid_thread_id_with_dot(self):
        with pytest.raises(ValueError, match="Invalid thread_id"):
            RunRecorder(
                thread_id="test.thread",
                flow_name="test_flow",
            )

    def test_valid_thread_id_with_hyphen_underscore(self):
        recorder = RunRecorder(
            thread_id="test-thread_123",
            flow_name="test_flow",
        )
        assert recorder.thread_id == "test-thread_123"

    def test_save_creates_secure_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                thread_id="test-123",
                flow_name="test_flow",
            )
            recorder.finalize({"key": "value"}, "completed")

            file_path = recorder.save(base_dir=Path(tmpdir))

            import os

            stat_info = os.stat(file_path)
            mode = stat_info.st_mode & 0o777
            assert mode == 0o600

    def test_save_directory_secure_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = RunRecorder(
                thread_id="test-123",
                flow_name="test_flow",
            )
            recorder.finalize({"key": "value"}, "completed")

            recorder.save(base_dir=Path(tmpdir))

            runs_dir = Path(tmpdir) / "runs"
            import os

            stat_info = os.stat(runs_dir)
            mode = stat_info.st_mode & 0o777
            assert mode == 0o700

    def test_save_hardens_preexisting_directory(self):
        """Regression: existing runs/ dir with 0o755 must be tightened to 0o700."""
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            runs_dir = Path(tmpdir) / "runs"
            runs_dir.mkdir(mode=0o755)
            assert (os.stat(runs_dir).st_mode & 0o777) == 0o755

            recorder = RunRecorder(
                thread_id="test-hardendir",
                flow_name="test_flow",
            )
            recorder.finalize({"key": "value"}, "completed")
            recorder.save(base_dir=Path(tmpdir))

            stat_info = os.stat(runs_dir)
            mode = stat_info.st_mode & 0o777
            assert mode == 0o700

    def test_record_state_complete_state_type_fallback(self):
        """Regression: record_state_complete without prior start uses state_type param, not 'unknown'."""
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        recorder.record_state_complete(
            "approval",
            "success",
            "approved",
            ["$.result"],
            state_type="wait",
        )

        assert len(recorder.states) == 1
        assert recorder.states[0]["type"] == "wait"

    def test_wait_state_no_duplicate_entry_on_normal_path(self):
        """Regression: notify start + interrupt complete must produce exactly one wait entry."""
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        # Simulate notify node: record_state_start
        recorder.record_state_start("approval", "wait")
        # Simulate interrupt node: record_state_complete only (no second start)
        recorder.record_state_complete(
            "approval",
            "success",
            "yes",
            ["$.answer"],
            state_type="wait",
        )

        assert len(recorder.states) == 1
        state = recorder.states[0]
        assert state["name"] == "approval"
        assert state["type"] == "wait"
        assert state["status"] == "success"

    def test_wait_state_resume_path_synthesizes_correct_type(self):
        """Regression: on resume (no notify), complete with state_type='wait' sets type correctly."""
        recorder = RunRecorder(
            thread_id="test-123",
            flow_name="test_flow",
        )

        # On resume path, no notify node ran — only interrupt node calls complete
        recorder.record_state_complete(
            "approval",
            "success",
            "yes",
            ["$.answer"],
            state_type="wait",
        )

        assert len(recorder.states) == 1
        assert recorder.states[0]["type"] == "wait"

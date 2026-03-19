import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fdsx.core import engine
from fdsx.models.flow import Flow, TaskState


class TestBatchExecution:
    def test_full_batch_flow(self):
        flow_path = Path("tests/fixtures/batch_flow.yaml")
        tasks_file = Path("tests/fixtures/sample_tasks.md")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task 1\n2. Task 2\n3. Task 3",
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            with patch("fdsx.core.engine.display_task_list", return_value=True):
                with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        base_dir = Path(tmpdir)
                        results = engine.run_batch(flow_path, tasks_file, base_dir)

        assert len(results) == 3
        thread_ids = [r["thread_id"] for r in results]
        assert len(thread_ids) == len(set(thread_ids)), (
            "All thread_ids should be unique"
        )
        for result in results:
            assert result["status"] in ["completed", "failed"]
            assert result["task_description"] is not None
            assert result["thread_id"] is not None

    def test_batch_rejection(self):
        flow_path = Path("tests/fixtures/batch_flow.yaml")
        tasks_file = Path("tests/fixtures/sample_tasks.md")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task 1\n2. Task 2\n3. Task 3",
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            with patch("fdsx.core.engine.display_task_list", return_value=False):
                with tempfile.TemporaryDirectory() as tmpdir:
                    base_dir = Path(tmpdir)
                    results = engine.run_batch(flow_path, tasks_file, base_dir)

        assert results == []

    def test_missing_task_splitter(self):
        flow = Flow(
            name="Test Flow",
            start_at="plan",
            states={
                "plan": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.result",
                    end=True,
                )
            },
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            yaml.dump(flow.model_dump(), f)
            flow_path = Path(f.name)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Test task\n")
            tasks_file = Path(f.name)

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                base_dir = Path(tmpdir)
                with pytest.raises(engine.FlowValidationError, match="task_splitter"):
                    engine.run_batch(flow_path, tasks_file, base_dir)
        finally:
            flow_path.unlink()
            tasks_file.unlink()

    def test_mutual_exclusion_validation(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fdsx.cli.main",
                "run",
                "tests/fixtures/batch_flow.yaml",
                "--input",
                "foo=bar",
                "--tasks",
                "tests/fixtures/sample_tasks.md",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2
        assert "mutually exclusive" in result.stderr.lower()


class TestBatchIntegrationWithMockedInput:
    def test_batch_with_failing_task_continue(self):
        flow_path = Path("tests/fixtures/batch_flow.yaml")
        tasks_file = Path("tests/fixtures/sample_tasks.md")

        call_count = [0]

        def mock_run_flow(flow_path, inputs, thread_id, base_dir):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Task failed intentionally")
            return {"result": "ok"}

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task 1\n2. Task 2\n3. Task 3",
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            with patch("fdsx.core.engine.display_task_list", return_value=True):
                with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        base_dir = Path(tmpdir)
                        with patch("fdsx.core.engine.input", side_effect=["y", "y"]):
                            results = engine.run_batch(flow_path, tasks_file, base_dir)

        assert len(results) == 3

    def test_batch_with_failing_task_stop(self):
        flow_path = Path("tests/fixtures/batch_flow.yaml")
        tasks_file = Path("tests/fixtures/sample_tasks.md")

        call_count = [0]

        def mock_run_flow(flow_path, inputs, thread_id, base_dir):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Task failed intentionally")
            return {"result": "ok"}

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task 1\n2. Task 2\n3. Task 3",
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            with patch("fdsx.core.engine.display_task_list", return_value=True):
                with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        base_dir = Path(tmpdir)
                        with patch("fdsx.core.engine.input", side_effect=["n"]):
                            results = engine.run_batch(flow_path, tasks_file, base_dir)

        assert len(results) == 2

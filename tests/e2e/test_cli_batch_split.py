import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fdsx.core import engine
from fdsx.core.config import FdsxConfig, TaskSplitterConfig
from tests import FIXTURES_DIR
from tests.e2e.cli_test_utils import fixture_path, run_fdsx


class TestBatchExecution:
    def test_full_batch_flow(self):
        flow_path = FIXTURES_DIR / "batch_flow.yaml"
        tasks_file = FIXTURES_DIR / "sample_tasks.md"

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task 1\n2. Task 2\n3. Task 3",
            stderr="",
        )

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            patch(
                "fdsx.core.engine.batch.load_config",
                return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
            ),
            patch("fdsx.core.engine.batch.display_task_list", return_value=True),
            patch("fdsx.core.engine.batch.run_flow", return_value={"result": "ok"}),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
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
        flow_path = FIXTURES_DIR / "batch_flow.yaml"
        tasks_file = FIXTURES_DIR / "sample_tasks.md"

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task 1\n2. Task 2\n3. Task 3",
            stderr="",
        )

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            patch(
                "fdsx.core.engine.batch.load_config",
                return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
            ),
            patch("fdsx.core.engine.batch.display_task_list", return_value=False),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            base_dir = Path(tmpdir)
            results = engine.run_batch(flow_path, tasks_file, base_dir)

        assert results == []

    def test_batch_fails_when_task_splitter_not_configured(self):
        """Regression: run_batch must raise FlowValidationError when task_splitter is None in config."""
        flow_path = FIXTURES_DIR / "batch_flow.yaml"
        tasks_file = FIXTURES_DIR / "sample_tasks.md"

        with (
            patch(
                "fdsx.core.engine.batch.load_config",
                return_value=FdsxConfig(task_splitter=None),
            ),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            base_dir = Path(tmpdir)
            with pytest.raises(engine.FlowValidationError, match="task_splitter"):
                engine.run_batch(flow_path, tasks_file, base_dir)

    def test_mutual_exclusion_validation(self):
        result = run_fdsx(
            [
                "run",
                fixture_path("batch_flow.yaml"),
                "--input",
                "foo=bar",
                "--tasks",
                fixture_path("sample_tasks.md"),
            ]
        )

        assert result.returncode == 2
        assert "mutually exclusive" in result.stderr.lower()


class TestBatchIntegrationWithMockedInput:
    def test_batch_with_failing_task_continue(self):
        flow_path = FIXTURES_DIR / "batch_flow.yaml"
        tasks_file = FIXTURES_DIR / "sample_tasks.md"

        call_count = [0]

        def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
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

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            patch(
                "fdsx.core.engine.batch.load_config",
                return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
            ),
            patch("fdsx.core.engine.batch.display_task_list", return_value=True),
            patch("fdsx.core.engine.batch.run_flow", side_effect=mock_run_flow),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            base_dir = Path(tmpdir)
            with patch("fdsx.core.engine.batch.input", side_effect=["y", "y"]):
                results = engine.run_batch(flow_path, tasks_file, base_dir)

        assert len(results) == 3

    def test_batch_with_failing_task_stop(self):
        flow_path = FIXTURES_DIR / "batch_flow.yaml"
        tasks_file = FIXTURES_DIR / "sample_tasks.md"

        call_count = [0]

        def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
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

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            patch(
                "fdsx.core.engine.batch.load_config",
                return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
            ),
            patch("fdsx.core.engine.batch.display_task_list", return_value=True),
            patch("fdsx.core.engine.batch.run_flow", side_effect=mock_run_flow),
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            base_dir = Path(tmpdir)
            with patch("fdsx.core.engine.batch.input", side_effect=["n"]):
                results = engine.run_batch(flow_path, tasks_file, base_dir)

        assert len(results) == 2


class TestBatchQuietFlagPropagation:
    def test_run_batch_passes_quiet_true_to_run_flow(self):
        flow_path = FIXTURES_DIR / "batch_flow.yaml"
        tasks_file = FIXTURES_DIR / "sample_tasks.md"

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task 1\n2. Task 2",
            stderr="",
        )

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            patch(
                "fdsx.core.engine.batch.load_config",
                return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
            ),
            patch("fdsx.core.engine.batch.display_task_list", return_value=True),
            patch("fdsx.core.engine.batch.run_flow") as mock_run_flow,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            base_dir = Path(tmpdir)
            engine.run_batch(flow_path, tasks_file, base_dir, quiet=True)

        assert mock_run_flow.called
        for call_args in mock_run_flow.call_args_list:
            assert call_args.kwargs.get("quiet") is True

    def test_run_batch_passes_quiet_false_by_default(self):
        flow_path = FIXTURES_DIR / "batch_flow.yaml"
        tasks_file = FIXTURES_DIR / "sample_tasks.md"

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task 1",
            stderr="",
        )

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            patch(
                "fdsx.core.engine.batch.load_config",
                return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
            ),
            patch("fdsx.core.engine.batch.display_task_list", return_value=True),
            patch("fdsx.core.engine.batch.run_flow") as mock_run_flow,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            base_dir = Path(tmpdir)
            engine.run_batch(flow_path, tasks_file, base_dir)

        assert mock_run_flow.called
        for call_args in mock_run_flow.call_args_list:
            assert call_args.kwargs.get("quiet") is False


class TestBatchSourceInjection:
    def test_run_batch_injects_source_as_tasks_file_path(self):
        flow_path = FIXTURES_DIR / "batch_flow.yaml"
        tasks_file = FIXTURES_DIR / "sample_tasks.md"

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task 1\n2. Task 2",
            stderr="",
        )

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            patch(
                "fdsx.core.engine.batch.load_config",
                return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
            ),
            patch("fdsx.core.engine.batch.display_task_list", return_value=True),
            patch("fdsx.core.engine.batch.run_flow") as mock_run_flow,
            tempfile.TemporaryDirectory() as tmpdir,
        ):
            base_dir = Path(tmpdir)
            engine.run_batch(flow_path, tasks_file, base_dir)

        assert mock_run_flow.called
        for call_args in mock_run_flow.call_args_list:
            inputs = call_args.kwargs.get("inputs") or call_args.args[1]
            assert inputs["source"] == str(tasks_file)

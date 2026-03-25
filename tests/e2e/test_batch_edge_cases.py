"""E2E tests for edge cases (T42): empty dir, all completed, single task, invalid YAML mix."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core import engine
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file
from tests import FIXTURES_DIR


class TestEdgeCases:
    """T42: Edge case handling."""

    def test_empty_tasks_dir_error_via_cli(self, tmp_path):
        """Empty tasks directory should produce a clear error via CLI."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        workflow_path = FIXTURES_DIR / "simple_flow.yaml"

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run",
                str(workflow_path),
                "--tasks-dir",
                str(tasks_dir),
                "--auto-workflow",
            ],
        )

        assert result.exit_code == 2, f"Expected exit code 2, got {result.exit_code}"
        assert (
            "no .yaml" in result.stderr.lower() or "empty" in result.stderr.lower()
        ), f"Error should mention no YAML files: {result.stderr}"

    def test_all_tasks_completed_multi_file_noop(self, tmp_path):
        """Multiple files with all entries completed should result in no run_flow calls."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        flow_path = FIXTURES_DIR / "batch_flow.yaml"

        tf1 = TaskFile(
            entries=[
                TaskEntry(description="task 1", status="completed"),
                TaskEntry(description="task 2", status="completed"),
            ]
        )
        save_task_file(tasks_dir / "001-file1.yaml", tf1)

        tf2 = TaskFile(
            entries=[
                TaskEntry(description="task 3", status="completed"),
                TaskEntry(description="task 4", status="completed"),
            ]
        )
        save_task_file(tasks_dir / "002-file2.yaml", tf2)

        with patch(
            "fdsx.core.engine.tasks_dir.run_flow", return_value={"result": "ok"}
        ) as mock_run:
            with patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

        mock_run.assert_not_called()
        assert len(results) == 4
        for r in results:
            assert r["category"] == "skipped"
            assert r["status"] == "completed"

    def test_single_task_file_single_entry(self, tmp_path):
        """One file with one entry should execute correctly."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        flow_path = FIXTURES_DIR / "batch_flow.yaml"

        tf = TaskFile(entries=[TaskEntry(description="single task")])
        save_task_file(tasks_dir / "001-single.yaml", tf)

        with patch(
            "fdsx.core.engine.tasks_dir.run_flow", return_value={"result": "ok"}
        ):
            with patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

        assert len(results) == 1
        assert results[0]["status"] == "completed"
        assert results[0]["category"] == "new"

        # After all entries complete, the file is moved to completed/
        loaded = load_task_file(tasks_dir / "completed" / "001-single.yaml")
        assert loaded.entries[0].status == "completed"

    def test_mix_valid_and_invalid_yaml_files(self, tmp_path):
        """Mix of valid and invalid YAML files should error clearly."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        (tasks_dir / "001-valid.yaml").write_text("description: valid task\n")
        (tasks_dir / "002-invalid.yaml").write_text(": [broken yaml\n")
        (tasks_dir / "003-also-valid.yaml").write_text("description: another valid\n")

        workflow_path = FIXTURES_DIR / "simple_flow.yaml"

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run",
                str(workflow_path),
                "--tasks-dir",
                str(tasks_dir),
                "--auto-workflow",
            ],
        )

        assert result.exit_code == 2
        assert "002-invalid.yaml" in result.stderr or "invalid" in result.stderr.lower()

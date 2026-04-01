"""E2E tests for clear error messages (T40): invalid task file scenarios."""

import pytest
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core import engine
from tests import FIXTURES_DIR


class TestErrorMessages:
    """T40: Clear error messages for various failure scenarios."""

    def test_invalid_yaml_task_file_via_cli(self, tmp_path):
        """Invalid YAML in task file should produce a clear error via CLI."""
        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-bad.yaml").write_text(": [broken yaml\n")

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
        assert "001-bad.yaml" in result.stderr or "invalid" in result.stderr.lower(), (
            f"Error should mention the invalid file: {result.stderr}"
        )

    def test_invalid_yaml_task_file_via_api(self, tmp_path):
        """Invalid YAML in task file should produce a clear error via API."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-bad.yaml").write_text(": [broken yaml\n")

        workflow_path = FIXTURES_DIR / "simple_flow.yaml"

        with pytest.raises(engine.FlowValidationError) as exc_info:
            engine.run_tasks_dir(workflow_path, tasks_dir, auto_workflow=True)

        assert (
            "001-bad.yaml" in str(exc_info.value)
            or "invalid" in str(exc_info.value).lower()
        ), f"Error should mention the invalid file: {exc_info.value}"

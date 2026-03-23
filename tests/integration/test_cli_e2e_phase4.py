"""End-to-end CLI tests for Phase 4 batch task scenarios."""

import tempfile
from pathlib import Path

from unittest.mock import patch

from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.providers.base import ProviderResult
from tests.integration.cli_test_utils import fixture_path, run_fdsx


class TestBatchCLIE2E:
    """End-to-end CLI tests for batch task execution (T070)."""

    def test_batch_full_execution_with_approval(self):
        """Test fdsx run --tasks via CLI: approve → all tasks execute → exit 0, no JSON."""
        runner = CliRunner()
        flow_path = str(Path("tests/fixtures/batch_flow.yaml").resolve())
        tasks_path = str(Path("tests/fixtures/sample_tasks.md").resolve())

        mock_task_result = ProviderResult(
            exit_code=0,
            stdout="1. Implement user authentication feature\n2. Add search functionality\n3. Write API documentation",
            stderr="",
        )

        class MockTaskSplitterProvider:
            def execute(self, prompt, model=None, **kwargs):
                return mock_task_result

        with patch(
            "fdsx.core.batch.get_provider",
            return_value=MockTaskSplitterProvider(),
        ):
            with patch("fdsx.core.engine.display_task_list", return_value=True):
                result = runner.invoke(
                    app,
                    ["run", flow_path, "--tasks", tasks_path],
                )

        assert result.exit_code == 0, (
            f"output: {result.output}\nexception: {result.exception}"
        )
        # FR-1.3: No JSON on stdout
        assert result.output == "" or not result.output.startswith("[")

    def test_input_tasks_mutual_exclusion(self):
        """Test --input and --tasks together → exit code 2, mutual exclusion error."""
        result = run_fdsx(
            [
                "run",
                fixture_path("batch_flow.yaml"),
                "--input",
                "task=foo",
                "--tasks",
                fixture_path("sample_tasks.md"),
            ],
            timeout=30,
        )

        assert result.returncode == 2, (
            f"Expected exit code 2, got {result.returncode}. stderr: {result.stderr}"
        )
        assert "mutually exclusive" in result.stderr.lower()

    def test_batch_missing_description(self):
        """Test --tasks with flow missing description → exit code 2 with actionable error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_path = Path(tmpdir) / "no_description_flow.yaml"
            flow_path.write_text(
                "name: No Description Flow\n"
                "start_at: task1\n"
                "version: '1.0'\n"
                "\n"
                "states:\n"
                "  task1:\n"
                "    type: task\n"
                "    provider: system\n"
                "    command: echo hello\n"
                "    result_path: $.result\n"
                "    end: true\n"
            )

            tasks_path = Path(tmpdir) / "tasks.md"
            tasks_path.write_text("Task 1\nTask 2\n")

            result = run_fdsx(
                [
                    "run",
                    str(flow_path),
                    "--tasks",
                    str(tasks_path),
                ],
                input="y\n",
                timeout=30,
                cwd=tmpdir,
            )

            assert result.returncode == 2, (
                f"Expected exit code 2, got {result.returncode}. stderr: {result.stderr}"
            )
            assert "description" in result.stderr.lower()

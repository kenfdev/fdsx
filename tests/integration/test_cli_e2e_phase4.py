"""End-to-end CLI tests for Phase 4 batch task scenarios."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from unittest.mock import patch

from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.providers.base import ProviderResult


def get_fdsx_command():
    """Get the fdsx command invoking the CLI module directly."""
    return [sys.executable, "-m", "fdsx.cli.main"]


class TestBatchCLIE2E:
    """End-to-end CLI tests for batch task execution (T070)."""

    def test_batch_full_execution_with_approval(self):
        """Test fdsx run --tasks via CLI: approve → all tasks execute → exit 0 + JSON."""
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
        json_text = result.output.split("\n\n")[0]
        output = json.loads(json_text)
        assert isinstance(output, list)
        assert len(output) == 3
        for item in output:
            assert item["status"] == "completed"
            assert "thread_id" in item
            assert item["error"] is None

    def test_input_tasks_mutual_exclusion(self):
        """Test --input and --tasks together → exit code 2, mutual exclusion error."""
        flow_path = str(Path("tests/fixtures/batch_flow.yaml").resolve())
        tasks_path = str(Path("tests/fixtures/sample_tasks.md").resolve())

        result = subprocess.run(
            get_fdsx_command()
            + [
                "run",
                flow_path,
                "--input",
                "task=foo",
                "--tasks",
                tasks_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 2, (
            f"Expected exit code 2, got {result.returncode}. stderr: {result.stderr}"
        )
        assert "mutually exclusive" in result.stderr.lower()

    def test_batch_missing_task_splitter(self):
        """Test --tasks with flow missing task_splitter → exit code 2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_path = Path(tmpdir) / "no_splitter_flow.yaml"
            flow_path.write_text(
                "name: No Splitter Flow\n"
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

            result = subprocess.run(
                get_fdsx_command()
                + [
                    "run",
                    str(flow_path),
                    "--tasks",
                    str(tasks_path),
                ],
                input="y\n",
                capture_output=True,
                text=True,
                timeout=30,
                cwd=tmpdir,
            )

            assert result.returncode == 2, (
                f"Expected exit code 2, got {result.returncode}. stderr: {result.stderr}"
            )
            assert "task_splitter" in result.stderr.lower()

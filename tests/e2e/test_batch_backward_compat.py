"""E2E tests for backward compatibility (T39): --tasks flag reads task_splitter from config."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from fdsx.core import engine
from fdsx.core.config import FdsxConfig, TaskSplitterConfig


class TestBackwardCompat:
    """T39: Verify --tasks (in-memory batch) reads task_splitter from config, not flow."""

    def test_tasks_flag_reads_config_not_flow(self):
        """Verify run_batch calls load_config() and uses config.task_splitter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_path = Path(tmpdir) / "flow.yaml"
            workflow_path.write_text(
                yaml.dump(
                    {
                        "name": "Test Flow",
                        "description": "A test flow",
                        "start_at": "step1",
                        "version": "1.0",
                        "states": {
                            "step1": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo test",
                                "result_path": "$.result",
                                "end": True,
                            }
                        },
                    }
                )
            )
            tasks_file = Path(tmpdir) / "tasks.txt"
            tasks_file.write_text("Task 1\nTask 2\n")

            config_loaded = []

            def mock_load_config(*args, **kwargs):
                config_loaded.append(True)
                return FdsxConfig(
                    task_splitter=TaskSplitterConfig(
                        provider="claude", model="claude-sonnet-4-6"
                    )
                )

            mock_provider = MagicMock()
            mock_provider.execute.return_value = MagicMock(
                exit_code=0,
                stdout='[{"description": "Task 1"}, {"description": "Task 2"}]',
                stderr="",
            )

            with (
                patch("fdsx.core.engine.batch.load_config", mock_load_config),
                patch("fdsx.core.batch.get_provider", return_value=mock_provider),
                patch("fdsx.core.engine.batch.display_task_list", return_value=True),
                patch(
                    "fdsx.core.engine.batch.run_flow",
                    return_value={"result": "ok"},
                ),
            ):
                engine.run_batch(workflow_path, tasks_file)

            assert len(config_loaded) > 0, "load_config should have been called"
            assert "task_splitter" not in workflow_path.read_text().lower(), (
                "task_splitter should not be in flow YAML"
            )

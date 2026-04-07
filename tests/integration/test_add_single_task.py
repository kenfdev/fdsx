from unittest.mock import MagicMock, patch

import structlog.testing
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core.batch import TASKS_DIR, split_tasks_to_groups, write_task_files
from fdsx.core.config import FdsxConfig, TaskSplitterConfig
from fdsx.models.task import TaskEntry, load_task_file


class TestSingleTaskIntegration:
    def test_single_task_one_group_one_entry(self, tmp_path):
        """Single task result produces exactly one file."""
        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Fix login bug"}]]',
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            groups = split_tasks_to_groups(
                "Fix the login bug", task_splitter, single_task=True
            )

        assert len(groups) == 1
        assert len(groups[0]) == 1
        assert groups[0][0].description == "Fix login bug"

        tasks_dir = tmp_path / TASKS_DIR
        created_files = write_task_files(groups, tasks_dir)

        assert len(created_files) == 1
        assert "fix-login-bug" in created_files[0].name

    def test_single_task_three_groups_coalesced(self, tmp_path):
        """Multiple groups are coalesced into one entry with a warning."""
        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Task A"}], [{"description": "Task B"}], [{"description": "Task C"}]]',
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            structlog.testing.capture_logs() as log_output,
        ):
            groups = split_tasks_to_groups(
                "Multi-part task", task_splitter, single_task=True
            )

        assert len(groups) == 1
        assert len(groups[0]) == 1
        assert "Task A" in groups[0][0].description
        assert "Task B" in groups[0][0].description
        assert "Task C" in groups[0][0].description
        assert "\n\n" in groups[0][0].description

        assert any(r.get("event") == "splitter_over_split" for r in log_output)

        tasks_dir = tmp_path / TASKS_DIR
        created_files = write_task_files(groups, tasks_dir)

        assert len(created_files) == 1

    def test_single_task_multiple_entries_in_one_group_coalesced(self, tmp_path):
        """Multiple entries in one group are coalesced with a warning."""
        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "First task"}, {"description": "Second task"}]]',
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            structlog.testing.capture_logs() as log_output,
        ):
            groups = split_tasks_to_groups("Two tasks", task_splitter, single_task=True)

        assert len(groups) == 1
        assert len(groups[0]) == 1
        assert "First task" in groups[0][0].description
        assert "Second task" in groups[0][0].description

        assert any(r.get("event") == "splitter_over_split" for r in log_output)

    def test_single_task_no_coalescing_when_already_single(self, tmp_path):
        """No warning when result is already one group with one entry."""
        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Only task"}]]',
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            structlog.testing.capture_logs() as log_output,
        ):
            groups = split_tasks_to_groups(
                "Single task", task_splitter, single_task=True
            )

        assert len(groups) == 1
        assert len(groups[0]) == 1
        assert groups[0][0].description == "Only task"

        assert not any(r.get("event") == "splitter_over_split" for r in log_output)

    def test_single_task_file_round_trips(self, tmp_path):
        """Task file content round-trips correctly through save/load."""
        groups = [
            [TaskEntry(description="Implement feature\n1. Step one\n2. Step two")]
        ]

        tasks_dir = tmp_path / TASKS_DIR
        created_files = write_task_files(groups, tasks_dir)

        assert len(created_files) == 1

        loaded = load_task_file(created_files[0])
        assert len(loaded.entries) == 1
        assert (
            loaded.entries[0].description
            == "Implement feature\n1. Step one\n2. Step two"
        )


class TestAddSingleTaskCli:
    def test_add_single_task_cli(self, tmp_path, monkeypatch):
        """fdsx add without --split creates exactly one task file."""
        task_file = tmp_path / "fix.md"
        task_file.write_text("Fix the login bug")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Fix login"}]]',
            stderr="",
        )

        def mock_load_config(*args, **kwargs):
            return FdsxConfig(task_splitter=TaskSplitterConfig())

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir(exist_ok=True)

        with (
            patch("fdsx.cli.main.load_config", side_effect=mock_load_config),
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["add", str(task_file)], catch_exceptions=False)

        assert result.exit_code == 0
        assert result.stdout == ""
        assert "Created 1 task file" in result.stderr

        tasks_dir = tmp_path / TASKS_DIR
        yaml_files = list(tasks_dir.glob("*.yaml"))
        assert len(yaml_files) == 1

    def test_add_single_task_cli_with_split_false_default(self, tmp_path, monkeypatch):
        """fdsx add without --split flag produces single-task behavior."""
        task_file = tmp_path / "task.md"
        task_file.write_text("Implement login feature")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Login feature"}]]',
            stderr="",
        )

        def mock_load_config(*args, **kwargs):
            return FdsxConfig(task_splitter=TaskSplitterConfig())

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir(exist_ok=True)

        with (
            patch("fdsx.cli.main.load_config", side_effect=mock_load_config),
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["add", str(task_file)], catch_exceptions=False)

        assert result.exit_code == 0

        tasks_dir = tmp_path / TASKS_DIR
        yaml_files = list(tasks_dir.glob("*.yaml"))
        assert len(yaml_files) == 1

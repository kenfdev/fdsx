import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from fdsx.core.batch import TASKS_DIR, split_tasks_to_groups, write_task_files
from fdsx.core.config import FdsxConfig, TaskSplitterConfig
from fdsx.models.task import TaskEntry


class TestSplitIntegration:
    def test_split_end_to_end_with_mock_provider(self):
        """End-to-end test: split task content via mock provider and write files."""
        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Implement feature A"}, {"description": "Implement feature B"}], [{"description": "Write tests for feature A"}], [{"description": "Write tests for feature B"}]]',
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            result_groups = split_tasks_to_groups(
                "Implement features A and B",
                task_splitter,
            )

        assert len(result_groups) == 3
        assert len(result_groups[0]) == 2
        assert len(result_groups[1]) == 1
        assert len(result_groups[2]) == 1

        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir) / TASKS_DIR
            created_files = write_task_files(result_groups, tasks_dir)

            assert len(created_files) == 3
            assert (tasks_dir / "001-implement-feature-a.yaml").exists()
            assert (tasks_dir / "002-write-tests-for-feature-a.yaml").exists()
            assert (tasks_dir / "003-write-tests-for-feature-b.yaml").exists()

            for f in created_files:
                assert f.exists()
                assert f.stat().st_mode & 0o777 == 0o600

            content = (tasks_dir / "001-implement-feature-a.yaml").read_text()
            data = yaml.safe_load(content)
            assert len(data["tasks"]) == 2
            assert data["tasks"][0]["description"] == "Implement feature A"

    def test_split_with_empty_result(self):
        """Test handling of empty result from provider."""
        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="[]",
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            groups = split_tasks_to_groups("some content", task_splitter)

        assert groups == []

    def test_split_with_single_task(self):
        """Test handling of single task result."""
        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Single task"}]]',
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            groups = split_tasks_to_groups("single task", task_splitter)

        assert len(groups) == 1
        assert len(groups[0]) == 1
        assert groups[0][0].description == "Single task"

        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir) / TASKS_DIR
            created_files = write_task_files(groups, tasks_dir)

            assert len(created_files) == 1
            content = (tasks_dir / "001-single-task.yaml").read_text()
            data = yaml.safe_load(content)
            assert "description" in data
            assert data["description"] == "Single task"

    def test_split_writes_yaml_with_task_status(self):
        """Verify that written YAML files contain correct status field."""
        groups = [
            [TaskEntry(description="Task with default status")],
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir) / TASKS_DIR
            write_task_files(groups, tasks_dir)

            content = (tasks_dir / "001-task-with-default-status.yaml").read_text()
            data = yaml.safe_load(content)

            assert "status" in data
            assert data["status"] == "pending"


class TestSplitCliIntegration:
    def test_split_command_missing_task_splitter_config_uses_defaults(
        self, tmp_path, monkeypatch
    ):
        """Test split command falls back to built-in defaults when task_splitter is not configured."""
        from typer.testing import CliRunner
        from fdsx.cli.main import app

        task_file = tmp_path / "tasks.md"
        task_file.write_text("Task 1\nTask 2")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Task 1"}], [{"description": "Task 2"}]]',
            stderr="",
        )

        monkeypatch.chdir(tmp_path)

        # Config has no task_splitter — should fall back to built-in TaskSplitterConfig()
        with patch(
            "fdsx.cli.main.load_config", return_value=FdsxConfig(task_splitter=None)
        ):
            with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
                runner = CliRunner()
                result = runner.invoke(app, ["split", str(task_file)])

        assert result.exit_code == 0
        import json as _json

        paths = _json.loads(result.stdout)
        assert isinstance(paths, list)
        assert len(paths) == 2
        assert "Created 2 task file" in result.stderr

    def test_split_command_missing_task_file(self, tmp_path):
        """Test split command fails when task file doesn't exist."""
        from typer.testing import CliRunner
        from fdsx.cli.main import app

        with patch(
            "fdsx.cli.main.load_config",
            return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["split", str(tmp_path / "nonexistent.md")])

            assert result.exit_code == 2
            assert "not found" in result.stderr

    def test_split_command_non_empty_dir_without_force(self, tmp_path, monkeypatch):
        """Test split command fails when tasks dir is non-empty and --force is not used."""
        from typer.testing import CliRunner
        from fdsx.cli.main import app

        tasks_dir = tmp_path / TASKS_DIR
        tasks_dir.mkdir(parents=True, exist_ok=True)
        existing_file = tasks_dir / "existing.yaml"
        existing_file.write_text("existing: true")

        task_file = tmp_path / "tasks.md"
        task_file.write_text("Task 1")

        def mock_load_config(*args, **kwargs):
            return FdsxConfig(task_splitter=TaskSplitterConfig())

        monkeypatch.chdir(tmp_path)

        with patch("fdsx.cli.main.load_config", side_effect=mock_load_config):
            runner = CliRunner()
            result = runner.invoke(
                app, ["split", str(task_file)], catch_exceptions=False
            )

            assert result.exit_code == 2
            assert "not empty" in result.stderr

    def test_split_command_with_force_flag(self, tmp_path, monkeypatch):
        """Test split command clears existing dir with --force flag."""
        from typer.testing import CliRunner
        from fdsx.cli.main import app

        tasks_dir = tmp_path / TASKS_DIR
        tasks_dir.mkdir(parents=True, exist_ok=True)
        existing_file = tasks_dir / "existing.yaml"
        existing_file.write_text("existing: true")

        task_file = tmp_path / "tasks.md"
        task_file.write_text("Task 1")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Task 1"}]]',
            stderr="",
        )

        def mock_load_config(*args, **kwargs):
            return FdsxConfig(task_splitter=TaskSplitterConfig())

        monkeypatch.chdir(tmp_path)

        with patch("fdsx.cli.main.load_config", side_effect=mock_load_config):
            with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
                runner = CliRunner()
                result = runner.invoke(
                    app, ["split", str(task_file), "--force"], catch_exceptions=False
                )

        assert result.exit_code == 0
        assert not existing_file.exists()

    def test_split_command_force_refuses_symlinked_tasks_dir(
        self, tmp_path, monkeypatch
    ):
        """Test --force refuses to delete tasks directory when it is a symlink."""
        from typer.testing import CliRunner
        from fdsx.cli.main import app

        real_dir = tmp_path / "real_tasks"
        real_dir.mkdir()
        tasks_parent = tmp_path / ".fdsx"
        tasks_parent.mkdir()
        symlink_dir = tasks_parent / "tasks"
        symlink_dir.symlink_to(real_dir)

        # Place a file in the symlinked dir so the non-empty check triggers
        (symlink_dir / "existing.yaml").write_text("existing: true")

        task_file = tmp_path / "tasks.md"
        task_file.write_text("Task 1")

        monkeypatch.chdir(tmp_path)

        with patch(
            "fdsx.cli.main.load_config",
            return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app, ["split", str(task_file), "--force"], catch_exceptions=False
            )

        assert result.exit_code == 2
        assert "symlink" in result.stderr

    def test_split_command_force_preserves_non_yaml_files(self, tmp_path, monkeypatch):
        """Test --force only deletes .yaml files, preserving other files."""
        from typer.testing import CliRunner
        from fdsx.cli.main import app

        tasks_dir = tmp_path / TASKS_DIR
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (tasks_dir / "existing.yaml").write_text("old: true")
        (tasks_dir / "notes.txt").write_text("user notes")
        (tasks_dir / "readme.md").write_text("# Notes")

        task_file = tmp_path / "tasks.md"
        task_file.write_text("Task 1")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Task 1"}]]',
            stderr="",
        )

        def mock_load_config(*args, **kwargs):
            return FdsxConfig(task_splitter=TaskSplitterConfig())

        monkeypatch.chdir(tmp_path)

        with patch("fdsx.cli.main.load_config", side_effect=mock_load_config):
            with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
                runner = CliRunner()
                result = runner.invoke(
                    app, ["split", str(task_file), "--force"], catch_exceptions=False
                )

        assert result.exit_code == 0
        assert not (tasks_dir / "existing.yaml").exists()
        assert (tasks_dir / "notes.txt").exists()
        assert (tasks_dir / "readme.md").exists()

    def test_split_command_success(self, tmp_path, monkeypatch):
        """Test successful split command execution."""
        from typer.testing import CliRunner
        from fdsx.cli.main import app

        task_file = tmp_path / "tasks.md"
        task_file.write_text("Implement feature A\nImplement feature B")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Implement feature A"}, {"description": "Implement feature B"}]]',
            stderr="",
        )

        def mock_load_config(*args, **kwargs):
            return FdsxConfig(task_splitter=TaskSplitterConfig())

        monkeypatch.chdir(tmp_path)

        with patch("fdsx.cli.main.load_config", side_effect=mock_load_config):
            with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
                runner = CliRunner()
                result = runner.invoke(
                    app, ["split", str(task_file)], catch_exceptions=False
                )

        assert result.exit_code == 0
        import json as _json

        paths = _json.loads(result.stdout)
        assert isinstance(paths, list)
        assert len(paths) == 1
        assert "Created 1 task file" in result.stderr

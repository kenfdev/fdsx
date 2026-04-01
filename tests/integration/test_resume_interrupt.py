from unittest.mock import MagicMock, patch

import yaml

from fdsx.cli.main import app
from fdsx.models.task import TaskEntry, TaskFile, save_task_file


class TestResumeCommandOnError:
    """Integration tests for resume command display on errors."""

    def test_runtime_error_displays_resume_command(self, tmp_path, monkeypatch):
        """RuntimeError from engine displays resume command with correct thread_id."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        workflow_yaml = yaml.dump(
            {
                "name": "Test",
                "start_at": "s",
                "states": {
                    "s": {
                        "type": "task",
                        "provider": "system",
                        "command": "echo done",
                        "result_path": "$.result",
                        "end": True,
                    }
                },
            }
        )
        workflow_path = tmp_path / "test.yaml"
        workflow_path.write_text(workflow_yaml)

        mock_run_flow = MagicMock(side_effect=RuntimeError("Test error"))

        with patch("fdsx.core.engine.run_flow", mock_run_flow):
            runner = __import__("typer.testing", fromlist=["CliRunner"]).CliRunner()
            result = runner.invoke(app, ["run", str(workflow_path)])

        assert result.exit_code == 1
        assert "Error: Test error" in result.stderr
        assert "fdsx resume --thread-id" in result.stderr
        assert "To resume this flow, run:" in result.stderr

    def test_exception_displays_resume_command(self, tmp_path, monkeypatch):
        """Generic Exception from engine displays resume command."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        workflow_yaml = yaml.dump(
            {
                "name": "Test",
                "start_at": "s",
                "states": {
                    "s": {
                        "type": "task",
                        "provider": "system",
                        "command": "echo done",
                        "result_path": "$.result",
                        "end": True,
                    }
                },
            }
        )
        workflow_path = tmp_path / "test.yaml"
        workflow_path.write_text(workflow_yaml)

        mock_run_flow = MagicMock(side_effect=ValueError("Test value error"))

        with patch("fdsx.core.engine.run_flow", mock_run_flow):
            runner = __import__("typer.testing", fromlist=["CliRunner"]).CliRunner()
            result = runner.invoke(app, ["run", str(workflow_path)])

        assert result.exit_code == 1
        assert "Error: Test value error" in result.stderr
        assert "fdsx resume --thread-id" in result.stderr


class TestResumeCommandOnKeyboardInterrupt:
    """Integration tests for resume command display on KeyboardInterrupt."""

    def test_keyboard_interrupt_displays_resume_command(self, tmp_path, monkeypatch):
        """KeyboardInterrupt from engine displays resume command."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        workflow_yaml = yaml.dump(
            {
                "name": "Test",
                "start_at": "s",
                "states": {
                    "s": {
                        "type": "task",
                        "provider": "system",
                        "command": "echo done",
                        "result_path": "$.result",
                        "end": True,
                    }
                },
            }
        )
        workflow_path = tmp_path / "test.yaml"
        workflow_path.write_text(workflow_yaml)

        mock_run_flow = MagicMock(side_effect=KeyboardInterrupt())

        with patch("fdsx.core.engine.run_flow", mock_run_flow):
            runner = __import__("typer.testing", fromlist=["CliRunner"]).CliRunner()
            result = runner.invoke(app, ["run", str(workflow_path)])

        assert result.exit_code == 130
        assert "fdsx resume --thread-id" in result.stderr
        assert "To resume this flow, run:" in result.stderr

    def test_keyboard_interrupt_in_tasks_dir_mode(self, tmp_path, monkeypatch):
        """KeyboardInterrupt in tasks-dir mode displays run command, not resume."""
        monkeypatch.chdir(tmp_path)

        workflows_dir = tmp_path / ".fdsx" / "workflows"
        workflows_dir.mkdir(parents=True)

        workflow_yaml = yaml.dump(
            {
                "name": "Test",
                "start_at": "s",
                "states": {
                    "s": {
                        "type": "task",
                        "provider": "system",
                        "command": "echo done",
                        "result_path": "$.result",
                        "end": True,
                    }
                },
            }
        )
        (workflows_dir / "test.yaml").write_text(workflow_yaml)

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test task")])
        save_task_file(tasks_dir / "001-task.yaml", tf)

        mock_run_tasks_dir = MagicMock(side_effect=KeyboardInterrupt())

        with patch("fdsx.core.engine.run_tasks_dir", mock_run_tasks_dir):
            runner = __import__("typer.testing", fromlist=["CliRunner"]).CliRunner()
            result = runner.invoke(app, ["run", "--tasks-dir", str(tasks_dir)])

        assert result.exit_code == 130
        assert "fdsx run --tasks-dir" in result.stderr
        assert "To continue processing, run:" in result.stderr


class TestResumeCommandTasksDirMode:
    """Integration tests for resume command in tasks-dir mode (shows run command)."""

    def test_runtime_error_in_tasks_dir_displays_run_command(
        self, tmp_path, monkeypatch
    ):
        """RuntimeError in tasks-dir mode displays fdsx run --tasks-dir."""
        monkeypatch.chdir(tmp_path)

        workflows_dir = tmp_path / ".fdsx" / "workflows"
        workflows_dir.mkdir(parents=True)

        workflow_yaml = yaml.dump(
            {
                "name": "Test",
                "start_at": "s",
                "states": {
                    "s": {
                        "type": "task",
                        "provider": "system",
                        "command": "echo done",
                        "result_path": "$.result",
                        "end": True,
                    }
                },
            }
        )
        (workflows_dir / "test.yaml").write_text(workflow_yaml)

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test task")])
        save_task_file(tasks_dir / "001-task.yaml", tf)

        mock_run_tasks_dir = MagicMock(side_effect=RuntimeError("Tasks dir error"))

        with patch("fdsx.core.engine.run_tasks_dir", mock_run_tasks_dir):
            runner = __import__("typer.testing", fromlist=["CliRunner"]).CliRunner()
            result = runner.invoke(app, ["run", "--tasks-dir", str(tasks_dir)])

        assert result.exit_code == 1
        assert "Error: Tasks dir error" in result.stderr
        assert f"fdsx run --tasks-dir {tasks_dir}" in result.stderr
        assert "To continue processing, run:" in result.stderr

    def test_tasks_dir_command_shows_correct_path(self, tmp_path, monkeypatch):
        """The displayed run command contains the correct tasks-dir path."""
        monkeypatch.chdir(tmp_path)

        workflows_dir = tmp_path / ".fdsx" / "workflows"
        workflows_dir.mkdir(parents=True)

        workflow_yaml = yaml.dump(
            {
                "name": "Test",
                "start_at": "s",
                "states": {
                    "s": {
                        "type": "task",
                        "provider": "system",
                        "command": "echo done",
                        "result_path": "$.result",
                        "end": True,
                    }
                },
            }
        )
        (workflows_dir / "test.yaml").write_text(workflow_yaml)

        tasks_dir = tmp_path / "my-tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test")])
        save_task_file(tasks_dir / "001-task.yaml", tf)

        mock_run_tasks_dir = MagicMock(side_effect=RuntimeError("Error"))

        with patch("fdsx.core.engine.run_tasks_dir", mock_run_tasks_dir):
            runner = __import__("typer.testing", fromlist=["CliRunner"]).CliRunner()
            result = runner.invoke(app, ["run", "--tasks-dir", str(tasks_dir)])

        assert result.exit_code == 1
        assert f"--tasks-dir {tasks_dir}" in result.stderr


class TestResumeCommandWithThreadId:
    """Integration tests for resume command with explicit thread_id."""

    def test_explicit_thread_id_in_resume_command(self, tmp_path, monkeypatch):
        """When --thread-id is provided, it appears in the resume command."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        workflow_yaml = yaml.dump(
            {
                "name": "Test",
                "start_at": "s",
                "states": {
                    "s": {
                        "type": "task",
                        "provider": "system",
                        "command": "echo done",
                        "result_path": "$.result",
                        "end": True,
                    }
                },
            }
        )
        workflow_path = tmp_path / "test.yaml"
        workflow_path.write_text(workflow_yaml)

        mock_run_flow = MagicMock(side_effect=RuntimeError("Test error"))

        with patch("fdsx.core.engine.run_flow", mock_run_flow):
            runner = __import__("typer.testing", fromlist=["CliRunner"]).CliRunner()
            result = runner.invoke(
                app, ["run", "--thread-id", "my-custom-thread", str(workflow_path)]
            )

        assert result.exit_code == 1
        assert "--thread-id my-custom-thread" in result.stderr
        assert "fdsx resume --thread-id my-custom-thread" in result.stderr

from unittest.mock import MagicMock, patch

import yaml

from fdsx.cli.main import app
from fdsx.core import engine
from fdsx.core.config import FdsxConfig, TaskSplitterConfig
from fdsx.models.task import TaskEntry, TaskFile, save_task_file


class _MockSpinner:
    """Test double for Spinner that records start and update calls."""

    _started_messages: list[str] = []
    _update_messages: list[str] = []

    def __init__(self, message: str = ""):
        self._message = message

    def __enter__(self) -> "_MockSpinner":
        _MockSpinner._started_messages.append(self._message)
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def update(self, message: str) -> None:
        _MockSpinner._update_messages.append(message)

    @classmethod
    def reset(cls) -> None:
        cls._started_messages = []
        cls._update_messages = []


class TestSplitSpinner:
    """Tests for spinner during fdsx split command."""

    def test_split_spinner_messages(self, tmp_path, monkeypatch):
        """Spinner messages appear during split: splitting + writing + completion."""
        from typer.testing import CliRunner

        task_file = tmp_path / "tasks.md"
        task_file.write_text("Task 1\nTask 2\nTask 3")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Task 1"}], [{"description": "Task 2"}], [{"description": "Task 3"}]]',
            stderr="",
        )

        monkeypatch.chdir(tmp_path)

        with patch(
            "fdsx.cli.main.load_config",
            return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
        ):
            with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
                runner = CliRunner()
                result = runner.invoke(app, ["split", str(task_file)])

        assert result.exit_code == 0, f"stderr: {result.stderr}"
        assert "Splitting tasks..." in result.stderr
        assert "Writing 3 task file(s)..." in result.stderr

    def test_split_empty_result_no_spinner_crash(self, tmp_path, monkeypatch):
        """Empty result from provider — spinner stops cleanly without crash."""
        from typer.testing import CliRunner

        task_file = tmp_path / "tasks.md"
        task_file.write_text("Nothing to split")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="[]",
            stderr="",
        )

        monkeypatch.chdir(tmp_path)

        with patch(
            "fdsx.cli.main.load_config",
            return_value=FdsxConfig(task_splitter=TaskSplitterConfig()),
        ):
            with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
                runner = CliRunner()
                result = runner.invoke(app, ["split", str(task_file)])

        assert result.exit_code == 0
        assert "No tasks were generated" in result.stderr


class TestAutoSelectionSpinner:
    """Tests for spinner during workflow auto-selection in run_tasks_dir."""

    def test_auto_selection_progress_messages(self, tmp_path):
        """Spinner shows progress during auto-selection of workflows."""
        project_root = tmp_path
        workflows_dir = project_root / ".fdsx" / "workflows"
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
        for i in range(3):
            tf = TaskFile(entries=[TaskEntry(description=f"task {i}")])
            save_task_file(tasks_dir / f"{i:03d}-task.yaml", tf)

        resolve_count = [0]

        def mock_resolve(**kwargs):
            resolve_count[0] += 1
            return workflows_dir / "test.yaml"

        with patch(
            "fdsx.core.engine.resolve_workflow_for_task", side_effect=mock_resolve
        ):
            with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    engine.run_tasks_dir(
                        None,
                        tasks_dir,
                        base_dir=project_root / ".fdsx",
                        auto_workflow=True,
                    )

        assert resolve_count[0] == 3

    def test_auto_selection_spinner_message_count(self, tmp_path):
        """Spinner update called with correct per-entry progress."""
        project_root = tmp_path
        workflows_dir = project_root / ".fdsx" / "workflows"
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
        for i in range(2):
            tf = TaskFile(entries=[TaskEntry(description=f"task {i}")])
            save_task_file(tasks_dir / f"{i:03d}-task.yaml", tf)

        _MockSpinner.reset()

        with patch("fdsx.core.engine.Spinner", side_effect=_MockSpinner):
            with patch(
                "fdsx.core.engine.resolve_workflow_for_task",
                return_value=workflows_dir / "test.yaml",
            ):
                with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
                    with patch("fdsx.core.engine.display_tasks_dir_summary"):
                        engine.run_tasks_dir(
                            None,
                            tasks_dir,
                            base_dir=project_root / ".fdsx",
                            auto_workflow=True,
                        )

        assert len(_MockSpinner._update_messages) == 2
        assert (
            "Auto-selecting workflows for 2 tasks..."
            in _MockSpinner._update_messages[0]
        )
        assert "(1/2)" in _MockSpinner._update_messages[0]
        assert "(2/2)" in _MockSpinner._update_messages[1]

    def test_no_spinner_when_all_entries_have_workflow(self, tmp_path):
        """No spinner when all entries already have workflow field set."""
        project_root = tmp_path
        workflows_dir = project_root / ".fdsx" / "workflows"
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
        tf = TaskFile(
            entries=[TaskEntry(description="task with workflow", workflow="test.yaml")]
        )
        save_task_file(tasks_dir / "001-task.yaml", tf)

        _MockSpinner.reset()

        with patch("fdsx.core.engine.Spinner", side_effect=_MockSpinner):
            with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    engine.run_tasks_dir(
                        None,
                        tasks_dir,
                        base_dir=project_root / ".fdsx",
                        auto_workflow=True,
                    )

        assert len(_MockSpinner._started_messages) == 0

    def test_no_spinner_when_workflow_path_provided(self, tmp_path):
        """No spinner when workflow_path argument is provided to run_tasks_dir."""
        project_root = tmp_path
        workflow_path = project_root / "batch_flow.yaml"
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
        workflow_path.write_text(workflow_yaml)

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="task without workflow")])
        save_task_file(tasks_dir / "001-task.yaml", tf)

        _MockSpinner.reset()

        with patch("fdsx.core.engine.Spinner", side_effect=_MockSpinner):
            with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    engine.run_tasks_dir(
                        workflow_path,
                        tasks_dir,
                        base_dir=project_root / ".fdsx",
                        auto_workflow=True,
                    )

        assert len(_MockSpinner._started_messages) == 0

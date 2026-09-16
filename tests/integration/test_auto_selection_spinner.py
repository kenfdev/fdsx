from typing import ClassVar
from unittest.mock import patch

import yaml

from fdsx.core import engine
from fdsx.core.engine import FlowResult
from fdsx.models.task import TaskEntry, TaskFile, save_task_file


class _MockSpinner:
    """Test double for Spinner that records start and update calls."""

    _started_messages: ClassVar[list[str]] = []
    _update_messages: ClassVar[list[str]] = []

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
                "description": "Test workflow",
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

        with (
            patch(
                "fdsx.core.selector.resolve_workflow_for_task",
                side_effect=mock_resolve,
            ),
            patch(
                "fdsx.core.engine.tasks_dir.run_flow",
                return_value=FlowResult(results={"result": "ok"}, status="completed"),
            ),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
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
                "description": "Test workflow",
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

        with (
            patch("fdsx.core.engine.tasks_dir.Spinner", side_effect=_MockSpinner),
            patch(
                "fdsx.core.selector.resolve_workflow_for_task",
                return_value=workflows_dir / "test.yaml",
            ),
            patch(
                "fdsx.core.engine.tasks_dir.run_flow",
                return_value=FlowResult(results={"result": "ok"}, status="completed"),
            ),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
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
                "description": "Test workflow",
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

        with (
            patch("fdsx.core.engine.tasks_dir.Spinner", side_effect=_MockSpinner),
            patch(
                "fdsx.core.engine.tasks_dir.run_flow",
                return_value=FlowResult(results={"result": "ok"}, status="completed"),
            ),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
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
                "description": "Test workflow",
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

        with (
            patch("fdsx.core.engine.tasks_dir.Spinner", side_effect=_MockSpinner),
            patch(
                "fdsx.core.engine.tasks_dir.run_flow",
                return_value=FlowResult(results={"result": "ok"}, status="completed"),
            ),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            engine.run_tasks_dir(
                workflow_path,
                tasks_dir,
                base_dir=project_root / ".fdsx",
                auto_workflow=True,
            )

        assert len(_MockSpinner._started_messages) == 0

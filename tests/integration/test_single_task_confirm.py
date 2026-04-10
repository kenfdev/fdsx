"""Tests for single-task confirmation bypass bug.

Tests that confirm_workflow_assignments_interactive shows a confirmation prompt
even when there is exactly 1 task entry that is already auto-assigned.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from fdsx.display.terminal import confirm_workflow_assignments_interactive
from fdsx.models.task import TaskEntry, TaskFile, save_task_file


class TestSingleTaskConfirmation:
    """Tests for single-task auto-selection confirmation behavior."""

    def test_single_task_auto_selected_shows_confirmation(self):
        """Single auto-selected task should still show confirmation prompt.

        When len(display_keys)==1 and all are assigned, the early return
        was bypassing the interactive confirmation loop. The "WORKFLOW
        ASSIGNMENTS" header should appear in output.
        """
        import io

        display_keys = [(0, 0)]
        workflow_assignments = {(0, 0): Path("/workflows/plan.yaml")}
        task_files = [
            (
                Path("/tasks/001-test.yaml"),
                TaskFile(entries=[TaskEntry(description="test task")]),
            )
        ]
        available_workflows = [
            (Path("/workflows/plan.yaml"), "Plan workflow", "plan"),
            (Path("/workflows/write.yaml"), "Write workflow", "write"),
        ]
        stream = io.StringIO()

        with (
            patch("fdsx.core.mode.is_interactive", return_value=True),
            patch("fdsx.display.terminal.input", return_value="c"),
        ):
            result = confirm_workflow_assignments_interactive(
                display_keys,
                workflow_assignments,
                task_files,
                available_workflows,
                stream=stream,
            )

        output = stream.getvalue()
        assert "WORKFLOW ASSIGNMENTS" in output, (
            "Confirmation header missing — early return bypassed the prompt"
        )
        assert result == {(0, 0): Path("/workflows/plan.yaml")}

    def test_auto_workflow_skips_confirmation(self):
        """auto_workflow=True should skip confirm_workflow_assignments_interactive."""
        from fdsx.core import engine

        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir) / "tasks"
            project_root = tmpdir

            import yaml

            workflows_dir = Path(project_root) / ".fdsx" / "workflows"
            workflows_dir.mkdir(parents=True)
            (workflows_dir / "plan.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Plan",
                        "description": "Planning workflow",
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
            )

            tasks_dir.mkdir()
            tf = TaskFile(entries=[TaskEntry(description="test task")])
            save_task_file(tasks_dir / "001-test.yaml", tf)

            with (
                patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value={"result": "ok"},
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch(
                    "fdsx.display.terminal.confirm_workflow_assignments_interactive"
                ) as mock_cui,
            ):
                engine.run_tasks_dir(
                    None,
                    tasks_dir,
                    base_dir=Path(project_root) / ".fdsx",
                    auto_workflow=True,
                )

            mock_cui.assert_not_called()

    def test_multi_task_shows_confirmation(self):
        """Multiple tasks should show confirmation prompt (baseline test)."""
        import io

        display_keys = [(0, 0), (0, 1)]
        workflow_assignments = {
            (0, 0): Path("/workflows/plan.yaml"),
            (0, 1): Path("/workflows/plan.yaml"),
        }
        task_files = [
            (
                Path("/tasks/001-test.yaml"),
                TaskFile(
                    entries=[
                        TaskEntry(description="task 1"),
                        TaskEntry(description="task 2"),
                    ]
                ),
            )
        ]
        available_workflows = [
            (Path("/workflows/plan.yaml"), "Plan workflow", "plan"),
        ]
        stream = io.StringIO()

        with (
            patch("fdsx.core.mode.is_interactive", return_value=True),
            patch("fdsx.display.terminal.input", return_value="c"),
        ):
            result = confirm_workflow_assignments_interactive(
                display_keys,
                workflow_assignments,
                task_files,
                available_workflows,
                stream=stream,
            )

        output = stream.getvalue()
        assert "WORKFLOW ASSIGNMENTS" in output
        assert result == workflow_assignments

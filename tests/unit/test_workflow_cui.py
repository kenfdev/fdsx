from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.display.terminal import confirm_workflow_assignments_interactive
from fdsx.models.task import TaskEntry, TaskFile


class TestConfirmWorkflowAssignmentsInteractive:
    """Tests for the interactive workflow confirmation CUI."""

    def _make_task_files(self, *descriptions: str) -> list[tuple[Path, TaskFile]]:
        """Helper to create task_files list from descriptions."""
        return [
            (
                Path(f"00{i}-task.yaml"),
                TaskFile(entries=[TaskEntry(description=desc)]),
            )
            for i, desc in enumerate(descriptions, start=1)
        ]

    def _make_workflows(self, *names: str) -> list[tuple[Path, str]]:
        """Helper to create available_workflows list from names."""
        return [(Path(name), f"Description for {name}") for name in names]

    def test_confirm_returns_assignments_dict(self):
        """'c' input returns the assignments dict."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        wf1 = Path("review.yaml")
        wf2 = Path("implement.yaml")
        assignments = {
            (0, 0): wf1,
            (1, 0): wf2,
        }
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml", "implement.yaml")

        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", return_value="c"):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        assert result is not None
        assert result == assignments
        assert result[(0, 0)] == wf1
        assert result[(1, 0)] == wf2

    def test_cancel_returns_none(self):
        """'q' input returns None (uses 2 tasks since single assigned auto-confirms)."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        assignments = {
            (0, 0): Path("review.yaml"),
            (1, 0): Path("implement.yaml"),
        }
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml", "implement.yaml")

        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", return_value="q"):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        assert result is None

    def test_non_tty_auto_confirm_returns_copy(self):
        """When is_interactive() is False, returns immediately without calling input."""
        task_files = self._make_task_files("Fix the bug")
        assignments = {(0, 0): Path("review.yaml")}
        display_keys = [(0, 0)]
        workflows = self._make_workflows("review.yaml")

        with patch("fdsx.display.terminal.is_interactive", return_value=False):
            with patch("builtins.input") as mock_input:
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        mock_input.assert_not_called()
        assert result is not None
        assert result == assignments
        assert result is not assignments

    def test_change_workflow_by_number(self):
        """Entering a number changes that assignment and confirm returns updated dict (uses 2 tasks)."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        original_wf = Path("review.yaml")
        new_wf = Path("implement.yaml")
        assignments = {(0, 0): original_wf, (1, 0): original_wf}
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml", "implement.yaml")

        input_seq = ["1", "2", "c"]
        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=input_seq):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        assert result is not None
        assert result[(0, 0)] == new_wf

    def test_change_workflow_cancel_sub_pick_returns_to_main(self):
        """Cancelling workflow sub-pick ('c') returns to main prompt (uses 2 tasks)."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        assignments = {(0, 0): Path("review.yaml"), (1, 0): Path("review.yaml")}
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml", "implement.yaml")

        input_seq = ["1", "c", "c"]
        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=input_seq):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        assert result is not None
        assert result[(0, 0)] == Path("review.yaml")

    def test_invalid_number_reprompts(self):
        """Invalid row number prints error and re-prompts (uses 2 tasks)."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        assignments = {(0, 0): Path("review.yaml"), (1, 0): Path("review.yaml")}
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml")
        stream = StringIO()

        input_seq = ["0", "99", "abc", "c"]
        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=input_seq):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows, stream=stream
                )

        assert result is not None
        assert "Invalid number" in stream.getvalue()

    def test_invalid_workflow_pick_reprompts(self):
        """Invalid workflow pick number prints error and re-prompts (uses 2 tasks)."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        assignments = {(0, 0): Path("review.yaml"), (1, 0): Path("review.yaml")}
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml", "implement.yaml")
        stream = StringIO()

        input_seq = ["1", "99", "c"]
        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=input_seq):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows, stream=stream
                )

        assert result is not None
        assert "Invalid number" in stream.getvalue()

    def test_empty_workflows_no_change_possible(self):
        """When no alternative workflows, prints message and returns to main (uses 2 tasks)."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        assignments = {(0, 0): Path("review.yaml"), (1, 0): Path("review.yaml")}
        display_keys = [(0, 0), (1, 0)]
        workflows: list[tuple[Path, str]] = []
        stream = StringIO()

        input_seq = ["1", "c"]
        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=input_seq):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows, stream=stream
                )

        assert result is not None
        assert "No alternative workflows" in stream.getvalue()
        assert result[(0, 0)] == Path("review.yaml")

    def test_unassigned_entry_blocks_confirm(self):
        """'c' with unassigned entries is rejected until all are assigned."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        wf1 = Path("review.yaml")
        wf2 = Path("implement.yaml")
        assignments = {
            (0, 0): wf1,
        }
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml", "implement.yaml")
        stream = StringIO()

        input_seq = ["c", "2", "2", "c"]
        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=input_seq):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows, stream=stream
                )

        assert result is not None
        stderr_text = stream.getvalue()
        assert "Cannot confirm" in stderr_text
        assert "no workflow assigned" in stderr_text
        assert result[(1, 0)] == wf2

    def test_table_shows_correct_columns(self):
        """Table output contains expected columns and task info (uses 2 tasks)."""
        task_files = self._make_task_files("Fix the login bug", "Write tests")
        assignments = {
            (0, 0): Path("review.yaml"),
            (1, 0): Path("implement.yaml"),
        }
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml", "implement.yaml")
        stream = StringIO()

        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", return_value="c"):
                confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows, stream=stream
                )

        stderr_text = stream.getvalue()
        assert "WORKFLOW ASSIGNMENTS" in stderr_text
        assert "FILE" in stderr_text
        assert "ENTRY" in stderr_text
        assert "WORKFLOW" in stderr_text
        assert "TASK" in stderr_text
        assert "review.yaml" in stderr_text
        assert "Fix the login" in stderr_text
        assert "task.yaml" in stderr_text

    def test_confirm_does_not_persist_while_in_memory(self):
        """The function returns a dict; caller is responsible for persistence."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        wf1 = Path("review.yaml")
        wf2 = Path("implement.yaml")
        assignments = {(0, 0): wf1, (1, 0): wf1}
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml", "implement.yaml")

        input_seq = ["1", "2", "c"]
        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=input_seq):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        assert result[(0, 0)] == wf2
        assert assignments[(0, 0)] == wf1

    def test_returns_none_preserves_original_assignments(self):
        """Cancel returns None; original assignments dict is unchanged (uses 2 tasks)."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        wf = Path("review.yaml")
        assignments = {(0, 0): wf, (1, 0): wf}
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml")

        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", return_value="q"):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        assert result is None
        assert assignments[(0, 0)] == wf

    def test_sanitizes_ansi_from_description(self):
        """ANSI escape sequences in task description are stripped from output (uses 2 tasks)."""
        task_files = [
            (
                Path("001-task.yaml"),
                TaskFile(
                    entries=[TaskEntry(description="\x1b[31mFix the bug\x1b[0m danger")]
                ),
            ),
            (
                Path("002-task.yaml"),
                TaskFile(entries=[TaskEntry(description="Write tests")]),
            ),
        ]
        assignments = {(0, 0): Path("review.yaml"), (1, 0): Path("review.yaml")}
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml")
        stream = StringIO()

        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", return_value="c"):
                confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows, stream=stream
                )

        stderr_text = stream.getvalue()
        assert "\x1b" not in stderr_text
        assert "Fix the bug" in stderr_text

    def test_workflow_change_persists_through_multiple_changes(self):
        """Changing the same assignment twice keeps the last value (uses 2 tasks)."""
        task_files = self._make_task_files("Fix the bug", "Write tests")
        wf1 = Path("review.yaml")
        wf2 = Path("implement.yaml")
        wf3 = Path("test.yaml")
        assignments = {(0, 0): wf1, (1, 0): wf1}
        display_keys = [(0, 0), (1, 0)]
        workflows = self._make_workflows("review.yaml", "implement.yaml", "test.yaml")

        input_seq = ["1", "2", "1", "3", "c"]
        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", side_effect=input_seq):
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        assert result is not None
        assert result[(0, 0)] == wf3

    def test_single_task_auto_confirm_no_input_called(self):
        """A single assigned task auto-confirms without calling input (T028 edge case)."""
        task_files = self._make_task_files("Fix the bug")
        wf = Path("review.yaml")
        assignments = {(0, 0): wf}
        display_keys = [(0, 0)]
        workflows = self._make_workflows("review.yaml")

        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input") as mock_input:
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        mock_input.assert_not_called()
        assert result is not None
        assert result == assignments

    def test_single_unassigned_task_shows_cui(self):
        """A single unassigned task still requires CUI interaction."""
        task_files = self._make_task_files("Fix the bug")
        display_keys = [(0, 0)]
        assignments: dict[tuple[int, int], Path] = {}
        workflows = self._make_workflows("review.yaml")

        with patch("fdsx.display.terminal.is_interactive", return_value=True):
            with patch("builtins.input", return_value="c") as mock_input:
                result = confirm_workflow_assignments_interactive(
                    display_keys, assignments, task_files, workflows
                )

        mock_input.assert_called()
        assert result is None, (
            "Unassigned single task should block on 'c' until assigned"
        )

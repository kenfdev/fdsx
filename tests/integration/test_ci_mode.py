"""Integration tests for the mode module (T005-T007) and CI mode guards (T010-T013)."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from fdsx.core.engine.tasks_dir import run_tasks_dir
from fdsx.core.mode import get_interactive_mode, is_interactive, set_interactive_mode
from fdsx.core.selector import confirm_workflow_selection, pick_workflow_manually
from fdsx.display.terminal import display_wait_prompt
from fdsx.models.task import TaskEntry, TaskFile, save_task_file
from tests import FIXTURES_DIR


class TestMode:
    """Tests for the mode module functions."""

    def test_set_interactive_mode_true(self):
        """set_interactive_mode(True) makes is_interactive() return True."""
        set_interactive_mode(True)
        try:
            assert is_interactive() is True
        finally:
            set_interactive_mode(None)

    def test_set_interactive_mode_false(self):
        """set_interactive_mode(False) makes is_interactive() return False."""
        set_interactive_mode(False)
        try:
            assert is_interactive() is False
        finally:
            set_interactive_mode(None)

    def test_is_interactive_falls_back_to_stdin_tty(self):
        """When mode is None, is_interactive() falls back to sys.stdin.isatty()."""
        set_interactive_mode(None)
        try:
            with patch("sys.stdin.isatty", return_value=True):
                assert is_interactive() is True
            with patch("sys.stdin.isatty", return_value=False):
                assert is_interactive() is False
        finally:
            set_interactive_mode(None)

    def test_get_interactive_mode_returns_raw_value(self):
        """get_interactive_mode() returns the raw mode value."""
        set_interactive_mode(True)
        try:
            assert get_interactive_mode() is True
        finally:
            set_interactive_mode(None)

        set_interactive_mode(False)
        try:
            assert get_interactive_mode() is False
        finally:
            set_interactive_mode(None)

        set_interactive_mode(None)
        assert get_interactive_mode() is None


class TestCIModeGuards:
    """Tests for CI mode input guards (T010)."""

    def test_wait_state_auto_selects_first_choice_in_ci(self):
        """display_wait_prompt returns first choice without calling input() in CI mode."""
        set_interactive_mode(False)
        try:
            with patch("builtins.input") as mock_input:
                result = display_wait_prompt("test_state", "test message", ["a", "b"])
            assert result == "a"
            mock_input.assert_not_called()
        finally:
            set_interactive_mode(None)

    def test_selector_auto_approves_in_ci(self):
        """confirm_workflow_selection returns True without calling input() in CI mode."""
        set_interactive_mode(False)
        try:
            with patch("builtins.input") as mock_input:
                result = confirm_workflow_selection(
                    Path("test.yaml"), "test task", "Test Workflow"
                )
            assert result is True
            mock_input.assert_not_called()
        finally:
            set_interactive_mode(None)

    def test_selector_auto_picks_first_workflow_in_ci(self):
        """pick_workflow_manually returns first workflow without calling input() in CI mode."""
        set_interactive_mode(False)
        try:
            workflows = [
                (Path("wf1.yaml"), "description 1", "Workflow 1"),
                (Path("wf2.yaml"), "description 2", "Workflow 2"),
            ]
            with patch("builtins.input") as mock_input:
                result = pick_workflow_manually(workflows)
            assert result == Path("wf1.yaml")
            mock_input.assert_not_called()
        finally:
            set_interactive_mode(None)

    def test_tasks_dir_fail_fast_in_ci(self):
        """In CI mode without continue_on_error, tasks dir fails fast on error."""
        set_interactive_mode(False)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tasks_dir = Path(tmpdir)
                flow_path = FIXTURES_DIR / "batch_flow.yaml"

                tf = TaskFile(entries=[TaskEntry(description="task A")])
                save_task_file(tasks_dir / "001-a.yaml", tf)

                error_count = [0]

                def mock_run_flow_err(*args, **kwargs):
                    error_count[0] += 1
                    raise RuntimeError("intentional error")

                with (
                    patch(
                        "fdsx.core.engine.tasks_dir.run_flow",
                        side_effect=mock_run_flow_err,
                    ),
                    patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                    patch("builtins.input") as mock_input,
                ):
                    results = run_tasks_dir(
                        flow_path,
                        tasks_dir,
                        auto_workflow=True,
                        continue_on_error=False,
                    )

                assert len(results) == 1
                assert results[0]["status"] == "failed"
                mock_input.assert_not_called()
        finally:
            set_interactive_mode(None)

    def test_tasks_dir_continue_on_error_in_ci(self):
        """In CI mode with continue_on_error=True, tasks dir continues after error."""
        set_interactive_mode(False)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tasks_dir = Path(tmpdir)
                flow_path = FIXTURES_DIR / "batch_flow.yaml"

                tf1 = TaskFile(entries=[TaskEntry(description="task A")])
                save_task_file(tasks_dir / "001-a.yaml", tf1)
                tf2 = TaskFile(entries=[TaskEntry(description="task B")])
                save_task_file(tasks_dir / "002-b.yaml", tf2)

                run_count = [0]

                def mock_run_flow_err(*args, **kwargs):
                    run_count[0] += 1
                    if run_count[0] == 1:
                        raise RuntimeError("intentional error")
                    return {"result": "ok"}

                with (
                    patch(
                        "fdsx.core.engine.tasks_dir.run_flow",
                        side_effect=mock_run_flow_err,
                    ),
                    patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                    patch("builtins.input") as mock_input,
                ):
                    results = run_tasks_dir(
                        flow_path, tasks_dir, auto_workflow=True, continue_on_error=True
                    )

                assert len(results) == 2
                assert results[0]["status"] == "failed"
                assert results[1]["status"] == "completed"
                mock_input.assert_not_called()
        finally:
            set_interactive_mode(None)

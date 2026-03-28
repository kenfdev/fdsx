"""Integration tests for workflow assignment persistence (T017, T018)."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from fdsx.core import engine
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file
from tests import FIXTURES_DIR


class TestWorkflowPersistence:
    """Tests for workflow persistence to task YAML files."""

    def test_confirm_persists_workflow_to_yaml(self):
        """Confirming in CUI persists workflow field to task YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="Fix the bug")])
            save_task_file(tasks_dir / "001-test.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value={"result": "ok"},
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch("fdsx.display.terminal.is_interactive", return_value=False),
            ):
                results = engine.run_tasks_dir(
                    flow_path, tasks_dir, auto_workflow=False
                )

            # After all entries complete, the file is moved to completed/
            loaded = load_task_file(tasks_dir / "completed" / "001-test.yaml")
            assert loaded.entries[0].workflow is not None
            assert results[0]["status"] == "completed"

    def test_cancel_does_not_persist_workflow(self):
        """Cancelling in CUI does not modify the workflow field in YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="Fix the bug")])
            save_task_file(tasks_dir / "001-test.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value={"result": "ok"},
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch("fdsx.display.terminal.is_interactive", return_value=False),
                patch(
                    "fdsx.display.terminal.confirm_workflow_assignments_interactive",
                    return_value=None,
                ),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=False)

            loaded = load_task_file(tasks_dir / "001-test.yaml")
            assert loaded.entries[0].workflow is None

    def test_pre_existing_workflow_skips_auto_selection(self):
        """Tasks with workflow already set skip auto-selection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[TaskEntry(description="Fix the bug", workflow="review.yaml")]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            resolve_calls = []

            def mock_resolve(
                task_description, workflows_dir, selector_config, auto_workflow
            ):
                resolve_calls.append(
                    {
                        "desc": task_description,
                        "auto": auto_workflow,
                    }
                )
                return workflows_dir / "review.yaml"

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value={"result": "ok"},
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch(
                    "fdsx.core.selector.resolve_workflow_for_task",
                    side_effect=mock_resolve,
                ),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert len(resolve_calls) == 0, (
                "resolve_workflow_for_task should not be called when "
                "entry.workflow is already set"
            )

    def test_rerun_with_persisted_workflow_skips_selector(self):
        """Re-running after workflow is persisted skips the selector."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[TaskEntry(description="Fix the bug", workflow="review.yaml")]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            selector_called = []

            def track_selector(*args, **kwargs):
                selector_called.append(True)
                return Path("review.yaml")

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value={"result": "ok"},
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch(
                    "fdsx.core.selector.resolve_workflow_for_task",
                    side_effect=track_selector,
                ),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert len(selector_called) == 0, (
                "Selector should not be called when workflow is already persisted"
            )

            # After all entries complete, the file is moved to completed/
            loaded = load_task_file(tasks_dir / "completed" / "001-test.yaml")
            assert loaded.entries[0].workflow == "review.yaml"
            assert loaded.entries[0].status == "completed"

    def test_non_tty_auto_confirm_persists_workflow(self):
        """Non-TTY mode auto-confirms and persists workflow to YAML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="Fix the bug")])
            save_task_file(tasks_dir / "001-test.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value={"result": "ok"},
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch("fdsx.display.terminal.is_interactive", return_value=False),
                patch(
                    "fdsx.display.terminal.confirm_workflow_assignments_interactive"
                ) as mock_cui,
            ):
                mock_cui.return_value = {(0, 0): Path("review.yaml")}
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=False)

            mock_cui.assert_called_once()
            # After all entries complete, the file is moved to completed/
            loaded = load_task_file(tasks_dir / "completed" / "001-test.yaml")
            assert loaded.entries[0].workflow == "review.yaml"

    def test_workflow_persists_with_correct_filename_format(self):
        """Persisted workflow is stored as filename only (not full path)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="Fix the bug")])
            save_task_file(tasks_dir / "001-test.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value={"result": "ok"},
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch("fdsx.display.terminal.is_interactive", return_value=False),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=False)

            # After all entries complete, the file is moved to completed/
            loaded = load_task_file(tasks_dir / "completed" / "001-test.yaml")
            wf = loaded.entries[0].workflow
            assert wf is not None
            assert "/" not in wf
            assert "\\" not in wf
            assert wf == Path(wf).name

    def test_multiple_entries_all_persisted(self):
        """All workflow assignments are persisted for multi-entry files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(description="Task A"),
                    TaskEntry(description="Task B"),
                    TaskEntry(description="Task C"),
                ]
            )
            save_task_file(tasks_dir / "001-multi.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value={"result": "ok"},
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch("fdsx.display.terminal.is_interactive", return_value=False),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=False)

            loaded = load_task_file(tasks_dir / "completed" / "001-multi.yaml")
            for entry in loaded.entries:
                assert entry.workflow is not None
                assert entry.status == "completed"

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core import engine
from fdsx.core.engine import FlowResult
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file
from tests import FIXTURES_DIR


class TestTasksDirLoader:
    def test_load_tasks_dir_returns_sorted_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "b-task.yaml").write_text("description: b task\n")
            (tasks_dir / "a-task.yaml").write_text("description: a task\n")
            (tasks_dir / "c-task.yaml").write_text("description: c task\n")

            files = engine.load_tasks_dir(tasks_dir)

            assert len(files) == 3
            names = [f.name for f, _ in files]
            assert names == ["a-task.yaml", "b-task.yaml", "c-task.yaml"]

    def test_load_tasks_dir_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            with pytest.raises(ValueError, match=r"No .yaml or .yml files"):
                engine.load_tasks_dir(tasks_dir)

    def test_load_tasks_dir_nonexistent_dir(self):
        with pytest.raises(FileNotFoundError):
            engine.load_tasks_dir(Path("/nonexistent/path"))

    def test_load_tasks_dir_parses_task_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            task_file = tasks_dir / "001-test.yaml"
            task_file.write_text("description: Test task\n")

            files = engine.load_tasks_dir(tasks_dir)

            assert len(files) == 1
            file_path, parsed = files[0]
            assert file_path == task_file
            assert len(parsed.entries) == 1
            assert parsed.entries[0].description == "Test task"

    def test_load_tasks_dir_rejects_symlinked_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = Path(tmpdir) / "real"
            real_dir.mkdir()
            (real_dir / "001-task.yaml").write_text("description: task\n")

            symlink_dir = Path(tmpdir) / "link"
            symlink_dir.symlink_to(real_dir)

            with pytest.raises(ValueError, match="must not be a symlink"):
                engine.load_tasks_dir(symlink_dir)

    def test_load_tasks_dir_rejects_symlinked_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            real_file = tasks_dir / "001-real.yaml"
            real_file.write_text("description: real task\n")

            symlink_file = tasks_dir / "002-link.yaml"
            symlink_file.symlink_to(real_file)

            with pytest.raises(ValueError, match="must be a regular file"):
                engine.load_tasks_dir(tasks_dir)

    def test_load_tasks_dir_ignores_subdirectory_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "direct-task.yaml").write_text("description: direct task\n")
            subdir = tasks_dir / "subdir"
            subdir.mkdir()
            (subdir / "nested-task.yaml").write_text("description: nested task\n")

            files = engine.load_tasks_dir(tasks_dir)

            assert len(files) == 1
            assert files[0][0].name == "direct-task.yaml"

    def test_load_tasks_dir_discovers_yml_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "a-task.yml").write_text("description: yml task\n")
            (tasks_dir / "b-task.yml").write_text("description: another yml task\n")

            files = engine.load_tasks_dir(tasks_dir)

            assert len(files) == 2
            names = [f.name for f, _ in files]
            assert names == ["a-task.yml", "b-task.yml"]

    def test_load_tasks_dir_discovers_mixed_yaml_and_yml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "b-task.yaml").write_text("description: yaml task\n")
            (tasks_dir / "a-task.yml").write_text("description: yml task\n")
            (tasks_dir / "c-task.yaml").write_text("description: another yaml task\n")

            files = engine.load_tasks_dir(tasks_dir)

            assert len(files) == 3
            names = [f.name for f, _ in files]
            assert names == ["a-task.yml", "b-task.yaml", "c-task.yaml"]

    def test_load_tasks_dir_ignores_arbitrary_subdirectory_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            (tasks_dir / "001-task.yaml").write_text("description: task\n")
            for subdir_name in ["_parked", "done", "archive", "backup", "nested"]:
                subdir = tasks_dir / subdir_name
                subdir.mkdir()
                (subdir / "task.yaml").write_text("description: should be ignored\n")

            files = engine.load_tasks_dir(tasks_dir)

            assert len(files) == 1
            assert files[0][0].name == "001-task.yaml"

    def test_load_tasks_dir_empty_dir_with_only_subdirectory_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            subdir = tasks_dir / "some_subdir"
            subdir.mkdir()
            (subdir / "task.yaml").write_text("description: hidden task\n")

            with pytest.raises(ValueError, match=r"No .yaml or .yml files"):
                engine.load_tasks_dir(tasks_dir)


class TestFilterActionableEntries:
    def test_skips_completed(self):
        task_file = TaskFile(
            entries=[
                TaskEntry(description="task1", status="completed"),
                TaskEntry(description="task2", status="pending"),
            ]
        )
        result = engine._filter_actionable_entries(task_file)
        assert len(result) == 1
        assert result[0][1].description == "task2"

    def test_includes_failed(self):
        task_file = TaskFile(
            entries=[
                TaskEntry(description="task1", status="failed"),
            ]
        )
        result = engine._filter_actionable_entries(task_file)
        assert len(result) == 1
        assert result[0][1].description == "task1"

    def test_includes_running(self):
        task_file = TaskFile(
            entries=[
                TaskEntry(description="task1", status="running"),
            ]
        )
        result = engine._filter_actionable_entries(task_file)
        assert len(result) == 1
        assert result[0][1].description == "task1"

    def test_skips_all_completed(self):
        task_file = TaskFile(
            entries=[
                TaskEntry(description="task1", status="completed"),
                TaskEntry(description="task2", status="completed"),
            ]
        )
        result = engine._filter_actionable_entries(task_file)
        assert len(result) == 0


class TestUpdateTaskStatus:
    def test_updates_and_persists_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            task_file = TaskFile(
                entries=[
                    TaskEntry(description="test task", status="pending"),
                ]
            )
            file_path = tasks_dir / "task.yaml"
            save_task_file(file_path, task_file)

            engine._update_task_status(
                file_path, task_file, 0, "running", thread_id="test-thread-123"
            )

            loaded = load_task_file(file_path)
            assert loaded.entries[0].status == "running"
            assert loaded.entries[0].thread_id == "test-thread-123"

            engine._update_task_status(
                file_path, task_file, 0, "completed", thread_id="test-thread-123"
            )

            loaded = load_task_file(file_path)
            assert loaded.entries[0].status == "completed"

    def test_updates_error_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            task_file = TaskFile(
                entries=[
                    TaskEntry(description="test task", status="pending"),
                ]
            )
            file_path = tasks_dir / "task.yaml"
            save_task_file(file_path, task_file)

            engine._update_task_status(
                file_path, task_file, 0, "failed", error="Something went wrong"
            )

            loaded = load_task_file(file_path)
            assert loaded.entries[0].status == "failed"
            assert loaded.entries[0].error == "Something went wrong"


class TestRunTasksDir:
    def test_full_run_all_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf1 = TaskFile(entries=[TaskEntry(description="task A")])
            save_task_file(tasks_dir / "001-a.yaml", tf1)

            tf2 = TaskFile(entries=[TaskEntry(description="task B")])
            save_task_file(tasks_dir / "002-b.yaml", tf2)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value=FlowResult(
                        results={"result": "ok"}, status="completed"
                    ),
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert len(results) == 2
            for r in results:
                assert r["status"] == "completed"
                assert r["category"] == "new"

            # Files are moved to completed/ once all entries finish
            loaded_a = load_task_file(tasks_dir / "completed" / "001-a.yaml")
            assert loaded_a.entries[0].status == "completed"

            loaded_b = load_task_file(tasks_dir / "completed" / "002-b.yaml")
            assert loaded_b.entries[0].status == "completed"

    def test_skips_completed_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(
                        description="task already done",
                        status="completed",
                        thread_id="old-thread",
                    ),
                    TaskEntry(description="task to run", status="pending"),
                ]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            run_count = [0]

            def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
                run_count[0] += 1
                return FlowResult(results={"result": "ok"}, status="completed")

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert run_count[0] == 1
            skipped = [r for r in results if r["category"] == "skipped"]
            assert len(skipped) == 1
            assert skipped[0]["entry_description"] == "task already done"
            executed = [r for r in results if r["category"] == "new"]
            assert len(executed) == 1
            assert executed[0]["entry_description"] == "task to run"

    def test_retries_failed_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(
                        description="failed task",
                        status="failed",
                        error="previous error",
                    ),
                ]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value=FlowResult(
                        results={"result": "ok"}, status="completed"
                    ),
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert len(results) == 1
            assert results[0]["status"] == "completed"
            assert results[0]["category"] == "retried"

            # File is moved to completed/ once all entries finish
            loaded = load_task_file(tasks_dir / "completed" / "001-test.yaml")
            assert loaded.entries[0].status == "completed"
            assert loaded.entries[0].error is None

    def test_retries_running_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(
                        description="interrupted task",
                        status="running",
                        thread_id="old-thread",
                    ),
                ]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value=FlowResult(
                        results={"result": "ok"}, status="completed"
                    ),
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert len(results) == 1
            assert results[0]["status"] == "completed"
            assert results[0]["category"] == "retried"

    def test_multi_task_file_per_entry_tracking(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(description="task 1"),
                    TaskEntry(description="task 2"),
                    TaskEntry(description="task 3"),
                ]
            )
            save_task_file(tasks_dir / "001-multi.yaml", tf)

            call_count = [0]

            def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
                call_count[0] += 1
                return FlowResult(results={"result": "ok"}, status="completed")

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert call_count[0] == 3
            assert len(results) == 3

            # File is moved to completed/ once all entries finish
            loaded = load_task_file(tasks_dir / "completed" / "001-multi.yaml")
            for entry in loaded.entries:
                assert entry.status == "completed"

    def test_continues_after_failure(self):
        from fdsx.core.mode import set_interactive_mode

        set_interactive_mode(True)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tasks_dir = Path(tmpdir)
                flow_path = FIXTURES_DIR / "batch_flow.yaml"

                tf = TaskFile(
                    entries=[
                        TaskEntry(description="task 1"),
                        TaskEntry(description="task 2"),
                    ]
                )
                save_task_file(tasks_dir / "001-test.yaml", tf)

                call_count = [0]

                def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
                    call_count[0] += 1
                    if call_count[0] == 1:
                        raise RuntimeError("Task 1 failed")
                    return FlowResult(results={"result": "ok"}, status="completed")

                with (
                    patch(
                        "fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow
                    ),
                    patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                    patch("fdsx.core.engine.tasks_dir.input", side_effect=["y"]),
                ):
                    results = engine.run_tasks_dir(
                        flow_path, tasks_dir, auto_workflow=True
                    )

                assert len(results) == 2
                assert results[0]["status"] == "failed"
                assert results[1]["status"] == "completed"

                loaded = load_task_file(tasks_dir / "001-test.yaml")
                assert loaded.entries[0].status == "failed"
                assert loaded.entries[1].status == "completed"
        finally:
            set_interactive_mode(None)

    def test_stops_on_user_n(self):
        from fdsx.core.mode import set_interactive_mode

        set_interactive_mode(True)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tasks_dir = Path(tmpdir)
                flow_path = FIXTURES_DIR / "batch_flow.yaml"

                tf = TaskFile(
                    entries=[
                        TaskEntry(description="task 1"),
                        TaskEntry(description="task 2"),
                    ]
                )
                save_task_file(tasks_dir / "001-test.yaml", tf)

                call_count = [0]

                def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
                    call_count[0] += 1
                    if call_count[0] == 1:
                        raise RuntimeError("Task 1 failed")
                    return FlowResult(results={"result": "ok"}, status="completed")

                with (
                    patch(
                        "fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow
                    ),
                    patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                    patch("fdsx.core.engine.tasks_dir.input", side_effect=["n"]),
                ):
                    results = engine.run_tasks_dir(
                        flow_path, tasks_dir, auto_workflow=True
                    )

                assert len(results) == 1
                assert results[0]["status"] == "failed"
        finally:
            set_interactive_mode(None)

    def test_skips_file_when_all_completed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(description="done", status="completed"),
                ]
            )
            save_task_file(tasks_dir / "001-done.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value=FlowResult(
                        results={"result": "ok"}, status="completed"
                    ),
                ) as mock_run,
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            mock_run.assert_not_called()
            assert len(results) == 1
            assert results[0]["entry_index"] == 0
            assert results[0]["entry_description"] == "done"
            assert results[0]["category"] == "skipped"

    def test_all_completed_multi_entry_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(description="task 1", status="completed"),
                    TaskEntry(description="task 2", status="completed"),
                ]
            )
            save_task_file(tasks_dir / "001-done.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value=FlowResult(
                        results={"result": "ok"}, status="completed"
                    ),
                ) as mock_run,
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            mock_run.assert_not_called()
            assert len(results) == 2
            for r in results:
                assert r["category"] == "skipped"
                assert r["status"] == "completed"
            assert results[0]["entry_index"] == 0
            assert results[1]["entry_index"] == 1

    def test_retried_task_that_fails_again(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(
                        description="will fail again",
                        status="failed",
                        error="previous error",
                    ),
                ]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
                raise RuntimeError("Failed again")

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch("fdsx.core.engine.tasks_dir.input", side_effect=["n"]),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert len(results) == 1
            assert results[0]["status"] == "failed"
            assert results[0]["category"] == "retried"

    def test_thread_id_preserved_after_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(description="task 1"),
                ]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
                raise RuntimeError("Task failed")

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch("fdsx.core.engine.tasks_dir.input", side_effect=["n"]),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            loaded = load_task_file(tasks_dir / "001-test.yaml")
            assert loaded.entries[0].thread_id is not None

    def test_aborted_workflow_marks_entry_failed_no_file_move(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="task 1")])
            save_task_file(tasks_dir / "001-test.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value=FlowResult(
                        results={}, status="aborted", abort_state="abort_blocked"
                    ),
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert len(results) == 1
            result = results[0]
            assert result["status"] == "failed"
            assert result["error"] == "workflow aborted at state 'abort_blocked'"

            # Task file must NOT be moved to completed/
            assert (tasks_dir / "001-test.yaml").exists()
            assert not (tasks_dir / "completed" / "001-test.yaml").exists()

            # Entry status persisted as failed
            loaded = load_task_file(tasks_dir / "001-test.yaml")
            assert loaded.entries[0].status == "failed"

    def test_aborted_workflow_summary_shows_failed_count(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="task 1")])
            save_task_file(tasks_dir / "001-test.yaml", tf)

            with patch(
                "fdsx.core.engine.tasks_dir.run_flow",
                return_value=FlowResult(
                    results={}, status="aborted", abort_state="abort_blocked"
                ),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            captured = capsys.readouterr()
            assert "Failed: 1" in captured.err
            assert "✗" in captured.err


class TestDisplayTasksDirSummary:
    def test_displays_summary_to_stderr(self, capsys):
        from fdsx.core.batch import display_tasks_dir_summary

        results = [
            {
                "file_index": 0,
                "file_name": "001-a.yaml",
                "entry_index": 0,
                "entry_description": "task a",
                "thread_id": "thread-1",
                "status": "completed",
                "error": None,
                "category": "new",
            },
            {
                "file_index": 0,
                "file_name": "001-a.yaml",
                "entry_index": 1,
                "entry_description": "task b",
                "thread_id": "thread-2",
                "status": "failed",
                "error": "oops",
                "category": "new",
            },
            {
                "file_index": 1,
                "file_name": "002-b.yaml",
                "entry_index": 0,
                "entry_description": "skipped task",
                "thread_id": "thread-3",
                "status": "completed",
                "error": None,
                "category": "skipped",
            },
        ]

        display_tasks_dir_summary(results)

        captured = capsys.readouterr()
        assert "TASKS-DIR EXECUTION SUMMARY" in captured.err
        assert "Total: 3" in captured.err
        assert "Skipped: 1" in captured.err
        assert "New: 2" in captured.err
        assert "Failed: 1" in captured.err
        assert "Completed:" not in captured.err


class TestTasksDirCli:
    def test_run_tasks_dir_cli_success(self, tmp_path):
        project_root = Path(__file__).resolve().parent.parent.parent
        workflow_path = project_root / "tests/fixtures/batch_flow.yaml"

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="cli task")])
        save_task_file(tasks_dir / "001-test.yaml", tf)

        with (
            patch(
                "fdsx.core.engine.tasks_dir.run_flow",
                return_value=FlowResult(results={"result": "ok"}, status="completed"),
            ),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "run",
                    str(workflow_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--auto-workflow",
                ],
            )

        assert result.exit_code == 0, (
            f"exit_code={result.exit_code}, stderr={result.stderr}"
        )

    def test_run_tasks_dir_exit_code_one_on_failed(self, tmp_path):
        project_root = Path(__file__).resolve().parent.parent.parent
        workflow_path = project_root / "tests/fixtures/batch_flow.yaml"

        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="cli task")])
        save_task_file(tasks_dir / "001-test.yaml", tf)

        failed_results = [
            {
                "file_index": 0,
                "file_name": "001-test.yaml",
                "entry_index": 0,
                "entry_description": "cli task",
                "thread_id": "thread-1",
                "status": "failed",
                "error": "something went wrong",
                "category": "new",
            },
        ]

        with (
            patch("fdsx.cli.main.engine.run_tasks_dir", return_value=failed_results),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "run",
                    str(workflow_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--auto-workflow",
                ],
            )

        assert result.exit_code == 1, (
            f"exit_code={result.exit_code}, output={result.output}"
        )

    def test_run_tasks_dir_exit_code_zero_on_success(self, tmp_path):
        project_root = Path(__file__).resolve().parent.parent.parent
        workflow_path = project_root / "tests/fixtures/batch_flow.yaml"

        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="cli task")])
        save_task_file(tasks_dir / "001-test.yaml", tf)

        success_results = [
            {
                "file_index": 0,
                "file_name": "001-test.yaml",
                "entry_index": 0,
                "entry_description": "cli task",
                "thread_id": "thread-1",
                "status": "completed",
                "error": None,
                "category": "new",
            },
        ]

        with (
            patch("fdsx.cli.main.engine.run_tasks_dir", return_value=success_results),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                [
                    "run",
                    str(workflow_path),
                    "--tasks-dir",
                    str(tasks_dir),
                    "--auto-workflow",
                ],
            )

        assert result.exit_code == 0, (
            f"exit_code={result.exit_code}, output={result.output}"
        )

    def test_run_tasks_dir_mutual_exclusion(self, tmp_path):
        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-test.yaml").write_text("description: dummy\n")

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run",
                "workflow.yaml",
                "--tasks-dir",
                str(tasks_dir),
                "--input",
                "foo=bar",
            ],
        )
        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr.lower()

    def test_run_tasks_dir_without_workflow_requires_auto_workflow(self, tmp_path):
        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-test.yaml").write_text("description: dummy\n")

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["run", "--tasks-dir", str(tasks_dir), "--auto-workflow"],
        )
        assert result.exit_code == 1
        assert "No workflows found" in result.stderr

    def test_run_tasks_dir_rejects_symlink_dir(self, tmp_path):
        (tmp_path / ".fdsx").mkdir()
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        save_task_file(
            real_dir / "001-test.yaml", TaskFile(entries=[TaskEntry(description="t")])
        )

        symlink_dir = tmp_path / "link"
        symlink_dir.symlink_to(real_dir)

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["run", "tests/fixtures/batch_flow.yaml", "--tasks-dir", str(symlink_dir)],
        )
        assert result.exit_code == 2
        assert "symlink" in result.stderr.lower()

    def test_auto_and_confirm_workflow_mutually_exclusive(self, tmp_path):
        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-test.yaml").write_text("description: dummy\n")

        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "run",
                "--tasks-dir",
                str(tasks_dir),
                "--auto-workflow",
                "--confirm-workflow",
            ],
        )
        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr.lower()


class TestMoveToCompletedOnRunTasksDir:
    """Tests for FR-3: files are moved to completed/ after all entries complete."""

    def test_completed_file_moved_to_completed_subdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="task A")])
            save_task_file(tasks_dir / "001-a.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value=FlowResult(
                        results={"result": "ok"}, status="completed"
                    ),
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert not (tasks_dir / "001-a.yaml").exists()
            assert (tasks_dir / "completed" / "001-a.yaml").exists()

    def test_failed_file_stays_in_tasks_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="task A")])
            save_task_file(tasks_dir / "001-a.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    side_effect=RuntimeError("fail"),
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch("fdsx.core.engine.tasks_dir.input", side_effect=["n"]),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            # File with failed entry must remain in tasks_dir
            assert (tasks_dir / "001-a.yaml").exists()
            assert not (tasks_dir / "completed" / "001-a.yaml").exists()

    def test_partial_completion_file_stays_in_tasks_dir(self):
        """File with mixed completed/failed entries must not be moved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[
                    TaskEntry(description="task 1"),
                    TaskEntry(description="task 2"),
                ]
            )
            save_task_file(tasks_dir / "001-mixed.yaml", tf)

            call_count = [0]

            def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("first fails")
                return FlowResult(results={"result": "ok"}, status="completed")

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch("fdsx.core.engine.tasks_dir.input", side_effect=["y"]),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert (tasks_dir / "001-mixed.yaml").exists()
            assert not (tasks_dir / "completed" / "001-mixed.yaml").exists()

    def test_pre_completed_file_moved_to_completed_subdir(self):
        """Files that were already fully completed (skipped) should also be moved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                entries=[TaskEntry(description="already done", status="completed")]
            )
            save_task_file(tasks_dir / "001-done.yaml", tf)

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow") as mock_run,
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            mock_run.assert_not_called()
            assert not (tasks_dir / "001-done.yaml").exists()
            assert (tasks_dir / "completed" / "001-done.yaml").exists()

    def test_move_failure_logs_warning_and_does_not_abort(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="task A")])
            save_task_file(tasks_dir / "001-a.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value=FlowResult(
                        results={"result": "ok"}, status="completed"
                    ),
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
                patch(
                    "fdsx.core.engine.tasks_dir.move_task_to_completed",
                    side_effect=OSError("disk full"),
                ),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            captured = capsys.readouterr()
            assert "Warning" in captured.err
            assert "001-a.yaml" in captured.err
            # Execution should still complete
            assert len(results) == 1
            assert results[0]["status"] == "completed"

    def test_collision_logs_warning(self, capsys):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            # Put a file in completed/ to trigger a collision
            completed_dir = tasks_dir / "completed"
            completed_dir.mkdir()
            (completed_dir / "001-a.yaml").write_text("collision\n")

            tf = TaskFile(entries=[TaskEntry(description="task A")])
            save_task_file(tasks_dir / "001-a.yaml", tf)

            with (
                patch(
                    "fdsx.core.engine.tasks_dir.run_flow",
                    return_value=FlowResult(
                        results={"result": "ok"}, status="completed"
                    ),
                ),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            captured = capsys.readouterr()
            assert "Warning" in captured.err
            # Original file not lost on collision
            assert (tasks_dir / "001-a.yaml").exists()
            assert len(results) == 1
            assert results[0]["status"] == "completed"


class TestBatchEditFlow:
    """Regression tests for the interactive workflow confirmation CUI (T013-T020).

    Tests the new CUI flow: confirm ('c') proceeds, cancel ('q') aborts.
    """

    def _make_workflow_yaml(self, name: str, description: str) -> str:
        import yaml

        return yaml.dump(
            {
                "name": name,
                "description": description,
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

    def test_confirm_proceeds(self, tmp_path):
        """CUI confirm ('c') — execution proceeds."""
        project_root = tmp_path
        workflows_dir = project_root / ".fdsx" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "plan.yaml").write_text(
            self._make_workflow_yaml("Plan", "Planning workflow")
        )

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="plan this feature")])
        save_task_file(tasks_dir / "001-test.yaml", tf)

        wf_path = workflows_dir / "plan.yaml"
        mock_assignments = {(0, 0): wf_path}

        with (
            patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
            patch(
                "fdsx.core.engine.tasks_dir.run_flow",
                return_value=FlowResult(results={"result": "ok"}, status="completed"),
            ),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            patch(
                "fdsx.display.terminal.confirm_workflow_assignments_interactive",
                return_value=mock_assignments,
            ),
        ):
            results = engine.run_tasks_dir(
                None,
                tasks_dir,
                base_dir=project_root / ".fdsx",
                auto_workflow=False,
            )

        assert len([r for r in results if r["category"] == "new"]) == 1

    def test_cancel_aborts(self, tmp_path):
        """CUI cancel ('q') — execution aborts with empty results."""
        project_root = tmp_path
        workflows_dir = project_root / ".fdsx" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "plan.yaml").write_text(
            self._make_workflow_yaml("Plan", "Planning workflow")
        )

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="do something")])
        save_task_file(tasks_dir / "001-test.yaml", tf)

        with (
            patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            patch(
                "fdsx.display.terminal.confirm_workflow_assignments_interactive",
                return_value=None,
            ),
        ):
            results = engine.run_tasks_dir(
                None,
                tasks_dir,
                base_dir=project_root / ".fdsx",
                auto_workflow=False,
            )

        assert results == []

    def test_auto_workflow_skips_cui(self, tmp_path):
        """auto_workflow=True skips the CUI entirely."""
        project_root = tmp_path
        workflows_dir = project_root / ".fdsx" / "workflows"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "plan.yaml").write_text(
            self._make_workflow_yaml("Plan", "Planning workflow")
        )

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="plan this feature")])
        save_task_file(tasks_dir / "001-test.yaml", tf)

        with (
            patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
            patch(
                "fdsx.core.engine.tasks_dir.run_flow",
                return_value=FlowResult(results={"result": "ok"}, status="completed"),
            ),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            patch(
                "fdsx.display.terminal.confirm_workflow_assignments_interactive"
            ) as mock_cui,
        ):
            results = engine.run_tasks_dir(
                None,
                tasks_dir,
                base_dir=project_root / ".fdsx",
                auto_workflow=True,
            )

        mock_cui.assert_not_called()
        assert len([r for r in results if r["category"] == "new"]) == 1


class TestRunTasksDirQuietFlagPropagation:
    def test_run_tasks_dir_passes_quiet_true_to_run_flow(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="task A")])
            save_task_file(tasks_dir / "001-a.yaml", tf)

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow") as mock_run_flow,
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                engine.run_tasks_dir(
                    flow_path, tasks_dir, auto_workflow=True, quiet=True
                )

            assert mock_run_flow.called
            for call_args in mock_run_flow.call_args_list:
                assert call_args.kwargs.get("quiet") is True

    def test_run_tasks_dir_passes_quiet_false_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="task A")])
            save_task_file(tasks_dir / "001-a.yaml", tf)

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow") as mock_run_flow,
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert mock_run_flow.called
            for call_args in mock_run_flow.call_args_list:
                assert call_args.kwargs.get("quiet") is False


class TestRunTasksDirSourceInjection:
    def test_source_injected_when_task_file_has_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(
                source="features.md",
                entries=[TaskEntry(description="task A")],
            )
            save_task_file(tasks_dir / "001-a.yaml", tf)

            captured_inputs: list[dict] = []

            def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
                captured_inputs.append(dict(inputs))
                return {"result": "ok"}

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert len(captured_inputs) == 1
            assert captured_inputs[0]["task"] == "task A"
            assert captured_inputs[0]["source"] == "features.md"

    def test_source_injected_as_empty_string_when_task_file_source_is_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = FIXTURES_DIR / "batch_flow.yaml"

            tf = TaskFile(entries=[TaskEntry(description="task B")])
            save_task_file(tasks_dir / "001-b.yaml", tf)

            captured_inputs: list[dict] = []

            def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
                captured_inputs.append(dict(inputs))
                return {"result": "ok"}

            with (
                patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow),
                patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            ):
                engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

            assert len(captured_inputs) == 1
            assert captured_inputs[0]["task"] == "task B"
            assert captured_inputs[0]["source"] == ""


# ---------------------------------------------------------------------------
# T017: TestWorkflowHooksPerTask — on_workflow_start/end fires once per task
# ---------------------------------------------------------------------------


class TestWorkflowHooksPerTask:
    """Verify that workflow lifecycle hooks fire exactly once per task in run_tasks_dir."""

    _FLOW_YAML = """
name: HookTasksFlow
description: Flow with workflow-level hooks for tasks-dir test
start_at: step1
hooks:
  on_workflow_start:
    - command: "echo wf_start"
  on_workflow_end:
    - command: "echo wf_end"
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.result"
    end: true
"""

    def test_workflow_hooks_fire_once_per_task(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """on_workflow_start and on_workflow_end each fire exactly once per task (3 tasks total)."""
        monkeypatch.chdir(tmp_path)
        from fdsx.providers.base import ProviderResult

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        for i in range(1, 4):
            (tasks_dir / f"00{i}-task.yaml").write_text(f"description: task {i}\n")

        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(self._FLOW_YAML)

        fake_result = ProviderResult(exit_code=0, stdout="done", stderr="")

        with (
            patch(
                "fdsx.core.engine.run.execute_workflow_hooks", create=True
            ) as mock_run_wh,
            patch(
                "fdsx.core.engine.resume.execute_workflow_hooks", create=True
            ) as mock_resume_wh,
            patch("fdsx.providers.system._run_subprocess", return_value=fake_result),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            engine.run_tasks_dir(
                flow_path,
                tasks_dir,
                base_dir=tmp_path / ".fdsx",
                auto_workflow=True,
            )

        # Collect all calls from both run and resume patches
        all_start_calls = [
            c
            for c in mock_run_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_start"
        ] + [
            c
            for c in mock_resume_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_start"
        ]
        all_end_calls = [
            c
            for c in mock_run_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_end"
        ] + [
            c
            for c in mock_resume_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_end"
        ]

        assert len(all_start_calls) == 3, (
            f"on_workflow_start should fire once per task (3 tasks), "
            f"got {len(all_start_calls)} calls"
        )
        assert len(all_end_calls) == 3, (
            f"on_workflow_end should fire once per task (3 tasks), "
            f"got {len(all_end_calls)} calls"
        )

        # Each task should run with a distinct thread_id
        start_thread_ids = {c.kwargs.get("thread_id") for c in all_start_calls}
        assert len(start_thread_ids) == 3, (
            f"Expected 3 distinct thread_ids for 3 tasks, got: {start_thread_ids}"
        )

        # Each on_workflow_start's thread_id should match a corresponding on_workflow_end
        end_thread_ids = {c.kwargs.get("thread_id") for c in all_end_calls}
        assert start_thread_ids == end_thread_ids, (
            f"thread_ids from on_workflow_start {start_thread_ids} must match "
            f"on_workflow_end {end_thread_ids}"
        )

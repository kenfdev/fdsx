import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core import engine
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file


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
            with pytest.raises(ValueError, match="No .yaml files"):
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
            flow_path = Path("tests/fixtures/batch_flow.yaml")

            tf1 = TaskFile(entries=[TaskEntry(description="task A")])
            save_task_file(tasks_dir / "001-a.yaml", tf1)

            tf2 = TaskFile(entries=[TaskEntry(description="task B")])
            save_task_file(tasks_dir / "002-b.yaml", tf2)

            with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    results = engine.run_tasks_dir(flow_path, tasks_dir)

            assert len(results) == 2
            for r in results:
                assert r["status"] == "completed"
                assert r["category"] == "new"

            loaded_a = load_task_file(tasks_dir / "001-a.yaml")
            assert loaded_a.entries[0].status == "completed"

            loaded_b = load_task_file(tasks_dir / "002-b.yaml")
            assert loaded_b.entries[0].status == "completed"

    def test_skips_completed_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = Path("tests/fixtures/batch_flow.yaml")

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

            def mock_run_flow(flow_path, inputs, thread_id, base_dir):
                run_count[0] += 1
                return {"result": "ok"}

            with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    results = engine.run_tasks_dir(flow_path, tasks_dir)

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
            flow_path = Path("tests/fixtures/batch_flow.yaml")

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

            with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    results = engine.run_tasks_dir(flow_path, tasks_dir)

            assert len(results) == 1
            assert results[0]["status"] == "completed"
            assert results[0]["category"] == "retried"

            loaded = load_task_file(tasks_dir / "001-test.yaml")
            assert loaded.entries[0].status == "completed"
            assert loaded.entries[0].error is None

    def test_retries_running_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = Path("tests/fixtures/batch_flow.yaml")

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

            with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    results = engine.run_tasks_dir(flow_path, tasks_dir)

            assert len(results) == 1
            assert results[0]["status"] == "completed"
            assert results[0]["category"] == "retried"

    def test_multi_task_file_per_entry_tracking(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = Path("tests/fixtures/batch_flow.yaml")

            tf = TaskFile(
                entries=[
                    TaskEntry(description="task 1"),
                    TaskEntry(description="task 2"),
                    TaskEntry(description="task 3"),
                ]
            )
            save_task_file(tasks_dir / "001-multi.yaml", tf)

            call_count = [0]

            def mock_run_flow(flow_path, inputs, thread_id, base_dir):
                call_count[0] += 1
                return {"result": "ok"}

            with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    results = engine.run_tasks_dir(flow_path, tasks_dir)

            assert call_count[0] == 3
            assert len(results) == 3

            loaded = load_task_file(tasks_dir / "001-multi.yaml")
            for entry in loaded.entries:
                assert entry.status == "completed"

    def test_continues_after_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = Path("tests/fixtures/batch_flow.yaml")

            tf = TaskFile(
                entries=[
                    TaskEntry(description="task 1"),
                    TaskEntry(description="task 2"),
                ]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            call_count = [0]

            def mock_run_flow(flow_path, inputs, thread_id, base_dir):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("Task 1 failed")
                return {"result": "ok"}

            with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    with patch("fdsx.core.engine.input", side_effect=["y", "y"]):
                        results = engine.run_tasks_dir(flow_path, tasks_dir)

            assert len(results) == 2
            assert results[0]["status"] == "failed"
            assert results[1]["status"] == "completed"

            loaded = load_task_file(tasks_dir / "001-test.yaml")
            assert loaded.entries[0].status == "failed"
            assert loaded.entries[1].status == "completed"

    def test_stops_on_user_n(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = Path("tests/fixtures/batch_flow.yaml")

            tf = TaskFile(
                entries=[
                    TaskEntry(description="task 1"),
                    TaskEntry(description="task 2"),
                ]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            call_count = [0]

            def mock_run_flow(flow_path, inputs, thread_id, base_dir):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("Task 1 failed")
                return {"result": "ok"}

            with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    with patch("fdsx.core.engine.input", side_effect=["n"]):
                        results = engine.run_tasks_dir(flow_path, tasks_dir)

            assert len(results) == 1
            assert results[0]["status"] == "failed"

    def test_skips_file_when_all_completed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = Path("tests/fixtures/batch_flow.yaml")

            tf = TaskFile(
                entries=[
                    TaskEntry(description="done", status="completed"),
                ]
            )
            save_task_file(tasks_dir / "001-done.yaml", tf)

            with patch(
                "fdsx.core.engine.run_flow", return_value={"result": "ok"}
            ) as mock_run:
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    results = engine.run_tasks_dir(flow_path, tasks_dir)

            mock_run.assert_not_called()
            assert len(results) == 1
            assert results[0]["entry_index"] == 0
            assert results[0]["entry_description"] == "done"
            assert results[0]["category"] == "skipped"

    def test_all_completed_multi_entry_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = Path("tests/fixtures/batch_flow.yaml")

            tf = TaskFile(
                entries=[
                    TaskEntry(description="task 1", status="completed"),
                    TaskEntry(description="task 2", status="completed"),
                ]
            )
            save_task_file(tasks_dir / "001-done.yaml", tf)

            with patch(
                "fdsx.core.engine.run_flow", return_value={"result": "ok"}
            ) as mock_run:
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    results = engine.run_tasks_dir(flow_path, tasks_dir)

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
            flow_path = Path("tests/fixtures/batch_flow.yaml")

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

            def mock_run_flow(flow_path, inputs, thread_id, base_dir):
                raise RuntimeError("Failed again")

            with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    with patch("fdsx.core.engine.input", side_effect=["n"]):
                        results = engine.run_tasks_dir(flow_path, tasks_dir)

            assert len(results) == 1
            assert results[0]["status"] == "failed"
            assert results[0]["category"] == "retried"

    def test_thread_id_preserved_after_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            flow_path = Path("tests/fixtures/batch_flow.yaml")

            tf = TaskFile(
                entries=[
                    TaskEntry(description="task 1"),
                ]
            )
            save_task_file(tasks_dir / "001-test.yaml", tf)

            def mock_run_flow(flow_path, inputs, thread_id, base_dir):
                raise RuntimeError("Task failed")

            with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
                with patch("fdsx.core.engine.display_tasks_dir_summary"):
                    with patch("fdsx.core.engine.input", side_effect=["n"]):
                        engine.run_tasks_dir(flow_path, tasks_dir)

            loaded = load_task_file(tasks_dir / "001-test.yaml")
            assert loaded.entries[0].thread_id is not None


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

        with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
            with patch("fdsx.core.engine.display_tasks_dir_summary"):
                runner = CliRunner()
                result = runner.invoke(
                    app,
                    [
                        "run",
                        str(workflow_path),
                        "--tasks-dir",
                        str(tasks_dir),
                    ],
                )

        assert result.exit_code == 0, (
            f"exit_code={result.exit_code}, stderr={result.stderr}"
        )

    def test_run_tasks_dir_mutual_exclusion(self, tmp_path):
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

    def test_run_tasks_dir_requires_workflow(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-test.yaml").write_text("description: dummy\n")

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["run", "--tasks-dir", str(tasks_dir)],
        )
        assert result.exit_code == 2
        assert "required" in result.stderr.lower()

    def test_run_tasks_dir_rejects_symlink_dir(self, tmp_path):
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

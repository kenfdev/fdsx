"""Integration tests for Phase 6: Polish, Backward Compatibility & E2E.

Tests:
- T39: Backward compatibility for --tasks flag
- T40: Clear error messages for invalid task files
- T42: Edge cases (empty dir, all completed, single task file, invalid YAML)
- T43: End-to-end full pipeline (split → edit → run → crash → resume → complete)
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core import engine
from fdsx.core.batch import TASKS_DIR, split_tasks_to_groups, write_task_files
from fdsx.core.config import FdsxConfig, TaskSplitterConfig
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file


class TestBackwardCompat:
    """T39: Verify --tasks (in-memory batch) reads task_splitter from config, not flow."""

    def test_tasks_flag_reads_config_not_flow(self):
        """Verify run_batch calls load_config() and uses config.task_splitter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            workflow_path = Path(tmpdir) / "flow.yaml"
            workflow_path.write_text(
                yaml.dump(
                    {
                        "name": "Test Flow",
                        "description": "A test flow",
                        "start_at": "step1",
                        "version": "1.0",
                        "states": {
                            "step1": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo test",
                                "result_path": "$.result",
                                "end": True,
                            }
                        },
                    }
                )
            )
            tasks_file = Path(tmpdir) / "tasks.txt"
            tasks_file.write_text("Task 1\nTask 2\n")

            config_loaded = []

            def mock_load_config(*args, **kwargs):
                config_loaded.append(True)
                return FdsxConfig(
                    task_splitter=TaskSplitterConfig(
                        provider="claude", model="claude-sonnet-4-6"
                    )
                )

            mock_provider = MagicMock()
            mock_provider.execute.return_value = MagicMock(
                exit_code=0,
                stdout='[{"description": "Task 1"}, {"description": "Task 2"}]',
                stderr="",
            )

            with patch("fdsx.core.engine.load_config", mock_load_config):
                with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
                    with patch("fdsx.core.engine.display_task_list", return_value=True):
                        with patch(
                            "fdsx.core.engine.run_flow", return_value={"result": "ok"}
                        ):
                            engine.run_batch(workflow_path, tasks_file)

            assert len(config_loaded) > 0, "load_config should have been called"
            assert "task_splitter" not in workflow_path.read_text().lower(), (
                "task_splitter should not be in flow YAML"
            )


class TestErrorMessages:
    """T40: Clear error messages for various failure scenarios."""

    def test_invalid_yaml_task_file_via_cli(self, tmp_path):
        """Invalid YAML in task file should produce a clear error via CLI."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-bad.yaml").write_text(": [broken yaml\n")

        workflow_path = Path("tests/fixtures/simple_flow.yaml")

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

        assert result.exit_code == 2, f"Expected exit code 2, got {result.exit_code}"
        assert "001-bad.yaml" in result.stderr or "invalid" in result.stderr.lower(), (
            f"Error should mention the invalid file: {result.stderr}"
        )

    def test_invalid_yaml_task_file_via_api(self, tmp_path):
        """Invalid YAML in task file should produce a clear error via API."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-bad.yaml").write_text(": [broken yaml\n")

        workflow_path = Path("tests/fixtures/simple_flow.yaml")

        with pytest.raises(engine.FlowValidationError) as exc_info:
            engine.run_tasks_dir(workflow_path, tasks_dir, auto_workflow=True)

        assert (
            "001-bad.yaml" in str(exc_info.value)
            or "invalid" in str(exc_info.value).lower()
        ), f"Error should mention the invalid file: {exc_info.value}"


class TestEdgeCases:
    """T42: Edge case handling."""

    def test_empty_tasks_dir_error_via_cli(self, tmp_path):
        """Empty tasks directory should produce a clear error via CLI."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        workflow_path = Path("tests/fixtures/simple_flow.yaml")

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

        assert result.exit_code == 2, f"Expected exit code 2, got {result.exit_code}"
        assert (
            "no .yaml" in result.stderr.lower() or "empty" in result.stderr.lower()
        ), f"Error should mention no YAML files: {result.stderr}"

    def test_all_tasks_completed_multi_file_noop(self, tmp_path):
        """Multiple files with all entries completed should result in no run_flow calls."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        flow_path = Path("tests/fixtures/batch_flow.yaml")

        tf1 = TaskFile(
            entries=[
                TaskEntry(description="task 1", status="completed"),
                TaskEntry(description="task 2", status="completed"),
            ]
        )
        save_task_file(tasks_dir / "001-file1.yaml", tf1)

        tf2 = TaskFile(
            entries=[
                TaskEntry(description="task 3", status="completed"),
                TaskEntry(description="task 4", status="completed"),
            ]
        )
        save_task_file(tasks_dir / "002-file2.yaml", tf2)

        with patch(
            "fdsx.core.engine.run_flow", return_value={"result": "ok"}
        ) as mock_run:
            with patch("fdsx.core.engine.display_tasks_dir_summary"):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

        mock_run.assert_not_called()
        assert len(results) == 4
        for r in results:
            assert r["category"] == "skipped"
            assert r["status"] == "completed"

    def test_single_task_file_single_entry(self, tmp_path):
        """One file with one entry should execute correctly."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        flow_path = Path("tests/fixtures/batch_flow.yaml")

        tf = TaskFile(entries=[TaskEntry(description="single task")])
        save_task_file(tasks_dir / "001-single.yaml", tf)

        with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
            with patch("fdsx.core.engine.display_tasks_dir_summary"):
                results = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

        assert len(results) == 1
        assert results[0]["status"] == "completed"
        assert results[0]["category"] == "new"

        loaded = load_task_file(tasks_dir / "001-single.yaml")
        assert loaded.entries[0].status == "completed"

    def test_mix_valid_and_invalid_yaml_files(self, tmp_path):
        """Mix of valid and invalid YAML files should error clearly."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        (tasks_dir / "001-valid.yaml").write_text("description: valid task\n")
        (tasks_dir / "002-invalid.yaml").write_text(": [broken yaml\n")
        (tasks_dir / "003-also-valid.yaml").write_text("description: another valid\n")

        workflow_path = Path("tests/fixtures/simple_flow.yaml")

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

        assert result.exit_code == 2
        assert "002-invalid.yaml" in result.stderr or "invalid" in result.stderr.lower()


class TestFullPipelineE2E:
    """T43: End-to-end full pipeline test (T029).

    Tests the complete lifecycle:
    1. Split task content into groups via mock LLM (with spinner)
    2. Simulate user editing a task file
    3. Run tasks_dir with 2nd task failing
    4. Verify partial state: first=completed, second=failed
    5. Resume: run again, verify first skipped, second retried
    6. Verify final state: all completed
    """

    def test_split_edit_run_crash_resume_complete(self, tmp_path):
        """Full lifecycle: split → edit → run (crash) → resume → complete."""
        tasks_dir = tmp_path / TASKS_DIR
        tasks_dir.mkdir(parents=True)
        flow_path = Path("tests/fixtures/batch_flow.yaml")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Implement feature A"}, {"description": "Implement feature B"}]]',
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            result_groups = split_tasks_to_groups(
                "Implement features A and B",
                task_splitter,
            )

        assert len(result_groups) == 1
        assert len(result_groups[0]) == 2

        created_files = write_task_files(result_groups, tasks_dir)
        assert len(created_files) == 1

        task_file_path = created_files[0]
        assert task_file_path.exists()

        task_file = load_task_file(task_file_path)
        assert len(task_file.entries) == 2
        assert task_file.entries[0].description == "Implement feature A"
        assert task_file.entries[1].description == "Implement feature B"

        task_file.entries[1].description = "Implement feature B (edited)"
        save_task_file(task_file_path, task_file)
        edited = load_task_file(task_file_path)
        assert edited.entries[1].description == "Implement feature B (edited)"

        run_count = [0]

        def mock_run_flow(flow_path, inputs, thread_id, base_dir):
            run_count[0] += 1
            task_desc = ""
            if inputs:
                task_desc = inputs.get("task", "")
            if task_desc and "feature B" in task_desc:
                raise RuntimeError("Simulated crash during feature B")
            return {"result": "ok"}

        with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
            with patch("fdsx.core.engine.display_tasks_dir_summary"):
                with patch("fdsx.core.engine.input", side_effect=["n"]):
                    results1 = engine.run_tasks_dir(
                        flow_path, tasks_dir, auto_workflow=True
                    )

        assert len(results1) == 2
        assert run_count[0] == 2, "Both entries should have been attempted"

        task_file_after_run1 = load_task_file(task_file_path)
        assert task_file_after_run1.entries[0].status == "completed"
        assert task_file_after_run1.entries[0].description == "Implement feature A"
        assert task_file_after_run1.entries[1].status == "failed"
        assert (
            task_file_after_run1.entries[1].description
            == "Implement feature B (edited)"
        )
        assert "Simulated crash" in (task_file_after_run1.entries[1].error or "")

        run_count_after_resume = [0]

        def mock_run_flow_resume(flow_path, inputs, thread_id, base_dir):
            run_count_after_resume[0] += 1
            return {"result": "ok"}

        with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow_resume):
            with patch("fdsx.core.engine.display_tasks_dir_summary"):
                results2 = engine.run_tasks_dir(
                    flow_path, tasks_dir, auto_workflow=True
                )

        assert len(results2) == 2
        assert run_count_after_resume[0] == 1, (
            "Only the failed entry should have been retried"
        )

        for r in results2:
            if r["entry_description"] == "Implement feature A":
                assert r["category"] == "skipped"
                assert r["status"] == "completed"
            elif r["entry_description"] == "Implement feature B (edited)":
                assert r["category"] == "retried"
                assert r["status"] == "completed"

        task_file_final = load_task_file(task_file_path)
        assert task_file_final.entries[0].status == "completed"
        assert task_file_final.entries[0].description == "Implement feature A"
        assert task_file_final.entries[1].status == "completed"
        assert task_file_final.entries[1].description == "Implement feature B (edited)"

    def test_e2e_split_helpers_then_run_via_cli(self, tmp_path):
        """End-to-end: split helpers create files, then run command executes them via CLI."""
        tasks_dir = tmp_path / TASKS_DIR
        tasks_dir.mkdir(parents=True)
        flow_path = Path("tests/fixtures/batch_flow.yaml")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "CLI test task 1"}, {"description": "CLI test task 2"}]]',
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            result_groups = split_tasks_to_groups(
                "CLI test tasks",
                task_splitter,
            )
        created_files = write_task_files(result_groups, tasks_dir)

        with patch("fdsx.core.engine.run_flow", return_value={"result": "ok"}):
            with patch("fdsx.core.engine.display_tasks_dir_summary"):
                runner = CliRunner()
                result = runner.invoke(
                    app,
                    [
                        "run",
                        str(flow_path),
                        "--tasks-dir",
                        str(tasks_dir),
                        "--auto-workflow",
                    ],
                )

        assert result.exit_code == 0, (
            f"Expected exit code 0, got {result.exit_code}: {result.stderr}"
        )

        for f in created_files:
            loaded = load_task_file(f)
            for entry in loaded.entries:
                assert entry.status == "completed", (
                    f"Entry '{entry.description}' should be completed"
                )

    def test_full_pipeline_split_auto_select_cui_persist_skip_error_resume(
        self, tmp_path
    ):
        """T029: Full pipeline: split (with spinner) → auto-select → CUI confirm → persist → re-run (skip) → error → resume command.

        This tests the complete integration of all Phase 1-6 features:
        - Split creates task files with mock LLM
        - Run auto-selects workflows (spinner shown)
        - CUI confirms assignments
        - Workflow field is persisted in YAML
        - Re-run skips auto-selection (workflow already set)
        - Error triggers resume command display
        """
        import yaml

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

        tasks_dir = tmp_path / TASKS_DIR
        tasks_dir.mkdir(parents=True)

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "E2E Task 1"}, {"description": "E2E Task 2"}]]',
            stderr="",
        )

        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            result_groups = split_tasks_to_groups(
                "E2E test tasks",
                task_splitter,
            )

        created_files = write_task_files(result_groups, tasks_dir)
        assert len(created_files) == 1

        task_file = load_task_file(created_files[0])
        assert len(task_file.entries) == 2
        assert task_file.entries[0].workflow is None
        assert task_file.entries[1].workflow is None

        resolve_count = [0]

        def mock_resolve(**kwargs):
            resolve_count[0] += 1
            return workflows_dir / "test.yaml"

        run_count = [0]

        def mock_run_flow(flow_path, inputs, thread_id, base_dir):
            run_count[0] += 1
            if inputs and "E2E Task 2" in inputs.get("task", ""):
                raise RuntimeError("Simulated error on Task 2")
            return {"result": "ok"}

        auto_select_spinners: list[str] = []

        class _SpinnerCapture:
            def __init__(self, message: str = ""):
                self._message = message

            def __enter__(self):
                auto_select_spinners.append(f"start:{self._message}")
                return self

            def __exit__(self, *args):
                auto_select_spinners.append("stop")

            def update(self, msg):
                auto_select_spinners.append(f"update:{msg}")

        with patch("fdsx.core.engine.Spinner", side_effect=_SpinnerCapture):
            with patch(
                "fdsx.core.engine.resolve_workflow_for_task", side_effect=mock_resolve
            ):
                with patch("fdsx.core.engine.run_flow", side_effect=mock_run_flow):
                    with patch("fdsx.core.engine.display_tasks_dir_summary"):
                        with patch("fdsx.core.engine.input", side_effect=["n"]):
                            results = engine.run_tasks_dir(
                                None,
                                tasks_dir,
                                base_dir=project_root / ".fdsx",
                                auto_workflow=True,
                            )

        assert resolve_count[0] == 2, "Both tasks should be auto-selected"
        assert run_count[0] == 2, "Both tasks should be attempted"

        updated_task_file = load_task_file(created_files[0])
        assert updated_task_file.entries[0].workflow == "test.yaml"
        assert updated_task_file.entries[1].workflow == "test.yaml"
        assert updated_task_file.entries[0].status == "completed"
        assert updated_task_file.entries[1].status == "failed"

        assert any("Auto-selecting" in msg for msg in auto_select_spinners), (
            "Spinner should show auto-selection progress"
        )

        resolve_count_after_persist = [0]

        def mock_resolve_persist(**kwargs):
            resolve_count_after_persist[0] += 1
            return workflows_dir / "test.yaml"

        run_count_rerun = [0]

        def mock_run_flow_rerun(flow_path, inputs, thread_id, base_dir):
            run_count_rerun[0] += 1
            return {"result": "ok"}

        with patch("fdsx.core.engine.Spinner", side_effect=_SpinnerCapture):
            with patch(
                "fdsx.core.engine.resolve_workflow_for_task",
                side_effect=mock_resolve_persist,
            ):
                with patch(
                    "fdsx.core.engine.run_flow", side_effect=mock_run_flow_rerun
                ):
                    with patch("fdsx.core.engine.display_tasks_dir_summary"):
                        with patch("fdsx.core.engine.input", side_effect=["n"]):
                            results2 = engine.run_tasks_dir(
                                None,
                                tasks_dir,
                                base_dir=project_root / ".fdsx",
                                auto_workflow=True,
                            )

        assert resolve_count_after_persist[0] == 0, (
            "Workflow already set; auto-selection should be skipped after persistence"
        )
        assert run_count_rerun[0] == 1, (
            "Only Task 2 (failed) should be retried on re-run"
        )

        for r in results2:
            if r["entry_description"] == "E2E Task 1":
                assert r["category"] == "skipped"
            elif r["entry_description"] == "E2E Task 2":
                assert r["category"] == "retried"
                assert r["status"] == "completed"

        final_task_file = load_task_file(created_files[0])
        assert final_task_file.entries[0].status == "completed"
        assert final_task_file.entries[1].status == "completed"


class TestHelpText:
    """Verify help text is descriptive (T41/T27)."""

    def test_run_command_help_includes_batch_modes(self):
        """Verify run command help mentions batch and tasks-dir modes."""
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        assert "in-memory batch" in result.stdout
        assert "persistent batch" in result.stdout

    def test_tasks_option_help_is_descriptive(self):
        """Verify --tasks option help is descriptive."""
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        assert "in-memory splitting and" in result.stdout
        assert "execution" in result.stdout

    def test_tasks_dir_option_help_mentions_resume(self):
        """Verify --tasks-dir option help mentions resume capability."""
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        assert "persistent" in result.stdout
        assert "resume support" in result.stdout

    def test_run_help_mentions_spinner_and_cui(self):
        """Verify run command help mentions spinner, CUI, and non-TTY behavior (T027)."""
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        assert "spinner" in result.stdout.lower()
        assert "confirmation" in result.stdout.lower()
        assert (
            "non-tty" in result.stdout.lower()
            or "noninteractive" in result.stdout.lower()
        )

    def test_run_help_mentions_auto_workflow_and_cui(self):
        """Verify --auto-workflow and --confirm-workflow mention CUI behavior (T027)."""
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        help_lower = result.stdout.lower()
        assert "skip" in help_lower and "confirmation" in help_lower
        assert "interactive" in help_lower or "workflow" in help_lower

    def test_split_help_mentions_spinner(self):
        """Verify split command help mentions spinner and non-TTY fallback (T027)."""
        runner = CliRunner()
        result = runner.invoke(app, ["split", "--help"])

        assert result.exit_code == 0
        assert "spinner" in result.stdout.lower() or "animated" in result.stdout.lower()
        assert (
            "non-tty" in result.stdout.lower()
            or "noninteractive" in result.stdout.lower()
        )


class TestSecuritySanitization:
    """Regression: ANSI injection via crafted filenames must be stripped from CLI output."""

    def test_validation_error_ansi_stripped_from_cli_output(self, tmp_path):
        """Regression: FlowValidationError containing ANSI escape sequences must not leak
        to the terminal.  A crafted .yaml filename with embedded escape bytes was the
        reported attack vector (security finding – unsanitized validation errors).
        """
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        # Create a YAML file with broken content so load_tasks_dir raises FlowValidationError
        (tasks_dir / "001-bad.yaml").write_text(": [broken yaml\n")

        workflow_path = Path("tests/fixtures/simple_flow.yaml")

        runner = CliRunner()

        # Patch load_tasks_dir to raise a FlowValidationError whose message contains
        # ANSI escape sequences (simulating a crafted filename being embedded in the error).
        ansi_payload = "\x1b[31mINJECTED\x1b[0m"
        with patch(
            "fdsx.cli.main.engine.run_tasks_dir",
            side_effect=engine.FlowValidationError(
                f"Invalid file {ansi_payload} caused error"
            ),
        ):
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

        assert result.exit_code == 2, f"Expected exit code 2, got {result.exit_code}"
        # ANSI escape byte must NOT appear in output
        assert "\x1b" not in result.output, (
            f"ANSI escape sequence leaked to terminal output: {result.output!r}"
        )
        # The visible error text should still be present (stripped of escape codes)
        assert "INJECTED" in result.output, (
            f"Visible error content was lost: {result.output!r}"
        )

"""E2E tests for full pipeline (T43), help text (T41/T27), and security sanitization."""

import re
from unittest.mock import MagicMock, patch

import yaml
from click import unstyle
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core import engine
from fdsx.core.batch import TASKS_DIR, split_tasks_to_groups, write_task_files
from fdsx.core.config import TaskSplitterConfig
from fdsx.core.engine import FlowResult
from fdsx.models.task import load_task_file, save_task_file
from tests import FIXTURES_DIR


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
        flow_path = FIXTURES_DIR / "batch_flow.yaml"

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

        def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
            run_count[0] += 1
            task_desc = ""
            if inputs:
                task_desc = inputs.get("task", "")
            if task_desc and "feature B" in task_desc:
                raise RuntimeError("Simulated crash during feature B")
            return FlowResult(results={"result": "ok"}, status="completed")

        with (
            patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            patch("fdsx.core.engine.tasks_dir.input", side_effect=["n"]),
        ):
            results1 = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

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

        def mock_run_flow_resume(flow_path, inputs, thread_id, base_dir, **kwargs):
            run_count_after_resume[0] += 1
            return FlowResult(results={"result": "ok"}, status="completed")

        with (
            patch(
                "fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow_resume
            ),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            results2 = engine.run_tasks_dir(flow_path, tasks_dir, auto_workflow=True)

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

        # After all entries complete on resume, the file is moved to completed/
        completed_path = task_file_path.parent / "completed" / task_file_path.name
        task_file_final = load_task_file(completed_path)
        assert task_file_final.entries[0].status == "completed"
        assert task_file_final.entries[0].description == "Implement feature A"
        assert task_file_final.entries[1].status == "completed"
        assert task_file_final.entries[1].description == "Implement feature B (edited)"

    def test_e2e_split_helpers_then_run_via_cli(self, tmp_path):
        """End-to-end: split helpers create files, then run command executes them via CLI."""
        tasks_dir = tmp_path / TASKS_DIR
        tasks_dir.mkdir(parents=True)
        flow_path = FIXTURES_DIR / "batch_flow.yaml"

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
            # After all entries complete, files are moved to completed/
            completed_f = f.parent / "completed" / f.name
            loaded = load_task_file(completed_f)
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

        def mock_run_flow(flow_path, inputs, thread_id, base_dir, **kwargs):
            run_count[0] += 1
            if inputs and "E2E Task 2" in inputs.get("task", ""):
                raise RuntimeError("Simulated error on Task 2")
            return FlowResult(results={"result": "ok"}, status="completed")

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

        with (
            patch(
                "fdsx.core.engine.tasks_dir.Spinner",
                side_effect=_SpinnerCapture,
            ),
            patch(
                "fdsx.core.selector.resolve_workflow_for_task",
                side_effect=mock_resolve,
            ),
            patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=mock_run_flow),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            patch("fdsx.core.engine.tasks_dir.input", side_effect=["n"]),
        ):
            engine.run_tasks_dir(
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

        def mock_run_flow_rerun(flow_path, inputs, thread_id, base_dir, **kwargs):
            run_count_rerun[0] += 1
            return FlowResult(results={"result": "ok"}, status="completed")

        with (
            patch(
                "fdsx.core.engine.tasks_dir.Spinner",
                side_effect=_SpinnerCapture,
            ),
            patch(
                "fdsx.core.selector.resolve_workflow_for_task",
                side_effect=mock_resolve_persist,
            ),
            patch(
                "fdsx.core.engine.tasks_dir.run_flow",
                side_effect=mock_run_flow_rerun,
            ),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
            patch("fdsx.core.engine.tasks_dir.input", side_effect=["n"]),
        ):
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

        # After all entries complete on resume, the file is moved to completed/
        completed_path = created_files[0].parent / "completed" / created_files[0].name
        final_task_file = load_task_file(completed_path)
        assert final_task_file.entries[0].status == "completed"
        assert final_task_file.entries[1].status == "completed"


class TestHelpText:
    """Verify help text is descriptive (T41/T27)."""

    def test_run_command_help_includes_batch_modes(self, tmp_path):
        """Verify run command help mentions batch and tasks-dir modes."""
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        assert "persistent batch" in result.stdout

    def test_tasks_dir_option_help_mentions_resume(self, tmp_path):
        """Verify --tasks-dir option help mentions resume capability."""
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"], terminal_width=80)

        assert result.exit_code == 0
        plain_output = unstyle(result.stdout)
        normalized_output = " ".join(
            re.sub(r"[\u2500-\u257f]", " ", plain_output).split()
        )
        assert "--tasks-dir" in normalized_output
        assert (
            "Directory of task YAML files for persistent batch execution "
            "with resume support"
        ) in normalized_output

    def test_run_help_mentions_spinner_and_cui(self, tmp_path):
        """Verify run command help mentions spinner, CUI, and non-TTY behavior (T027)."""
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        assert "spinner" in result.stdout.lower()
        assert "confirmation" in result.stdout.lower()
        assert (
            "non-tty" in result.stdout.lower()
            or "noninteractive" in result.stdout.lower()
        )

    def test_run_help_mentions_auto_workflow_and_cui(self, tmp_path):
        """Verify --auto-workflow and --confirm-workflow mention CUI behavior (T027)."""
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()
        result = runner.invoke(app, ["run", "--help"])

        assert result.exit_code == 0
        help_lower = result.stdout.lower()
        assert "skip" in help_lower and "confirmation" in help_lower
        assert "interactive" in help_lower or "workflow" in help_lower

    def test_split_help_mentions_spinner(self, tmp_path):
        """Verify split command help mentions spinner and non-TTY fallback (T027)."""
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()
        result = runner.invoke(app, ["add", "--split", "--help"])

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
        reported attack vector (security finding - unsanitized validation errors).
        """
        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        # Create a YAML file with broken content so load_tasks_dir raises FlowValidationError
        (tasks_dir / "001-bad.yaml").write_text(": [broken yaml\n")

        workflow_path = FIXTURES_DIR / "simple_flow.yaml"

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

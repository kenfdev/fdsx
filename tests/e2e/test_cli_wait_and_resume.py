import tempfile
from pathlib import Path

from tests.e2e.cli_test_utils import fixture_path, run_fdsx


class TestCLIE2EPhase3:
    """End-to-end CLI tests for Phase 3 (Wait state, checkpoint/resume, prompt_file)."""

    def test_wait_state_flow_with_stdin_selection(self):
        """Test fdsx run with Wait state flow - provide input via stdin."""
        result = run_fdsx(
            ["run", fixture_path("wait_approval.yaml")],
            input="1\n",
            timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr

    def test_resume_interrupted_flow(self):
        """Test fdsx resume --thread-id with previously interrupted flow."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / ".fdsx").mkdir()
            flow_path = fixture_path("wait_approval.yaml")
            thread_id = "test-resume-interrupted"
            base_dir = str(tmp_path / ".fdsx")

            first_run = run_fdsx(
                ["run", flow_path, "--thread-id", thread_id],
                input="",
                cwd=tmp_dir,
                timeout=30,
            )
            assert first_run.returncode == 1, (
                f"Expected exit 1, got {first_run.returncode}. stderr: {first_run.stderr}"
            )
            assert "Checkpoint saved" in first_run.stderr

            resume_result = run_fdsx(
                [
                    "resume",
                    "--thread-id",
                    thread_id,
                    "--base-dir",
                    base_dir,
                ],
                input="1\n",
                cwd=tmp_dir,
                timeout=30,
            )
            assert resume_result.returncode == 0, f"stderr: {resume_result.stderr}"
            # FR-1.3: No JSON on stdout
            assert resume_result.stdout == ""
            # FR-1.1: Completion message on stderr
            assert "completed successfully" in resume_result.stderr

    def test_resume_nonexistent_thread(self):
        """Test fdsx resume --thread-id with non-existent thread returns exit code 2."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / ".fdsx"

            result = run_fdsx(
                [
                    "resume",
                    "--thread-id",
                    "nonexistent-thread-id-12345",
                    "--base-dir",
                    str(base_dir),
                ],
                timeout=30,
            )
            assert result.returncode == 2, (
                f"Expected exit code 2, got {result.returncode}"
            )
            assert "No checkpoint found" in result.stderr

    def test_list_shows_threads(self):
        """Test fdsx list shows table output with known thread."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / ".fdsx").mkdir()
            base_dir = tmp_path / ".fdsx"
            thread_id = "test-list-thread"
            flow_path = fixture_path("simple_flow.yaml")

            run_result = run_fdsx(
                [
                    "run",
                    flow_path,
                    "--thread-id",
                    thread_id,
                ],
                cwd=tmp_dir,
                timeout=30,
            )
            assert run_result.returncode == 0, f"stderr: {run_result.stderr}"

            list_result = run_fdsx(
                [
                    "list",
                    "--base-dir",
                    str(base_dir),
                ],
                cwd=tmp_dir,
                timeout=30,
            )
            assert list_result.returncode == 0, f"stderr: {list_result.stderr}"
            assert "THREAD_ID" in list_result.stdout
            assert "FLOW_NAME" in list_result.stdout
            assert "STATUS" in list_result.stdout
            assert "CURRENT_STATE" in list_result.stdout
            assert "STARTED_AT" in list_result.stdout
            assert thread_id in list_result.stdout

    def test_list_empty(self):
        """Test fdsx list with no threads shows empty message."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / ".fdsx").mkdir()
            base_dir = tmp_path / ".fdsx"

            result = run_fdsx(
                [
                    "list",
                    "--base-dir",
                    str(base_dir),
                ],
                cwd=tmp_dir,
                timeout=30,
            )
            assert result.returncode == 0, f"stderr: {result.stderr}"
            assert "No flow executions found" in result.stdout

    def test_prompt_file_validate_success(self):
        """Test fdsx validate with prompt_file - file is loaded from disk successfully."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            prompt_path = Path(tmp_dir) / "my_prompt.txt"
            prompt_path.write_text("Analyze this code: {code}")

            flow_path = Path(tmp_dir) / "flow.yaml"
            flow_path.write_text(
                "name: Prompt File Validate Test\n"
                "description: Test prompt file validation\n"
                "start_at: task1\n"
                "version: '1.0'\n"
                "\n"
                "states:\n"
                "  task1:\n"
                "    type: task\n"
                "    provider: claude\n"
                "    model: opus\n"
                "    prompt_file: my_prompt.txt\n"
                "    result_path: $.result\n"
                "    end: true\n"
            )

            result = run_fdsx(["validate", str(flow_path)], timeout=30)
            assert result.returncode == 0, (
                f"Expected exit 0 (prompt_file loaded from disk), got {result.returncode}. "
                f"stderr: {result.stderr}"
            )
            assert "is valid" in result.stdout

    def test_prompt_file_missing_fails(self):
        """Test fdsx run with missing prompt_file fails with exit code 2 and clear error."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / ".fdsx").mkdir()
            flow_path = tmp_path / "flow.yaml"
            flow_path.write_text(
                "name: Missing Prompt File Test\n"
                "description: Test missing prompt file\n"
                "start_at: task1\n"
                "version: '1.0'\n"
                "\n"
                "states:\n"
                "  task1:\n"
                "    type: task\n"
                "    provider: claude\n"
                "    model: opus\n"
                "    prompt_file: nonexistent.txt\n"
                "    result_path: $.result\n"
                "    end: true\n"
            )

            result = run_fdsx(
                ["run", str(flow_path)],
                cwd=tmp_dir,
                timeout=30,
            )
            assert result.returncode == 2, (
                f"Expected exit code 2 (validation error), got {result.returncode}. "
                f"stderr: {result.stderr}"
            )
            assert "not found" in result.stderr.lower()

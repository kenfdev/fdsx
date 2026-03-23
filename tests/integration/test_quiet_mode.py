"""Integration tests for quiet mode end-to-end (T019, FR-5.1, FR-5.4).

Tests verify:
- --quiet suppresses stderr streaming output from providers
- Log files are still written with --quiet
- Completion summary still appears with --quiet
"""

import shutil
from pathlib import Path

from typer.testing import CliRunner

from fdsx.cli.main import app


class TestQuietModeE2E:
    """End-to-end integration tests for --quiet flag (FR-5.1, FR-5.4)."""

    def test_quiet_suppresses_state_streaming_output(self, tmp_path, monkeypatch):
        """--quiet suppresses [state_name] prefixed lines on stderr."""
        flow_path = str(
            Path(__file__).resolve().parent.parent / "fixtures" / "simple_flow.yaml"
        )
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, ["run", flow_path, "--quiet"])

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        # Streaming lines with [state_name] prefix should be absent
        assert "[plan]" not in result.output
        assert "[implement]" not in result.output
        assert "[review]" not in result.output

    def test_quiet_completion_summary_still_printed(self, tmp_path, monkeypatch):
        """--quiet does not suppress the completion summary."""
        flow_path = str(
            Path(__file__).resolve().parent.parent / "fixtures" / "simple_flow.yaml"
        )
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, ["run", flow_path, "--quiet"])

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        # Completion summary should still appear (from display_completion_summary)
        assert "completed successfully" in result.output

    def test_non_quiet_produces_streaming_output(self, tmp_path, monkeypatch):
        """Without --quiet, streaming output is produced (baseline)."""
        flow_path = str(
            Path(__file__).resolve().parent.parent / "fixtures" / "simple_flow.yaml"
        )
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, ["run", flow_path])

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        # At least one streaming line should be present without --quiet
        assert (
            "[plan]" in result.output
            or "[implement]" in result.output
            or "[review]" in result.output
        )

    def test_quiet_log_files_still_written(self, tmp_path, monkeypatch):
        """--quiet still writes per-state log files (FR-5.3)."""
        monkeypatch.chdir(tmp_path)

        src_fixture = (
            Path(__file__).resolve().parent.parent / "fixtures" / "simple_flow.yaml"
        )
        flow_file = tmp_path / "simple_flow.yaml"
        shutil.copy(src_fixture, flow_file)

        runner = CliRunner()
        thread_id = "test-quiet-log-files"

        result = runner.invoke(
            app,
            ["run", str(flow_file), "--thread-id", thread_id, "--quiet"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )

        # Verify log files were created despite --quiet
        log_dir = tmp_path / ".fdsx" / "runs" / thread_id / "logs"
        log_files = list(log_dir.glob("*.log"))
        assert len(log_files) > 0, f"Expected log files in {log_dir}, found none"

        # Verify at least one log file has content
        any_content = any(f.stat().st_size > 0 for f in log_files)
        assert any_content, "All log files are empty"

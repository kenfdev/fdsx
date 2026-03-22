from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.display.terminal import display_resume_command


class TestDisplayResumeCommandSingleFlow:
    """Tests for display_resume_command in single-flow mode."""

    def test_displays_single_flow_resume_command(self):
        """Single-flow mode displays fdsx resume --thread-id with correct format."""
        buf = StringIO()
        display_resume_command(
            mode="single-flow",
            thread_id="abc-123-xyz",
            stream=buf,
        )

        output = buf.getvalue()
        assert "fdsx resume --thread-id abc-123-xyz" in output
        assert "To resume this flow, run:" in output

    def test_single_flow_box_format(self):
        """Resume command is displayed in a box format with borders."""
        buf = StringIO()
        display_resume_command(
            mode="single-flow",
            thread_id="test-thread",
            stream=buf,
        )

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert any(line.startswith("+--") for line in lines)
        assert any("|  $ fdsx resume" in line for line in lines)
        border_lines = [line for line in lines if line.startswith("+")]
        assert any(line.rstrip().endswith("+") for line in border_lines)

    def test_single_flow_thread_id_sanitized(self):
        """ANSI escape sequences in thread_id are sanitized."""
        buf = StringIO()
        display_resume_command(
            mode="single-flow",
            thread_id="\x1b[31mevil\x1b[0m",
            stream=buf,
        )

        output = buf.getvalue()
        assert "\x1b" not in output
        assert "evil" in output

    def test_single_flow_with_extra_args(self):
        """Extra arguments are appended to the resume command."""
        buf = StringIO()
        display_resume_command(
            mode="single-flow",
            thread_id="test-id",
            extra_args=["--base-dir", "/custom/path"],
            stream=buf,
        )

        output = buf.getvalue()
        assert "fdsx resume --thread-id test-id --base-dir /custom/path" in output

    def test_single_flow_extra_args_sanitized(self):
        """Extra arguments are sanitized."""
        buf = StringIO()
        display_resume_command(
            mode="single-flow",
            thread_id="test-id",
            extra_args=["\x1b[32mevil\x1b[0m"],
            stream=buf,
        )

        output = buf.getvalue()
        assert "\x1b" not in output
        assert "evil" in output

    def test_single_flow_requires_thread_id(self):
        """ValueError is raised if thread_id is None in single-flow mode."""
        buf = StringIO()
        with pytest.raises(ValueError, match="thread_id is required"):
            display_resume_command(
                mode="single-flow",
                thread_id=None,
                stream=buf,
            )

    def test_single_flow_output_goes_to_stream(self):
        """Output is written to the specified stream."""
        buf = StringIO()
        display_resume_command(
            mode="single-flow",
            thread_id="test-id",
            stream=buf,
        )

        assert "fdsx resume" in buf.getvalue()


class TestDisplayResumeCommandTasksDir:
    """Tests for display_resume_command in tasks-dir mode."""

    def test_displays_tasks_dir_run_command(self):
        """Tasks-dir mode displays fdsx run --tasks-dir with correct format."""
        buf = StringIO()
        display_resume_command(
            mode="tasks-dir",
            tasks_dir=Path("/path/to/tasks"),
            stream=buf,
        )

        output = buf.getvalue()
        assert "fdsx run --tasks-dir /path/to/tasks" in output
        assert "To continue processing, run:" in output

    def test_tasks_dir_box_format(self):
        """Resume command is displayed in a box format with borders."""
        buf = StringIO()
        display_resume_command(
            mode="tasks-dir",
            tasks_dir=Path("./tasks"),
            stream=buf,
        )

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert any(line.startswith("+--") for line in lines)
        assert any("|  $ fdsx run" in line for line in lines)

    def test_tasks_dir_path_sanitized(self):
        """ANSI escape sequences in path are sanitized."""
        buf = StringIO()
        display_resume_command(
            mode="tasks-dir",
            tasks_dir=Path("\x1b[31mevil\x1b[0m"),
            stream=buf,
        )

        output = buf.getvalue()
        assert "\x1b" not in output
        assert "evil" in output

    def test_tasks_dir_requires_tasks_dir(self):
        """ValueError is raised if tasks_dir is None in tasks-dir mode."""
        buf = StringIO()
        with pytest.raises(ValueError, match="tasks_dir is required"):
            display_resume_command(
                mode="tasks-dir",
                tasks_dir=None,
                stream=buf,
            )

    def test_tasks_dir_with_extra_args(self):
        """Extra arguments are appended to the run command."""
        buf = StringIO()
        display_resume_command(
            mode="tasks-dir",
            tasks_dir=Path("/path/to/tasks"),
            extra_args=["--auto-workflow"],
            stream=buf,
        )

        output = buf.getvalue()
        assert "fdsx run --tasks-dir /path/to/tasks --auto-workflow" in output


class TestDisplayResumeCommandDefaults:
    """Tests for default behavior of display_resume_command."""

    def test_defaults_to_stderr(self):
        """When no stream is provided, output goes to sys.stderr."""
        with patch("sys.stderr", new_callable=StringIO) as mock_stderr:
            display_resume_command(
                mode="single-flow",
                thread_id="test-id",
            )

        output = mock_stderr.getvalue()
        assert "fdsx resume" in output

    def test_invalid_mode_raises_error(self):
        """Invalid mode raises ValueError."""
        buf = StringIO()
        with pytest.raises(ValueError, match="Invalid mode"):
            display_resume_command(
                mode="invalid-mode",
                thread_id="test-id",
                stream=buf,
            )

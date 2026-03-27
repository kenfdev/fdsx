"""Integration tests for StreamLogger output streaming (T001, T002).

Tests verify that:
- Summary lines are always visible on stderr regardless of quiet mode
- Raw stdout/stderr output is suppressed in quiet mode
- Raw stdout/stderr output is visible in normal mode
- Summary lines are written to the log file
"""

from fdsx.logging.stream_logger import StreamLogger


class TestSummaryLinesInQuietMode:
    """T001: Summary lines are visible even when quiet=True."""

    def test_summary_lines_visible_in_quiet_mode(self, capsys):
        """on_summary() output appears on stderr even when quiet=True."""
        logger = StreamLogger(state_name="s", quiet=True)
        logger.on_summary("test")

        stderr = capsys.readouterr().err
        assert "[s] test" in stderr


class TestRawOutputInQuietMode:
    """T002a: Raw stdout is suppressed when quiet=True."""

    def test_raw_output_suppressed_in_quiet_mode(self, capsys):
        """on_stdout() output is suppressed when quiet=True."""
        logger = StreamLogger(state_name="s", quiet=True)
        logger.on_stdout("test")

        stderr = capsys.readouterr().err
        assert "test" not in stderr


class TestRawOutputInNormalMode:
    """T002b: Raw stdout is visible when quiet=False."""

    def test_raw_output_visible_in_normal_mode(self, capsys):
        """on_stdout() output appears on stderr when quiet=False."""
        logger = StreamLogger(state_name="s", quiet=False)
        logger.on_stdout("test")

        stderr = capsys.readouterr().err
        assert "[s] test" in stderr


class TestSummaryWrittenToLogFile:
    """T002c: Summary lines are written to the per-state log file."""

    def test_summary_written_to_log_file(self, tmp_path):
        """on_summary() writes content to the log file when log_dir is set."""
        logger = StreamLogger(state_name="s", log_dir=tmp_path, quiet=True)
        logger.on_summary("hello")
        logger.close()

        log_file = tmp_path / "s_1.log"
        content = log_file.read_text()
        assert "hello" in content

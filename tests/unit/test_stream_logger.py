"""Unit tests for StreamLogger (T010).

Tests verify:
- on_stdout and on_stderr prefix lines with [state_name] and print to stderr
- Lines are written to the per-state log file
- Log file is created lazily (only when first line arrives)
- No log file created when no output is produced (FR-2.6)
- ANSI escape codes pass through without sanitization (FR-2.7)
- close() flushes and closes the file handle
- log_dir=None suppresses file writes but terminal output still works
- Thread-safe writes (multiple threads can call on_stdout/on_stderr)
"""

import sys
import threading
from unittest.mock import patch

from fdsx.logging.stream_logger import LOG_FILE_SUFFIX, StreamLogger


class TestStreamLoggerTerminalOutput:
    """Tests for terminal (stderr) streaming behaviour."""

    def test_on_stdout_prefixes_line(self, capsys):
        """on_stdout prints '[state_name] line' to stderr."""
        logger = StreamLogger("MyState")
        logger.on_stdout("hello world")
        captured = capsys.readouterr()
        assert captured.err == "[MyState] hello world\n"

    def test_on_stderr_prefixes_line(self, capsys):
        """on_stderr prints '[state_name] line' to stderr."""
        logger = StreamLogger("SomeState")
        logger.on_stderr("error line")
        captured = capsys.readouterr()
        assert captured.err == "[SomeState] error line\n"

    def test_multiple_lines_all_prefixed(self, capsys):
        """Each line gets its own [state_name] prefix."""
        logger = StreamLogger("Planner")
        logger.on_stdout("line one")
        logger.on_stdout("line two")
        captured = capsys.readouterr()
        lines = captured.err.splitlines()
        assert lines == ["[Planner] line one", "[Planner] line two"]

    def test_ansi_passthrough_stdout(self, capsys):
        """ANSI escape codes pass through as-is (FR-2.7)."""
        ansi_line = "\x1b[32mgreen text\x1b[0m"
        logger = StreamLogger("ColorState")
        logger.on_stdout(ansi_line)
        captured = capsys.readouterr()
        assert ansi_line in captured.err

    def test_ansi_passthrough_stderr(self, capsys):
        """ANSI escape codes in stderr lines pass through as-is (FR-2.7)."""
        ansi_line = "\x1b[31merror in red\x1b[0m"
        logger = StreamLogger("ColorState")
        logger.on_stderr(ansi_line)
        captured = capsys.readouterr()
        assert ansi_line in captured.err

    def test_empty_line_still_prefixed(self, capsys):
        """Empty lines are still printed with the prefix."""
        logger = StreamLogger("State")
        logger.on_stdout("")
        captured = capsys.readouterr()
        assert captured.err == "[State] \n"

    def test_no_log_dir_does_not_raise(self, capsys):
        """StreamLogger with log_dir=None works without errors."""
        logger = StreamLogger("State", log_dir=None)
        logger.on_stdout("line")
        logger.on_stderr("err line")
        logger.close()
        captured = capsys.readouterr()
        assert "[State] line" in captured.err
        assert "[State] err line" in captured.err

    def test_on_stdout_flushes_stderr(self):
        """on_stdout calls sys.stderr.flush() after print (T006)."""
        logger = StreamLogger("FlushState")
        with patch.object(sys.stderr, "flush") as mock_flush:
            logger.on_stdout("hello")
        mock_flush.assert_called_once()

    def test_on_stderr_flushes_stderr(self):
        """on_stderr calls sys.stderr.flush() after print (T007)."""
        logger = StreamLogger("FlushState")
        with patch.object(sys.stderr, "flush") as mock_flush:
            logger.on_stderr("error")
        mock_flush.assert_called_once()

    def test_on_stdout_no_flush_when_quiet(self):
        """on_stdout does NOT flush when quiet=True (no print, no flush)."""
        logger = StreamLogger("FlushState", quiet=True)
        with patch.object(sys.stderr, "flush") as mock_flush:
            logger.on_stdout("hello")
        mock_flush.assert_not_called()

    def test_on_stderr_no_flush_when_quiet(self):
        """on_stderr does NOT flush when quiet=True (no print, no flush)."""
        logger = StreamLogger("FlushState", quiet=True)
        with patch.object(sys.stderr, "flush") as mock_flush:
            logger.on_stderr("error")
        mock_flush.assert_not_called()


class TestStreamLoggerFileWriting:
    """Tests for per-state log file writing."""

    def test_log_file_created_on_first_stdout(self, tmp_path):
        """Log file is created lazily on first stdout line (FR-2.6)."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("TestState", log_dir)

        # File should not exist before first write
        log_path = log_dir / f"TestState_1{LOG_FILE_SUFFIX}"
        assert not log_path.exists()

        logger.on_stdout("first line")
        logger.close()

        assert log_path.exists()

    def test_log_file_created_on_first_stderr(self, tmp_path):
        """Log file is created lazily on first stderr line (FR-2.6)."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("TestState", log_dir)

        log_path = log_dir / f"TestState_1{LOG_FILE_SUFFIX}"
        assert not log_path.exists()

        logger.on_stderr("error line")
        logger.close()

        assert log_path.exists()

    def test_no_log_file_when_no_output(self, tmp_path):
        """No log file is created when no output is produced (FR-2.6)."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("EmptyState", log_dir)
        logger.close()

        log_path = log_dir / f"EmptyState_1{LOG_FILE_SUFFIX}"
        assert not log_path.exists()

    def test_log_file_contains_stdout_lines(self, tmp_path):
        """Stdout lines are written verbatim to the log file (no prefix)."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("Writer", log_dir)
        logger.on_stdout("line one")
        logger.on_stdout("line two")
        logger.close()

        log_path = log_dir / f"Writer_1{LOG_FILE_SUFFIX}"
        content = log_path.read_text(encoding="utf-8")
        assert "line one\n" in content
        assert "line two\n" in content

    def test_log_file_contains_stderr_lines(self, tmp_path):
        """Stderr lines are written verbatim to the log file."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("Writer", log_dir)
        logger.on_stderr("stderr line")
        logger.close()

        log_path = log_dir / f"Writer_1{LOG_FILE_SUFFIX}"
        content = log_path.read_text(encoding="utf-8")
        assert "stderr line\n" in content

    def test_log_file_has_no_prefix_in_content(self, tmp_path):
        """Log file content does not include the [state_name] prefix."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("PrefixCheck", log_dir)
        logger.on_stdout("raw line")
        logger.close()

        log_path = log_dir / f"PrefixCheck_1{LOG_FILE_SUFFIX}"
        content = log_path.read_text(encoding="utf-8")
        assert "[PrefixCheck]" not in content
        assert "raw line\n" in content

    def test_ansi_codes_written_to_log_file(self, tmp_path):
        """ANSI codes pass through to log file as-is (FR-2.7)."""
        log_dir = tmp_path / "logs"
        ansi_line = "\x1b[32mcolored\x1b[0m"
        logger = StreamLogger("AnsiState", log_dir)
        logger.on_stdout(ansi_line)
        logger.close()

        log_path = log_dir / f"AnsiState_1{LOG_FILE_SUFFIX}"
        content = log_path.read_text(encoding="utf-8")
        assert ansi_line in content

    def test_log_dir_created_automatically(self, tmp_path):
        """log_dir is created automatically if it does not exist."""
        log_dir = tmp_path / "nested" / "logs"
        assert not log_dir.exists()

        logger = StreamLogger("AutoDir", log_dir)
        logger.on_stdout("trigger creation")
        logger.close()

        assert log_dir.exists()
        assert (log_dir / f"AutoDir_1{LOG_FILE_SUFFIX}").exists()

    def test_log_file_stem_matches_state_name(self, tmp_path):
        """Log file stem matches exactly the state_name."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("my_state", log_dir)
        logger.on_stdout("x")
        logger.close()

        assert (log_dir / f"my_state_1{LOG_FILE_SUFFIX}").exists()

    def test_close_idempotent(self, tmp_path):
        """Calling close() multiple times does not raise."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("State", log_dir)
        logger.on_stdout("line")
        logger.close()
        logger.close()  # Should not raise


class TestStreamLoggerQuietMode:
    """Tests for StreamLogger quiet mode (T015, FR-5.1, FR-5.4)."""

    def test_quiet_false_is_default(self, capsys):
        """quiet=False is the default: stderr output is produced."""
        logger = StreamLogger("State")
        assert logger.quiet is False
        logger.on_stdout("hello")
        captured = capsys.readouterr()
        assert "[State] hello" in captured.err

    def test_quiet_true_suppresses_stdout_stderr_print(self, capsys):
        """quiet=True suppresses print to stderr for on_stdout."""
        logger = StreamLogger("State", quiet=True)
        logger.on_stdout("hello")
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_quiet_true_suppresses_on_stderr_print(self, capsys):
        """quiet=True suppresses print to stderr for on_stderr."""
        logger = StreamLogger("State", quiet=True)
        logger.on_stderr("error line")
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_quiet_true_log_file_content_matches_non_quiet(self, tmp_path):
        """quiet=True writes identical log file content as quiet=False."""
        log_dir_normal = tmp_path / "normal"
        log_dir_quiet = tmp_path / "quiet"

        logger_normal = StreamLogger("State", log_dir_normal, quiet=False)
        logger_normal.on_stdout("line one")
        logger_normal.on_stderr("line two")
        logger_normal.close()

        logger_quiet = StreamLogger("State", log_dir_quiet, quiet=True)
        logger_quiet.on_stdout("line one")
        logger_quiet.on_stderr("line two")
        logger_quiet.close()

        normal_content = (log_dir_normal / f"State_1{LOG_FILE_SUFFIX}").read_text(
            encoding="utf-8"
        )
        quiet_content = (log_dir_quiet / f"State_1{LOG_FILE_SUFFIX}").read_text(
            encoding="utf-8"
        )
        assert normal_content == quiet_content

    def test_quiet_true_log_file_still_created(self, tmp_path):
        """quiet=True still creates the log file on first write (FR-5.4)."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("State", log_dir, quiet=True)
        logger.on_stdout("trigger")
        logger.close()

        assert (log_dir / f"State_1{LOG_FILE_SUFFIX}").exists()


class TestStreamLoggerThreadSafety:
    """Tests for thread-safe behaviour."""

    def test_concurrent_writes_from_same_logger(self, tmp_path):
        """Multiple threads writing through the same StreamLogger instance are safe."""
        log_dir = tmp_path / "logs"
        logger = StreamLogger("ThreadedState", log_dir)

        errors: list[Exception] = []

        def write_lines(n: int) -> None:
            try:
                for i in range(n):
                    logger.on_stdout(f"thread line {i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=write_lines, args=(20,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        logger.close()

        assert not errors, f"Errors in threads: {errors}"
        log_path = log_dir / f"ThreadedState_1{LOG_FILE_SUFFIX}"
        content = log_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        # 5 threads × 20 lines = 100 total lines
        assert len(lines) == 100

    def test_parallel_branches_share_log_file(self, tmp_path):
        """Two StreamLogger instances with the same state_name append to the same file."""
        log_dir = tmp_path / "logs"
        logger_a = StreamLogger("parallel_state", log_dir)
        logger_b = StreamLogger("parallel_state", log_dir)

        logger_a.on_stdout("branch_a line")
        logger_b.on_stdout("branch_b line")

        logger_a.close()
        logger_b.close()

        log_path = log_dir / f"parallel_state_1{LOG_FILE_SUFFIX}"
        content = log_path.read_text(encoding="utf-8")
        assert "branch_a line" in content
        assert "branch_b line" in content

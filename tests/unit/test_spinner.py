import sys
from io import StringIO
from unittest.mock import patch

import pytest

from fdsx.core.mode import is_interactive, set_interactive_mode
from fdsx.display.terminal import Spinner


class TestIsInteractive:
    """Tests for is_interactive function."""

    def test_returns_true_when_stdin_is_tty(self):
        """When stdin is a TTY, is_interactive returns True."""
        set_interactive_mode(None)
        try:
            with patch("sys.stdin.isatty", return_value=True):
                result = is_interactive()

            assert result is True
        finally:
            set_interactive_mode(None)

    def test_returns_false_when_stdin_is_not_tty(self):
        """When stdin is not a TTY (piped/redirected), is_interactive returns False."""
        set_interactive_mode(None)
        try:
            with patch("sys.stdin.isatty", return_value=False):
                result = is_interactive()

            assert result is False
        finally:
            set_interactive_mode(None)


class TestSpinnerTTYMode:
    """Tests for Spinner in TTY mode."""

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_start_creates_daemon_thread(self, _mock):
        """start() spawns a daemon thread in TTY mode."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)
        spinner.start()

        assert spinner._thread is not None
        assert spinner._thread.daemon is True
        assert spinner._running is True
        spinner.stop()

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_start_returns_self(self, _mock):
        """start() returns the Spinner instance itself."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)
        result = spinner.start()

        assert result is spinner
        spinner.stop()

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_stop_joins_thread(self, _mock):
        """stop() joins the thread and sets _running to False."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)
        spinner.start()
        spinner.stop()

        assert spinner._thread is None
        assert spinner._running is False

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_update_changes_message(self, _mock):
        """update() changes the internal message in TTY mode."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)
        spinner.start()
        spinner.update("Updated message")

        assert spinner._message == "Updated message"
        spinner.stop()

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_double_start_no_concurrent_threads(self, _mock):
        """Calling start() twice does not create concurrent threads."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)
        spinner.start()
        first_thread = spinner._thread
        assert first_thread is not None

        spinner.start()
        second_thread = spinner._thread
        assert second_thread is not None

        assert first_thread is not second_thread
        assert not first_thread.is_alive()
        assert spinner._running is True
        spinner.stop()
        assert spinner._thread is None

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_writes_frames_to_stream(self, _mock):
        """Spinner writes braille frame characters to the stream in TTY mode."""
        buf = StringIO()
        spinner = Spinner("Working", stream=buf)
        spinner.start()
        spinner._stop_event.wait(0.25)
        spinner.stop()

        output = buf.getvalue()
        assert "Working" in output
        # At least one braille character should appear
        assert any(ch in output for ch in Spinner._FRAMES)

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_stop_with_final_message(self, _mock):
        """stop(final_message) prints the message after clearing the line."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)
        spinner.start()
        spinner.stop("Done!")

        output = buf.getvalue()
        assert "Done!" in output

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_stop_clears_line(self, _mock):
        """stop() writes carriage-return + erase-line sequence in TTY mode."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)
        spinner.start()
        spinner.stop()

        output = buf.getvalue()
        assert "\r\033[K" in output


class TestSpinnerNonTTYMode:
    """Tests for Spinner in non-TTY mode."""

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_start_prints_message_no_thread(self, _mock):
        """start() prints message once and spawns no thread in non-TTY mode."""
        buf = StringIO()
        spinner = Spinner("Processing", stream=buf)
        spinner.start()

        output = buf.getvalue()
        assert "Processing\n" in output
        assert spinner._thread is None

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_update_prints_new_line(self, _mock):
        """update() prints the new message as a newline in non-TTY mode."""
        buf = StringIO()
        spinner = Spinner("Step 1", stream=buf)
        spinner.start()
        spinner.update("Step 2")

        output = buf.getvalue()
        assert "Step 1\n" in output
        assert "Step 2\n" in output

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_stop_with_final_message(self, _mock):
        """stop(final_message) prints the message in non-TTY mode."""
        buf = StringIO()
        spinner = Spinner("Processing", stream=buf)
        spinner.start()
        spinner.stop("Complete")

        output = buf.getvalue()
        assert "Complete\n" in output

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_no_carriage_return(self, _mock):
        """Spinner does not write \\r in non-TTY mode (CI/log compatible)."""
        buf = StringIO()
        spinner = Spinner("Running", stream=buf)
        spinner.start()
        spinner.update("Still running")
        spinner.stop("Finished")

        output = buf.getvalue()
        assert "\r" not in output

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_only_prints_once_on_start(self, _mock):
        """start() prints the message exactly once in non-TTY mode."""
        buf = StringIO()
        spinner = Spinner("Single print", stream=buf)
        spinner.start()

        output = buf.getvalue()
        assert output.count("Single print") == 1


class TestSpinnerContextManager:
    """Tests for Spinner used as a context manager."""

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_context_manager_starts_and_stops(self, _mock):
        """Context manager starts and stops the spinner automatically."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)

        with spinner as s:
            assert s is spinner
            assert s._running is True

        assert spinner._running is False

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_context_manager_stops_on_exception(self, _mock):
        """Context manager stops the spinner even when an exception is raised."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)

        with (
            pytest.raises(ValueError),
            spinner,
        ):
            raise ValueError("test error")

        assert spinner._running is False
        assert spinner._thread is None

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_context_manager_non_tty(self, _mock):
        """Context manager works correctly in non-TTY mode."""
        buf = StringIO()
        with Spinner("Loading", stream=buf) as spinner:
            assert spinner._thread is None

        output = buf.getvalue()
        assert "Loading\n" in output


class TestSpinnerSecurity:
    """Tests that spinner messages are sanitized to prevent ANSI injection."""

    @patch("fdsx.core.mode.is_interactive", return_value=True)
    def test_ansi_escapes_sanitized_in_tty_mode(self, _mock):
        """ANSI escape sequences in messages are stripped in TTY mode."""
        buf = StringIO()
        spinner = Spinner("\x1b[31mevil\x1b[0m", stream=buf)
        spinner.start()
        spinner._stop_event.wait(0.15)
        spinner.stop()

        output = buf.getvalue()
        output_sanitized = output.replace("\033[K", "")
        assert "\x1b" not in output_sanitized
        assert "evil" in output

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_ansi_escapes_sanitized_in_non_tty_mode(self, _mock):
        """ANSI escape sequences in messages are stripped in non-TTY mode."""
        buf = StringIO()
        spinner = Spinner("\x1b[31mevil\x1b[0m", stream=buf)
        spinner.start()

        output = buf.getvalue()
        assert "\x1b" not in output
        assert "evil" in output

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_ansi_in_final_message_sanitized(self, _mock):
        """ANSI escape sequences in stop(final_message) are stripped."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)
        spinner.start()
        spinner.stop("\x1b[32mDone\x1b[0m")

        output = buf.getvalue()
        assert "\x1b" not in output
        assert "Done" in output

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_ansi_in_update_sanitized(self, _mock):
        """ANSI escape sequences in update() messages are stripped."""
        buf = StringIO()
        spinner = Spinner("Loading", stream=buf)
        spinner.start()
        spinner.update("\x1b[31mstep 2\x1b[0m")

        output = buf.getvalue()
        assert "\x1b" not in output
        assert "step 2" in output

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_newline_in_message_sanitized(self, _mock):
        """Newlines in messages are replaced to prevent log injection."""
        buf = StringIO()
        spinner = Spinner("syncing\n[12:00:00] ✓ deploy complete", stream=buf)
        spinner.start()

        output = buf.getvalue()
        lines = output.strip().split("\n")
        assert len(lines) == 1
        assert "syncing" in output
        assert "deploy complete" in output

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_newline_in_update_sanitized(self, _mock):
        """Newlines in update() messages are replaced."""
        buf = StringIO()
        spinner = Spinner("start", stream=buf)
        spinner.start()
        spinner.update("line1\nline2")

        output = buf.getvalue()
        update_lines = [line for line in output.strip().split("\n") if "line1" in line]
        assert len(update_lines) == 1
        assert "line2" in update_lines[0]


class TestSpinnerEdgeCases:
    """Tests for Spinner edge cases."""

    def test_defaults_to_stderr(self):
        """Spinner uses sys.stderr by default when no stream provided."""
        with patch("fdsx.core.mode.is_interactive", return_value=False):
            spinner = Spinner()
        assert spinner._stream is sys.stderr

    def test_custom_stream(self):
        """Spinner uses provided custom stream."""
        buf = StringIO()
        with patch("fdsx.core.mode.is_interactive", return_value=False):
            spinner = Spinner(stream=buf)
        assert spinner._stream is buf

    def test_frame_sequence_valid(self):
        """Spinner frames are valid Unicode braille characters."""
        assert len(Spinner._FRAMES) > 0
        for frame in Spinner._FRAMES:
            assert len(frame) == 1
            assert ord(frame) >= 0x2800

    @patch("fdsx.core.mode.is_interactive", return_value=False)
    def test_default_message_empty(self, _mock):
        """Spinner initializes with an empty message by default."""
        buf = StringIO()
        spinner = Spinner(stream=buf)
        assert spinner._message == ""

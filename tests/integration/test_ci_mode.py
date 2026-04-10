"""Integration tests for the mode module (T005-T007)."""

from unittest.mock import patch

from fdsx.core.mode import get_interactive_mode, is_interactive, set_interactive_mode


class TestMode:
    """Tests for the mode module functions."""

    def test_set_interactive_mode_true(self):
        """set_interactive_mode(True) makes is_interactive() return True."""
        set_interactive_mode(True)
        try:
            assert is_interactive() is True
        finally:
            set_interactive_mode(None)

    def test_set_interactive_mode_false(self):
        """set_interactive_mode(False) makes is_interactive() return False."""
        set_interactive_mode(False)
        try:
            assert is_interactive() is False
        finally:
            set_interactive_mode(None)

    def test_is_interactive_falls_back_to_stdin_tty(self):
        """When mode is None, is_interactive() falls back to sys.stdin.isatty()."""
        set_interactive_mode(None)
        try:
            with patch("sys.stdin.isatty", return_value=True):
                assert is_interactive() is True
            with patch("sys.stdin.isatty", return_value=False):
                assert is_interactive() is False
        finally:
            set_interactive_mode(None)

    def test_get_interactive_mode_returns_raw_value(self):
        """get_interactive_mode() returns the raw mode value."""
        set_interactive_mode(True)
        try:
            assert get_interactive_mode() is True
        finally:
            set_interactive_mode(None)

        set_interactive_mode(False)
        try:
            assert get_interactive_mode() is False
        finally:
            set_interactive_mode(None)

        set_interactive_mode(None)
        assert get_interactive_mode() is None

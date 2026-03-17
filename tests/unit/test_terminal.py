from io import StringIO
from unittest.mock import patch

from fdsx.display.terminal import display_wait_prompt


class TestDisplayWaitPrompt:
    """Regression tests for display_wait_prompt CLI contract compliance."""

    def test_prompt_text_goes_to_stderr_not_stdout(self):
        """Regression: 'Select (1-N):' prompt must appear on stderr, not stdout.

        Before the fix, `input(f"Select (1-{N}): ")` wrote the prompt to stdout,
        corrupting JSON output when piped (e.g., `fdsx run flow.yaml | jq .`).
        """
        captured_stdout = StringIO()
        captured_stderr = StringIO()

        with patch("sys.stdout", captured_stdout):
            with patch("sys.stderr", captured_stderr):
                with patch("builtins.input", return_value="1"):
                    result = display_wait_prompt(
                        "review", "Please review the plan.", ["approve", "reject"]
                    )

        assert result == "approve"
        # stdout must be completely empty — no prompt text
        assert captured_stdout.getvalue() == ""
        # prompt must appear on stderr with 2-space indent
        stderr_text = captured_stderr.getvalue()
        assert "  Select (1-2): " in stderr_text

    def test_prompt_has_two_space_indent(self):
        """Regression: prompt must be indented with exactly two spaces per CLI contract."""
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            with patch("builtins.input", return_value="1"):
                display_wait_prompt("review", "Approve?", ["yes", "no"])

        stderr_text = captured_stderr.getvalue()
        assert "  Select (1-2): " in stderr_text

    def test_returns_correct_choice_string(self):
        """Valid numeric input returns the corresponding choice string."""
        with patch("builtins.input", return_value="2"):
            result = display_wait_prompt("review", "Choose:", ["approve", "reject"])
        assert result == "reject"

    def test_multiline_message_all_lines_indented(self):
        """Regression: multi-line message must indent all lines, not just first."""
        multi_line_message = (
            "Please review the plan.\nPlan: Implement feature X\nand deploy to prod."
        )
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            with patch("builtins.input", return_value="1"):
                display_wait_prompt("review", multi_line_message, ["approve", "reject"])

        stderr_text = captured_stderr.getvalue()
        lines = stderr_text.splitlines()
        message_lines = [
            line
            for line in lines
            if "review the plan" in line or "Plan:" in line or "and deploy" in line
        ]
        for line in message_lines:
            assert line.startswith("  "), f"Line not indented: {line!r}"

    def test_invalid_then_valid_input_loops(self):
        """Invalid input re-prompts; valid input is accepted on subsequent attempt."""
        with patch("builtins.input", side_effect=["0", "abc", "2"]):
            result = display_wait_prompt("review", "Choose:", ["approve", "reject"])
        assert result == "reject"

    def test_ansi_escape_sequences_stripped_from_message(self):
        """Regression: ANSI/OSC escape sequences in LLM-derived message must be stripped.

        A crafted message containing ANSI escape codes could spoof the approval screen
        or manipulate the operator's terminal (terminal injection / ANSI injection).
        The existing _sanitize_output function must be applied before display.
        """
        ansi_message = "\x1b[31mDangerous\x1b[0m content\x1b]0;spoof title\x07"
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            with patch("builtins.input", return_value="1"):
                result = display_wait_prompt(
                    "review", ansi_message, ["approve", "reject"]
                )

        assert result == "approve"
        stderr_text = captured_stderr.getvalue()
        # ESC byte (\x1b) must not appear in terminal output
        assert "\x1b" not in stderr_text, "ANSI escape sequence leaked to terminal"
        # BEL byte (\x07) must not appear
        assert "\x07" not in stderr_text, "BEL byte leaked to terminal"
        # Visible text content must still be present
        assert "Dangerous" in stderr_text
        assert "content" in stderr_text

    def test_ansi_escape_sequences_stripped_from_choices(self):
        """Regression: ANSI escape sequences in choices must be stripped before display."""
        ansi_choices = ["\x1b[32mapprove\x1b[0m", "reject"]
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            with patch("builtins.input", return_value="1"):
                result = display_wait_prompt("review", "Choose:", ansi_choices)

        assert result == "\x1b[32mapprove\x1b[0m"  # original choice string returned
        stderr_text = captured_stderr.getvalue()
        # ESC must not appear in the rendered choice lines
        assert "\x1b" not in stderr_text, "ANSI escape in choice leaked to terminal"
        # Visible text must still appear
        assert "approve" in stderr_text

    def test_ansi_escape_sequences_stripped_from_state_name(self):
        """Regression: ANSI escape sequences in state_name must be stripped from header."""
        ansi_state_name = "\x1b[31mmalicious_state\x1b[0m"
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            with patch("builtins.input", return_value="1"):
                result = display_wait_prompt(
                    ansi_state_name, "Choose:", ["approve", "reject"]
                )

        assert result == "approve"
        stderr_text = captured_stderr.getvalue()
        # ESC byte must not appear in terminal output
        assert "\x1b" not in stderr_text, "ANSI escape in state_name leaked to terminal"
        # The visible text content must still appear in the header
        assert "malicious_state" in stderr_text

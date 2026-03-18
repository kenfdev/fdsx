from io import StringIO
from unittest.mock import patch

from fdsx.display.terminal import (
    display_branch_complete,
    display_branch_failed,
    display_branch_start,
    display_parallel_results,
    display_wait_prompt,
)


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


class TestDisplayBranchStart:
    """Tests for display_branch_start function."""

    def test_displays_branch_start_with_model(self):
        """Branch start shows provider/model format."""
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_start(
                state_name="review",
                branch_index=0,
                provider="claude",
                model="sonnet",
            )

        stderr_text = captured_stderr.getvalue()
        assert "[branch-1]" in stderr_text
        assert "claude/sonnet" in stderr_text
        assert "⏳ running..." in stderr_text

    def test_displays_branch_start_without_model(self):
        """Branch start shows provider only when model is None."""
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_start(
                state_name="review",
                branch_index=1,
                provider="openai",
                model=None,
            )

        stderr_text = captured_stderr.getvalue()
        assert "[branch-2]" in stderr_text
        assert "openai" in stderr_text
        assert "⏳ running..." in stderr_text
        assert "/None" not in stderr_text

    def test_branch_index_is_1_indexed(self):
        """Branch index should be 1-indexed in display."""
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_start(
                state_name="parallel",
                branch_index=2,
                provider="test",
                model=None,
            )

        stderr_text = captured_stderr.getvalue()
        assert "branch-3" in stderr_text

    def test_ansi_escape_sequences_stripped_from_model(self):
        """Regression: ANSI escape sequences in model must be stripped.

        A crafted branch with ANSI escape codes in the model field could
        spoof terminal output or manipulate the operator's terminal.
        """
        ansi_model = "\x1b[31mmalicious_model\x1b[0m"
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_start(
                state_name="review",
                branch_index=0,
                provider="test",
                model=ansi_model,
            )

        stderr_text = captured_stderr.getvalue()
        # ESC byte must not appear in terminal output
        assert "\x1b" not in stderr_text, "ANSI escape in model leaked to terminal"
        # Visible text content must still be present
        assert "malicious_model" in stderr_text


class TestDisplayBranchComplete:
    """Tests for display_branch_complete function."""

    def test_displays_branch_complete_with_model(self):
        """Branch completion shows provider/model and duration."""
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_complete(
                state_name="review",
                branch_index=0,
                provider="claude",
                model="sonnet",
                duration=5.5,
            )

        stderr_text = captured_stderr.getvalue()
        assert "[branch-1]" in stderr_text
        assert "claude/sonnet" in stderr_text
        assert "✓ completed" in stderr_text
        assert "(5s)" in stderr_text

    def test_displays_branch_complete_without_model(self):
        """Branch completion shows provider only when model is None."""
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_complete(
                state_name="review",
                branch_index=0,
                provider="claude",
                model=None,
                duration=5.5,
            )

        stderr_text = captured_stderr.getvalue()
        assert "[branch-1]" in stderr_text
        assert "claude" in stderr_text
        assert "✓ completed" in stderr_text
        assert "(5s)" in stderr_text

    def test_duration_is_truncated_to_integer(self):
        """Duration should be truncated to integer second (int() behavior)."""
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_complete(
                state_name="review",
                branch_index=0,
                provider="test",
                model=None,
                duration=3.7,
            )

        stderr_text = captured_stderr.getvalue()
        assert "(3s)" in stderr_text

    def test_ansi_escape_sequences_stripped_from_model(self):
        """Regression: ANSI escape sequences in model must be stripped."""
        ansi_model = "\x1b[31mmalicious_model\x1b[0m"
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_complete(
                state_name="review",
                branch_index=0,
                provider="test",
                model=ansi_model,
                duration=5.0,
            )

        stderr_text = captured_stderr.getvalue()
        assert "\x1b" not in stderr_text, "ANSI escape in model leaked to terminal"
        assert "malicious_model" in stderr_text


class TestDisplayBranchFailed:
    """Tests for display_branch_failed function."""

    def test_displays_branch_failed_with_model(self):
        """Branch failure shows provider/model and failure marker."""
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_failed(
                state_name="review",
                branch_index=1,
                provider="openai",
                model="gpt-4",
            )

        stderr_text = captured_stderr.getvalue()
        assert "[branch-2]" in stderr_text
        assert "openai/gpt-4" in stderr_text
        assert "✗ failed" in stderr_text

    def test_displays_branch_failed_without_model(self):
        """Branch failure shows provider only when model is None."""
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_failed(
                state_name="review",
                branch_index=1,
                provider="openai",
                model=None,
            )

        stderr_text = captured_stderr.getvalue()
        assert "[branch-2]" in stderr_text
        assert "openai" in stderr_text
        assert "✗ failed" in stderr_text

    def test_ansi_escape_sequences_stripped_from_model(self):
        """Regression: ANSI escape sequences in model must be stripped."""
        ansi_model = "\x1b[31mmalicious_model\x1b[0m"
        captured_stderr = StringIO()

        with patch("sys.stderr", captured_stderr):
            display_branch_failed(
                state_name="review",
                branch_index=0,
                provider="test",
                model=ansi_model,
            )

        stderr_text = captured_stderr.getvalue()
        assert "\x1b" not in stderr_text, "ANSI escape in model leaked to terminal"
        assert "malicious_model" in stderr_text


class TestDisplayParallelResults:
    """Tests for display_parallel_results function."""

    def test_displays_all_branch_outputs(self):
        """All branch results should be displayed with headers."""
        captured_stderr = StringIO()

        branch_results = [
            {
                "index": 0,
                "provider": "claude",
                "model": "sonnet",
                "output": "first output",
                "exit_code": 0,
            },
            {
                "index": 1,
                "provider": "openai",
                "model": "gpt-4",
                "output": "second output",
                "exit_code": 0,
            },
        ]

        with patch("sys.stderr", captured_stderr):
            display_parallel_results("parallel_state", branch_results)

        stderr_text = captured_stderr.getvalue()
        assert "--- branch-1 (claude/sonnet) ---" in stderr_text
        assert "first output" in stderr_text
        assert "--- branch-2 (openai/gpt-4) ---" in stderr_text
        assert "second output" in stderr_text

    def test_displays_failed_branch(self):
        """Failed branches should show FAILED marker in header."""
        captured_stderr = StringIO()

        branch_results = [
            {
                "index": 0,
                "provider": "claude",
                "model": "sonnet",
                "output": "error occurred",
                "exit_code": 1,
            },
        ]

        with patch("sys.stderr", captured_stderr):
            display_parallel_results("parallel_state", branch_results)

        stderr_text = captured_stderr.getvalue()
        assert "--- branch-1 (claude/sonnet) FAILED ---" in stderr_text
        assert "error occurred" in stderr_text

    def test_multiline_output_preserved(self):
        """Multiline output should be preserved in display."""
        captured_stderr = StringIO()

        branch_results = [
            {
                "index": 0,
                "provider": "test",
                "output": "line 1\nline 2\nline 3",
                "exit_code": 0,
            },
        ]

        with patch("sys.stderr", captured_stderr):
            display_parallel_results("parallel_state", branch_results)

        stderr_text = captured_stderr.getvalue()
        assert "line 1" in stderr_text
        assert "line 2" in stderr_text
        assert "line 3" in stderr_text

    def test_empty_output_handled(self):
        """Empty output should not cause issues."""
        captured_stderr = StringIO()

        branch_results = [
            {"index": 0, "provider": "test", "output": "", "exit_code": 0},
        ]

        with patch("sys.stderr", captured_stderr):
            display_parallel_results("parallel_state", branch_results)

        stderr_text = captured_stderr.getvalue()
        assert "--- branch-1 (test) ---" in stderr_text

    def test_ansi_escape_sequences_stripped_from_output(self):
        """Regression: ANSI escape sequences in branch output must be stripped.

        Branch output comes from provider stdout, so attacker-controlled LLM output
        could inject ANSI sequences to spoof prompts, alter terminal state, or
        leak sensitive values into CI/logs.
        """
        ansi_output = "\x1b[31mMalicious\x1b[0m output\x1b]0;spoof title\x07"
        captured_stderr = StringIO()

        branch_results = [
            {"index": 0, "provider": "test", "output": ansi_output, "exit_code": 0},
        ]

        with patch("sys.stderr", captured_stderr):
            display_parallel_results("parallel_state", branch_results)

        stderr_text = captured_stderr.getvalue()
        # ESC byte must not appear in terminal output
        assert "\x1b" not in stderr_text, "ANSI escape in output leaked to terminal"
        # BEL byte must not appear
        assert "\x07" not in stderr_text, "BEL byte leaked to terminal"
        # Visible text content must still be present
        assert "Malicious" in stderr_text
        assert "output" in stderr_text

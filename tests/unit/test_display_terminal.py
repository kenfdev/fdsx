"""Unit tests for T004 display helpers: display_state_escalation,
display_branch_escalation, display_map_iteration_escalation.

These tests will fail with ImportError until the helpers are added to terminal.py.
"""

from io import StringIO
from unittest.mock import patch

from fdsx.display.terminal import (
    display_branch_escalation,
    display_map_iteration_escalation,
    display_state_escalation,
)


class TestDisplayStateEscalation:
    def test_output_contains_required_substrings(self):
        """Line must include arrow, state name, 'escalated to', provider, model, and timestamp."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_state_escalation("step1", "codex", "gpt-4o")
        out = captured.getvalue()
        assert "↑" in out
        assert "step1" in out
        assert "escalated to" in out
        assert "codex" in out
        assert "gpt-4o" in out
        # timestamp bracket like [HH:MM:SS]
        import re

        assert re.search(r"\[\d{2}:\d{2}:\d{2}\]", out), (
            f"no timestamp bracket in {out!r}"
        )

    def test_output_goes_to_stderr_not_stdout(self):
        """Escalation line must go to stderr, not stdout."""
        stdout = StringIO()
        stderr = StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            display_state_escalation("step1", "codex", "gpt-4o")
        assert stdout.getvalue() == ""
        assert stderr.getvalue() != ""

    def test_ansi_sequences_stripped_from_all_string_args(self):
        """ANSI escape codes in state_name, provider, and model must be stripped."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_state_escalation(
                "\x1b[31mstep1\x1b[0m",
                "\x1b[32mcodex\x1b[0m",
                "\x1b[33mgpt-4o\x1b[0m",
            )
        out = captured.getvalue()
        assert "\x1b" not in out, f"ANSI escape leaked to output: {out!r}"
        assert "step1" in out
        assert "codex" in out
        assert "gpt-4o" in out

    def test_model_none_omits_slash_and_model(self):
        """When model is None, no trailing slash or 'None' appears."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_state_escalation("step1", "codex", None)
        out = captured.getvalue()
        assert "codex" in out
        assert "/None" not in out
        assert "None" not in out


class TestDisplayBranchEscalation:
    def test_output_contains_1based_branch_index(self):
        """branch_index 0 → '[branch-1]' in output."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_branch_escalation("par", 0, "codex", "gpt-4o")
        out = captured.getvalue()
        assert "[branch-1]" in out

    def test_output_contains_arrow_and_provider_model(self):
        """Line must include '↑ escalated to codex/gpt-4o'."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_branch_escalation("par", 0, "codex", "gpt-4o")
        out = captured.getvalue()
        assert "↑ escalated to codex/gpt-4o" in out

    def test_second_branch_index_is_2_based(self):
        """branch_index 1 → '[branch-2]' in output."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_branch_escalation("par", 1, "codex", "gpt-4o")
        out = captured.getvalue()
        assert "[branch-2]" in out

    def test_output_goes_to_stderr(self):
        """Branch escalation line goes to stderr, not stdout."""
        stdout = StringIO()
        stderr = StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            display_branch_escalation("par", 0, "codex", "gpt-4o")
        assert stdout.getvalue() == ""
        assert stderr.getvalue() != ""

    def test_model_none_omits_slash_and_model(self):
        """When model is None, output shows provider without trailing slash."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_branch_escalation("par", 0, "codex", None)
        out = captured.getvalue()
        assert "codex" in out
        assert "/None" not in out
        assert "None" not in out


class TestDisplayMapIterationEscalation:
    def test_output_contains_1based_iter_and_total(self):
        """index 2, total 5 → '[iter-3/5]' in output (1-based)."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_map_iteration_escalation("map", 2, 5, "codex", "gpt-4o")
        out = captured.getvalue()
        assert "[iter-3/5]" in out

    def test_output_contains_arrow_and_provider_model(self):
        """Line must include '↑ escalated to codex/gpt-4o'."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_map_iteration_escalation("map", 2, 5, "codex", "gpt-4o")
        out = captured.getvalue()
        assert "↑ escalated to codex/gpt-4o" in out

    def test_first_iteration_is_1_based(self):
        """index 0 → '[iter-1/2]'."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_map_iteration_escalation("map", 0, 2, "codex", "gpt-4o")
        out = captured.getvalue()
        assert "[iter-1/2]" in out

    def test_output_goes_to_stderr(self):
        """Map iteration escalation line goes to stderr, not stdout."""
        stdout = StringIO()
        stderr = StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            display_map_iteration_escalation("map", 0, 2, "codex", "gpt-4o")
        assert stdout.getvalue() == ""
        assert stderr.getvalue() != ""

    def test_model_none_omits_slash_and_model(self):
        """When model is None, output shows provider without trailing slash."""
        captured = StringIO()
        with patch("sys.stderr", captured):
            display_map_iteration_escalation("map", 0, 2, "codex", None)
        out = captured.getvalue()
        assert "codex" in out
        assert "/None" not in out
        assert "None" not in out

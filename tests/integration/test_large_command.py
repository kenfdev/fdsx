"""Integration tests for large command ARG_MAX stdin fallback (T003).

Tests that SystemProvider handles commands exceeding 128KB correctly by
automatically piping via stdin, simulating real workflow conditions where
variable interpolation inflates command size.
"""

from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD
from fdsx.providers.system import SystemProvider


class TestLargeCommandIntegration:
    """Integration tests for large command execution via SystemProvider."""

    def test_system_provider_executes_large_command(self):
        """SystemProvider.execute() succeeds with a command exceeding 128KB."""
        provider = SystemProvider()

        padding = "x" * ARG_MAX_STDIN_THRESHOLD
        large_cmd = f"_large_var={padding}; echo 'workflow_complete'"

        assert len(large_cmd.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD

        result = provider.execute(prompt=large_cmd)

        assert result.exit_code == 0
        assert result.stdout == "workflow_complete"

    def test_system_provider_large_command_via_command_param(self):
        """SystemProvider.execute(command=...) also handles large commands correctly."""
        provider = SystemProvider()

        padding = "x" * ARG_MAX_STDIN_THRESHOLD
        large_cmd = f"_data={padding}; echo 'command_param_ok'"

        result = provider.execute(prompt="ignored", command=large_cmd)

        assert result.exit_code == 0
        assert result.stdout == "command_param_ok"

    def test_large_command_with_pipeline_simulating_variable_interpolation(self):
        """Simulates variable interpolation inflating a command beyond 128KB.

        In real workflows, a large state value (e.g., aggregated parallel review
        outputs) gets substituted into the next task's command template.  This test
        verifies the system provider handles such inflated commands end-to-end.
        """
        provider = SystemProvider()

        # Simulate a large state value being interpolated into the command.
        large_value = "review_output_line\n" * 7000  # ~130KB+ when embedded
        # Embed the large value as a heredoc-style variable, then process it
        large_cmd = f"large_state='{large_value}'; echo \"$large_state\" | wc -l | tr -d ' '"

        if len(large_cmd.encode("utf-8")) < ARG_MAX_STDIN_THRESHOLD:
            # If the constructed command doesn't hit the threshold, pad it
            padding = "x" * (ARG_MAX_STDIN_THRESHOLD - len(large_cmd.encode("utf-8")))
            large_cmd = f"_pad={padding}; {large_cmd}"

        assert len(large_cmd.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD

        result = provider.execute(prompt=large_cmd)

        assert result.exit_code == 0
        # Output should be a number (line count) — confirming shell executed correctly
        assert result.stdout.strip().isdigit()

    def test_large_command_exit_code_propagated(self):
        """Non-zero exit codes from large commands are correctly propagated."""
        provider = SystemProvider()

        padding = "x" * ARG_MAX_STDIN_THRESHOLD
        large_cmd = f"_pad={padding}; exit 7"

        result = provider.execute(prompt=large_cmd)

        assert result.exit_code == 7

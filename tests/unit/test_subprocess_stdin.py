"""Unit tests for _run_subprocess stdin fallback (T001).

Tests verify that commands >= 128KB are piped via stdin to `sh` instead of
passed as argv to `sh -c`, preventing ARG_MAX overflow errors.
"""

from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD, _run_subprocess


class TestSubprocessStdinFallback:
    """Tests for ARG_MAX stdin fallback in _run_subprocess."""

    def test_small_command_uses_sh_c(self):
        """Commands well below 128KB use the normal sh -c path and execute correctly."""
        cmd = "echo hello"
        result = _run_subprocess(args=[cmd], shell=True)

        assert result.exit_code == 0
        assert result.stdout == "hello"

    def test_large_command_uses_stdin_piping(self):
        """Commands >= 128KB are piped via stdin to sh and execute correctly."""
        # Build a command that exceeds ARG_MAX_STDIN_THRESHOLD bytes.
        # Use a variable assignment padding + echo so sh executes it correctly.
        padding = "x" * ARG_MAX_STDIN_THRESHOLD
        cmd = f"_pad={padding}; echo large_ok"

        assert len(cmd.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD

        result = _run_subprocess(args=[cmd], shell=True)

        assert result.exit_code == 0
        assert result.stdout == "large_ok"

    def test_stdin_fallback_produces_identical_output(self):
        """Small and large command paths produce identical stdout/stderr/exit_code for equivalent logic."""
        # Small version
        small_cmd = "echo same_output"
        small_result = _run_subprocess(args=[small_cmd], shell=True)

        # Large version: same logic, padded to exceed threshold
        padding = "x" * ARG_MAX_STDIN_THRESHOLD
        large_cmd = f"_pad={padding}; echo same_output"
        large_result = _run_subprocess(args=[large_cmd], shell=True)

        assert small_result.exit_code == large_result.exit_code
        assert small_result.stdout == large_result.stdout

    def test_stdin_fallback_preserves_shell_features(self):
        """Pipes, variable substitution, and subshells work via stdin fallback."""
        padding = "x" * ARG_MAX_STDIN_THRESHOLD
        # Test pipes
        cmd_pipe = f"_pad={padding}; echo 'hello world' | tr ' ' '_'"
        result_pipe = _run_subprocess(args=[cmd_pipe], shell=True)
        assert result_pipe.exit_code == 0
        assert result_pipe.stdout == "hello_world"

        # Test variable substitution
        cmd_var = f"_pad={padding}; MY_VAR=42; echo $MY_VAR"
        result_var = _run_subprocess(args=[cmd_var], shell=True)
        assert result_var.exit_code == 0
        assert result_var.stdout == "42"

        # Test subshell
        cmd_sub = f"_pad={padding}; echo $(echo subshell_works)"
        result_sub = _run_subprocess(args=[cmd_sub], shell=True)
        assert result_sub.exit_code == 0
        assert result_sub.stdout == "subshell_works"

    def test_boundary_exactly_128kb(self):
        """Command of exactly 131,072 bytes triggers stdin fallback."""
        # Construct a command that is exactly ARG_MAX_STDIN_THRESHOLD bytes.
        # Format: "_pad=<padding>; echo boundary_ok"
        prefix = "_pad="
        suffix = "; echo boundary_ok"
        padding_len = ARG_MAX_STDIN_THRESHOLD - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
        cmd = prefix + ("x" * padding_len) + suffix

        assert len(cmd.encode("utf-8")) == ARG_MAX_STDIN_THRESHOLD

        result = _run_subprocess(args=[cmd], shell=True)

        assert result.exit_code == 0
        assert result.stdout == "boundary_ok"

    def test_command_just_below_threshold_uses_sh_c(self):
        """Command of 131,071 bytes (threshold - 1) uses sh -c, not stdin."""
        # Construct a command that is exactly one byte below threshold.
        prefix = "_pad="
        suffix = "; echo below_ok"
        padding_len = ARG_MAX_STDIN_THRESHOLD - 1 - len(prefix.encode("utf-8")) - len(suffix.encode("utf-8"))
        cmd = prefix + ("x" * padding_len) + suffix

        assert len(cmd.encode("utf-8")) == ARG_MAX_STDIN_THRESHOLD - 1

        result = _run_subprocess(args=[cmd], shell=True)

        assert result.exit_code == 0
        assert result.stdout == "below_ok"

    def test_exit_code_preserved_via_stdin_fallback(self):
        """Non-zero exit codes are correctly preserved when using stdin fallback."""
        padding = "x" * ARG_MAX_STDIN_THRESHOLD
        cmd = f"_pad={padding}; exit 42"
        result = _run_subprocess(args=[cmd], shell=True)

        assert result.exit_code == 42

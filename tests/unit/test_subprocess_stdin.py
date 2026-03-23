"""Unit tests for _run_subprocess stdin fallback and stderr_callback (T001, T008).

Tests verify that commands >= 128KB are piped via stdin to `sh` instead of
passed as argv to `sh -c`, preventing ARG_MAX overflow errors.

T008 tests verify that stderr lines are streamed line-by-line via stderr_callback.
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
        padding_len = (
            ARG_MAX_STDIN_THRESHOLD
            - len(prefix.encode("utf-8"))
            - len(suffix.encode("utf-8"))
        )
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
        padding_len = (
            ARG_MAX_STDIN_THRESHOLD
            - 1
            - len(prefix.encode("utf-8"))
            - len(suffix.encode("utf-8"))
        )
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


class TestStderrCallback:
    """Tests for stderr_callback line-by-line streaming (T008)."""

    def test_stderr_callback_receives_lines(self):
        """stderr_callback is called once per stderr line."""
        received: list[str] = []

        result = _run_subprocess(
            args=["echo errline >&2"],
            shell=True,
            stderr_callback=received.append,
        )

        assert result.exit_code == 0
        assert "errline" in received

    def test_stderr_callback_multiple_lines(self):
        """Each stderr line triggers a separate callback invocation."""
        received: list[str] = []

        result = _run_subprocess(
            args=["echo line1 >&2; echo line2 >&2"],
            shell=True,
            stderr_callback=received.append,
        )

        assert result.exit_code == 0
        assert "line1" in received
        assert "line2" in received

    def test_stderr_still_in_result_with_callback(self):
        """ProviderResult.stderr still contains full stderr even when callback is used."""
        received: list[str] = []

        result = _run_subprocess(
            args=["echo captured >&2"],
            shell=True,
            stderr_callback=received.append,
        )

        assert result.exit_code == 0
        assert "captured" in result.stderr
        assert "captured" in received

    def test_stderr_callback_none_does_not_raise(self):
        """Passing stderr_callback=None (default) works without errors."""
        result = _run_subprocess(
            args=["echo no_callback >&2"],
            shell=True,
            stderr_callback=None,
        )

        assert result.exit_code == 0
        assert "no_callback" in result.stderr

    def test_stderr_lines_have_no_trailing_newline(self):
        """Lines delivered to stderr_callback do not include trailing newline."""
        received: list[str] = []

        _run_subprocess(
            args=["printf 'line_a\\nline_b\\n' >&2"],
            shell=True,
            stderr_callback=received.append,
        )

        for line in received:
            assert not line.endswith("\n"), f"Line has trailing newline: {line!r}"

    def test_stdout_and_stderr_callbacks_independent(self):
        """stdout and stderr callbacks are called independently."""
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        result = _run_subprocess(
            args=["echo stdout_line; echo stderr_line >&2"],
            shell=True,
            output_callback=stdout_lines.append,
            stderr_callback=stderr_lines.append,
        )

        assert result.exit_code == 0
        assert "stdout_line" in stdout_lines
        assert "stderr_line" in stderr_lines
        # Lines should not cross-contaminate
        assert not any("stderr_line" in line for line in stdout_lines)
        assert not any("stdout_line" in line for line in stderr_lines)

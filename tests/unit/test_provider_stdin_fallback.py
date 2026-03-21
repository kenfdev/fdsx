"""Unit tests for stdin fallback behaviour in CLI providers.

Tests verify that prompts at or above ARG_MAX_STDIN_THRESHOLD are piped via
stdin instead of being passed as a command-line argument, avoiding
"Argument list too long" (ENOMSG/E2BIG) errors.
"""

from unittest.mock import patch

from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD, ProviderResult
from fdsx.providers.claude import ClaudeOptions, ClaudeProvider
from fdsx.providers.codex import CodexOptions, CodexProvider
from fdsx.providers.opencode import OpenCodeProvider

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

SMALL_PROMPT = "hello world"
"""A prompt well below the ARG_MAX_STDIN_THRESHOLD."""

LARGE_PROMPT = "x" * ARG_MAX_STDIN_THRESHOLD
"""A prompt exactly at the ARG_MAX_STDIN_THRESHOLD (boundary / >= case)."""

ABOVE_THRESHOLD_PROMPT = "x" * (ARG_MAX_STDIN_THRESHOLD + 1)
"""A prompt one byte above the ARG_MAX_STDIN_THRESHOLD."""

FAKE_SUCCESS = ProviderResult(exit_code=0, stdout="ok", stderr="")
"""Reusable successful ProviderResult for use as mock return value."""


# ---------------------------------------------------------------------------
# Test classes (populated in T002–T004)
# ---------------------------------------------------------------------------


class TestClaudeStdinFallback:
    """T002: stdin fallback tests for ClaudeProvider."""

    def test_small_prompt_uses_args(self) -> None:
        """T01: prompt < threshold → prompt in args, stdin_data=None."""
        provider = ClaudeProvider()
        with patch("fdsx.providers.claude._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(SMALL_PROMPT)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert SMALL_PROMPT in kwargs["args"]
        assert kwargs.get("stdin_data") is None

    def test_large_prompt_uses_stdin(self) -> None:
        """T02: prompt above threshold → prompt NOT in args, stdin_data=prompt."""
        provider = ClaudeProvider()
        with patch("fdsx.providers.claude._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(ABOVE_THRESHOLD_PROMPT)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert ABOVE_THRESHOLD_PROMPT not in kwargs["args"]
        assert kwargs.get("stdin_data") == ABOVE_THRESHOLD_PROMPT

    def test_large_prompt_with_flags_keeps_flags_in_args(self) -> None:
        """T03: prompt >= threshold + flags → flags in args, prompt NOT in args, stdin_data=prompt."""
        options = ClaudeOptions(dangerously_skip_permissions=True)
        provider = ClaudeProvider(options)
        with patch("fdsx.providers.claude._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(ABOVE_THRESHOLD_PROMPT)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert ABOVE_THRESHOLD_PROMPT not in kwargs["args"]
        assert "--dangerously-skip-permissions" in kwargs["args"]
        assert kwargs.get("stdin_data") == ABOVE_THRESHOLD_PROMPT

    def test_prompt_at_threshold_uses_stdin(self) -> None:
        """T10: prompt exactly at threshold → uses stdin (boundary case)."""
        provider = ClaudeProvider()
        with patch("fdsx.providers.claude._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(LARGE_PROMPT)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert LARGE_PROMPT not in kwargs["args"]
        assert kwargs.get("stdin_data") == LARGE_PROMPT


class TestCodexStdinFallback:
    """T003: stdin fallback tests for CodexProvider."""

    def test_small_prompt_uses_args(self) -> None:
        """T04: prompt < threshold → prompt in args, stdin_data=None."""
        provider = CodexProvider()
        with patch("fdsx.providers.codex._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(SMALL_PROMPT)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert SMALL_PROMPT in kwargs["args"]
        assert kwargs.get("stdin_data") is None

    def test_large_prompt_uses_stdin(self) -> None:
        """T05: prompt >= threshold → prompt NOT in args, stdin_data=prompt."""
        provider = CodexProvider()
        with patch("fdsx.providers.codex._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(ABOVE_THRESHOLD_PROMPT)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert ABOVE_THRESHOLD_PROMPT not in kwargs["args"]
        assert kwargs.get("stdin_data") == ABOVE_THRESHOLD_PROMPT

    def test_large_prompt_with_flags_keeps_flags_in_args(self) -> None:
        """T06: prompt >= threshold + flags → flags in args, prompt NOT in args, stdin_data=prompt."""
        options = CodexOptions(full_auto=True)
        provider = CodexProvider(options)
        with patch("fdsx.providers.codex._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(ABOVE_THRESHOLD_PROMPT)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert ABOVE_THRESHOLD_PROMPT not in kwargs["args"]
        assert "--full-auto" in kwargs["args"]
        assert kwargs.get("stdin_data") == ABOVE_THRESHOLD_PROMPT


class TestOpenCodeStdinFallback:
    """T004: stdin fallback tests for OpenCodeProvider."""

    def test_small_prompt_uses_args(self) -> None:
        """T07: prompt < threshold → prompt in args, stdin_data=None."""
        provider = OpenCodeProvider()
        with patch("fdsx.providers.opencode._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(SMALL_PROMPT)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert SMALL_PROMPT in kwargs["args"]
        assert kwargs.get("stdin_data") is None

    def test_large_prompt_uses_stdin(self) -> None:
        """T08: prompt >= threshold → prompt NOT in args, stdin_data=prompt."""
        provider = OpenCodeProvider()
        with patch("fdsx.providers.opencode._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(ABOVE_THRESHOLD_PROMPT)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert ABOVE_THRESHOLD_PROMPT not in kwargs["args"]
        assert kwargs.get("stdin_data") == ABOVE_THRESHOLD_PROMPT

    def test_large_prompt_with_model_flag_keeps_model_in_args(self) -> None:
        """T09: prompt >= threshold + model flag → `-m model` in args, prompt NOT in args, stdin_data=prompt."""
        model = "gpt-4o"
        provider = OpenCodeProvider()
        with patch("fdsx.providers.opencode._run_subprocess", return_value=FAKE_SUCCESS) as mock_run:
            provider.execute(ABOVE_THRESHOLD_PROMPT, model=model)
        mock_run.assert_called_once()
        kwargs = mock_run.call_args.kwargs
        assert ABOVE_THRESHOLD_PROMPT not in kwargs["args"]
        assert "-m" in kwargs["args"]
        assert model in kwargs["args"]
        assert kwargs.get("stdin_data") == ABOVE_THRESHOLD_PROMPT

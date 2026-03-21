"""Unit tests for stdin fallback behaviour in CLI providers.

Tests verify that prompts at or above ARG_MAX_STDIN_THRESHOLD are piped via
stdin instead of being passed as a command-line argument, avoiding
"Argument list too long" (ENOMSG/E2BIG) errors.
"""

from unittest.mock import patch

from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD, ProviderResult
from fdsx.providers.claude import ClaudeOptions, ClaudeProvider
from fdsx.providers.codex import CodexOptions, CodexProvider
from fdsx.providers.opencode import OpenCodeOptions, OpenCodeProvider

# ---------------------------------------------------------------------------
# Shared test constants
# ---------------------------------------------------------------------------

SMALL_PROMPT = "hello world"
"""A prompt well below the ARG_MAX_STDIN_THRESHOLD."""

LARGE_PROMPT = "x" * ARG_MAX_STDIN_THRESHOLD
"""A prompt exactly at the ARG_MAX_STDIN_THRESHOLD (boundary / >= case)."""

FAKE_SUCCESS = ProviderResult(exit_code=0, stdout="ok", stderr="")
"""Reusable successful ProviderResult for use as mock return value."""


# ---------------------------------------------------------------------------
# Test classes (populated in T002–T004)
# ---------------------------------------------------------------------------


class TestClaudeStdinFallback:
    """T002: stdin fallback tests for ClaudeProvider."""

    pass


class TestCodexStdinFallback:
    """T003: stdin fallback tests for CodexProvider."""

    pass


class TestOpenCodeStdinFallback:
    """T004: stdin fallback tests for OpenCodeProvider."""

    pass

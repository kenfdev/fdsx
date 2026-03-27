"""Shared model validators for fdsx.

Centralised provider validation to prevent cross-module drift between
config.py and flow.py.
"""

from __future__ import annotations

import re

VALID_PROVIDERS = frozenset({"claude", "opencode", "codex", "gemini"})
PROFILE_NAME_REGEX = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")


def validate_llm_provider(v: str, context: str) -> str:
    """Validate that a provider name is one of the known LLM providers.

    Args:
        v: Provider name to validate.
        context: Human-readable context label used in the error message.

    Returns:
        The validated provider name (unchanged).

    Raises:
        ValueError: If v is not a recognised provider.
    """
    if v not in VALID_PROVIDERS:
        raise ValueError(
            f"{context} provider must be one of "
            f"{', '.join(sorted(VALID_PROVIDERS))}, got '{v}'"
        )
    return v


def validate_profile_name(name: str) -> str:
    """Validate that a profile name matches the allowed pattern.

    Args:
        name: Profile name to validate.

    Returns:
        The validated profile name (unchanged).

    Raises:
        ValueError: If the name doesn't match the pattern.
    """
    if not PROFILE_NAME_REGEX.match(name):
        raise ValueError(
            f"profile name must start with a letter and contain only "
            f"letters, numbers, underscores, or hyphens, got '{name}'"
        )
    return name

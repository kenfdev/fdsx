"""Shared model validators for fdsx.

Centralised provider validation to prevent cross-module drift between
config.py and flow.py.
"""

from __future__ import annotations

VALID_PROVIDERS = frozenset({"claude", "opencode", "codex"})


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

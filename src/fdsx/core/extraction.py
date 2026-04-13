import json
import re
from collections.abc import Callable
from typing import Any

import structlog

from fdsx.models.flow import ExtractRule, LLMClassifyFallback

log = structlog.get_logger(__name__)


def extract_value(
    output: str,
    extract_rule: ExtractRule,
    provider_factory: Callable[[str], Any] | None = None,
    state_dict: dict[str, Any] | None = None,
    source_provider: str | None = None,
) -> Any | None:
    """Extract a value from output using the specified extraction rule.

    Args:
        output: The raw output string to extract from
        extract_rule: The extraction rule defining strategies and fallback
        provider_factory: Optional factory to create LLM providers (for fallback)
        state_dict: Optional state dictionary (reserved for future use)
        source_provider: The provider that generated the output. When "system",
            LLM fallback is suppressed to prevent exfiltration of local command output.

    Returns:
        The extracted value as a string, or None if extraction failed
    """
    failures: list[dict[str, str]] = []
    for strategy_name in extract_rule.strategy:
        result = _execute_strategy(strategy_name, output, extract_rule.pattern)
        if result is not None:
            return result
        failures.append(
            {
                "strategy": strategy_name,
                "reason": _get_failure_reason(strategy_name, output, extract_rule.pattern),
            }
        )

    log.warning(
        "extraction_failed",
        strategies_tried=failures,
        output_preview=output[:500],
    )

    if extract_rule.fallback is not None:
        if source_provider == "system":
            return None
        # Only validate LLM output against pattern when keyword strategy is
        # configured, because only keyword patterns are pipe-delimited allowlists.
        # For json (field name) and regex (regex pattern), pattern has different semantics.
        validation_pattern = (
            extract_rule.pattern if "keyword" in extract_rule.strategy else None
        )
        return _execute_llm_fallback(
            output, extract_rule.fallback, provider_factory, pattern=validation_pattern
        )

    return None


def _execute_strategy(strategy_name: str, output: str, pattern: str) -> Any | None:
    """Execute a single extraction strategy.

    Args:
        strategy_name: The name of the strategy (json, regex, keyword)
        output: The output to extract from
        pattern: The pattern to use for extraction

    Returns:
        The extracted value or None if extraction failed
    """
    strategies = {
        "json": _json_strategy,
        "regex": _regex_strategy,
        "keyword": _keyword_strategy,
    }

    strategy_func = strategies.get(strategy_name)
    if strategy_func is None:
        return None

    return strategy_func(output, pattern)


def _get_failure_reason(strategy_name: str, output: str, pattern: str) -> str:
    """Return a human-readable reason why a strategy returned None.

    Args:
        strategy_name: The name of the strategy (json, regex, keyword)
        output: The output that was searched
        pattern: The pattern used for extraction

    Returns:
        A short description of why the strategy failed
    """
    if strategy_name == "json":
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", output)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                if _get_nested_value(data, pattern) is None:
                    return f"missing key '{pattern}'"
            except json.JSONDecodeError:
                return "invalid JSON"
        try:
            data = json.loads(output)
            if _get_nested_value(data, pattern) is None:
                return f"missing key '{pattern}'"
        except json.JSONDecodeError:
            return "invalid JSON"
        return f"missing key '{pattern}'"
    elif strategy_name == "regex":
        try:
            re.compile(pattern)
        except re.error:
            return "regex compile error"
        return "no match"
    elif strategy_name == "keyword":
        return "no match"
    else:
        return "unknown strategy"


def _json_strategy(output: str, pattern: str) -> Any | None:
    """Extract a value from JSON in the output.

    First tries to find a JSON code block, then tries parsing the entire output as JSON.
    Uses exact key lookup only (dot notation for nested paths).

    Args:
        output: The output string containing JSON
        pattern: The field name to extract (dot-separated path, e.g., "result.status")

    Returns:
        The extracted field value as a string, or None if not found
    """
    json_match = re.search(r"```json\s*([\s\S]*?)\s*```", output)
    if json_match:
        json_str = json_match.group(1)
        try:
            data = json.loads(json_str)
            value = _get_nested_value(data, pattern)
            if value is not None:
                return value
        except json.JSONDecodeError:
            pass

    try:
        data = json.loads(output)
        value = _get_nested_value(data, pattern)
        if value is not None:
            return value
    except json.JSONDecodeError:
        pass

    return None


def _get_nested_value(data: dict[str, Any] | list[Any], path: str) -> Any:
    """Get a nested value from a dict using dot notation.

    Args:
        data: The dictionary to search
        path: The dot-separated path (e.g., "result.status")

    Returns:
        The value at the path, or None if not found
    """
    if not path:
        return None
    path = path.lstrip(".")
    if not path:  # was "." (or "..") — return root
        return data
    keys = path.split(".")
    current: Any = data

    for key in keys:
        if isinstance(current, dict):
            if key in current:
                current = current[key]
            else:
                return None
        elif isinstance(current, list):
            try:
                index = int(key)
                if 0 <= index < len(current):
                    current = current[index]
                else:
                    return None
            except ValueError:
                return None
        else:
            return None

    return current


def _regex_strategy(output: str, pattern: str) -> str | None:
    """Extract a value using regex pattern.

    Args:
        output: The output string to search
        pattern: The regex pattern to match

    Returns:
        The first capture group if present, otherwise the full match, or None if no match
    """
    try:
        match = re.search(pattern, output)
    except re.error:
        return None
    if match is None:
        return None

    if match.groups():
        return match.group(1)

    return match.group(0)


def _keyword_strategy(output: str, pattern: str) -> str | None:
    """Extract a keyword from output (case-insensitive, word-boundary).

    Splits the pattern by '|' to get a list of keywords, then searches
    for each keyword case-insensitively using word boundaries in the output.
    Returns the keyword whose occurrence appears latest in the output
    (with original case from pattern).

    Args:
        output: The output string to search
        pattern: The pipe-delimited keywords (e.g., "APPROVED|REJECTED")

    Returns:
        The matched keyword from the pattern (with original casing), or None if not found
    """
    keywords = pattern.split("|")
    output_lower = output.lower()

    latest_match = None
    latest_pos = -1

    for keyword in keywords:
        keyword_lower = keyword.lower()
        pattern_escaped = re.escape(keyword_lower)
        for match in re.finditer(r"\b" + pattern_escaped + r"\b", output_lower):
            if match.start() > latest_pos:
                latest_pos = match.start()
                latest_match = keyword

    return latest_match


def _execute_llm_fallback(
    output: str,
    fallback: LLMClassifyFallback,
    provider_factory: Callable[[str], Any] | None,
    pattern: str | None = None,
) -> str | None:
    """Execute LLM-based classification fallback.

    Args:
        output: The raw output to classify
        fallback: The LLM fallback configuration
        provider_factory: Optional factory to create LLM providers
        pattern: Optional pipe-delimited keywords to validate LLM output against.
                 If provided, the LLM response must exactly match one of the keywords
                 (case-insensitive); unrecognised responses are rejected.

    Returns:
        The classification result, or None if the LLM call failed or the result
        is not in the allowed set.
    """
    if provider_factory is None:
        return None
    if fallback.provider == "system":
        return None  # system provider not allowed for LLM classification (defense-in-depth)

    try:
        provider = provider_factory(fallback.provider)
    except Exception:
        return None

    prompt = fallback.prompt.replace("{output}", output)

    try:
        from fdsx.providers.base import ProviderResult

        result: ProviderResult = provider.execute(
            prompt=prompt,
            model=None,
            timeout=None,
            output_callback=None,
        )

        if result.exit_code == 0:
            llm_output = result.stdout.strip()
            if pattern:
                keywords = pattern.split("|")
                llm_lower = llm_output.lower()
                for keyword in keywords:
                    if keyword.lower() == llm_lower:
                        return keyword
                return None  # LLM output not in allowed set
            return llm_output

    except Exception:
        pass

    return None

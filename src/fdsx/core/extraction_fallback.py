from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog

from fdsx.models.flow import ExtractionFallback, ExtractRule, LLMClassifyFallback

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig
    from fdsx.models.flow import Flow

log = structlog.get_logger(__name__)

__all__ = [
    "FallbackEvent",
    "ResolvedFallback",
    "execute_default_fallback",
    "resolve_fallback",
]


@dataclass(frozen=True)
class FallbackEvent:
    source: str
    outcome: str
    state_name: str
    pattern: str
    value_preview: str | None = None
    error_kind: str | None = None
    branch_index: int | None = None
    iter_index: int | None = None


@dataclass(frozen=True)
class ResolvedFallback:
    config: LLMClassifyFallback | ExtractionFallback
    source: Literal["rule", "workflow", "global"]


def resolve_fallback(
    rule: ExtractRule,
    flow: Flow,
    config: FdsxConfig,
) -> ResolvedFallback | None:
    if rule.fallback is not None:
        log.debug("fallback_resolved", source="rule")
        return ResolvedFallback(config=rule.fallback, source="rule")

    ef = flow.extraction_fallback
    if ef is False:
        log.debug("fallback_resolved", source=None, reason="workflow_disabled")
        return None
    if ef is not None:
        log.debug("fallback_resolved", source="workflow")
        return ResolvedFallback(config=ef, source="workflow")

    if config.extraction_fallback is not None:
        log.debug("fallback_resolved", source="global")
        return ResolvedFallback(config=config.extraction_fallback, source="global")

    log.debug("fallback_resolved", source=None, reason="none_configured")
    return None


def _build_default_fallback_prompt(
    output: str,
    rule: ExtractRule,
    extra_instructions: str | None,
) -> str:
    strategies = ", ".join(rule.strategy)

    if "keyword" in rule.strategy:
        allowed = " | ".join(rule.pattern.split("|"))
        rules_block = (
            f"2. The token MUST be one of the following allowed values, exactly as listed\n"
            f"   (case will be normalised): {allowed}\n"
            f"   If none of the allowed values is supported by the output, respond with the\n"
            f"   literal token NONE."
        )
    else:
        rules_block = (
            "2. If a value matching the requested shape can be recovered from the output,\n"
            "   respond with that value alone. If no value can be recovered, respond with the\n"
            "   literal token NONE."
        )

    extra_section = (
        f"ADDITIONAL INSTRUCTIONS:\n{extra_instructions}\n\n"
        if extra_instructions is not None
        else ""
    )

    return (
        f"You are a recovery extractor. A workflow tried to extract a value from a tool's\n"
        f"output and every configured strategy missed. Your job is to recover the intended\n"
        f"value or report that no value can be recovered.\n\n"
        f"CONTEXT:\n"
        f"STRATEGIES ATTEMPTED: {strategies}\n"
        f"PATTERN: {rule.pattern}\n\n"
        f"OUTPUT:\n"
        f"{output}\n\n"
        f"RULES:\n"
        f"1. Respond with exactly one token. Do not include explanations, commentary, code\n"
        f"   fences, or whitespace beyond the token itself.\n"
        f"{rules_block}\n\n"
        f"EXAMPLES:\n"
        f"Example 1 — value recovered from noisy output:\n"
        f"Output: The system reviewed the request. Decision: approved. Proceeding.\n"
        f"Response: APPROVED\n\n"
        f"Example 2 — value not present in output:\n"
        f"Output: Process initiated. Awaiting further instructions from operator.\n"
        f"Response: NONE\n\n"
        f"OUTPUT FORMAT: a single line containing exactly one token from the allowed set,\n"
        f"the recovered value, or NONE. No prose, no markdown, no quoting.\n\n"
        f"{extra_section}"
    )


def execute_default_fallback(
    output: str,
    rule: ExtractRule,
    resolved: ResolvedFallback,
    merged_profiles: dict[str, dict[str, Any]],
    source_provider: str,
    provider_factory: Callable[[str], Any],
    on_fallback: Callable[[FallbackEvent], None] | None = None,
    state_name: str = "",
) -> str | None:
    config = cast(ExtractionFallback, resolved.config)

    if config.provider is not None:
        provider_name = config.provider
        model = None
    else:
        profile_name = cast(str, config.profile)
        profile_data = merged_profiles.get(profile_name)
        if profile_data is None:
            log.info(
                "extraction_fallback_invoked",
                source=resolved.source,
                strategy_list=rule.strategy,
                outcome="error",
                error="profile_not_found",
            )
            event = FallbackEvent(
                source=resolved.source,
                outcome="error",
                state_name=state_name,
                pattern=rule.pattern,
                error_kind="profile_not_found",
            )
            if on_fallback:
                on_fallback(event)
            return None
        provider_name = profile_data["provider"]
        model = profile_data.get("model")

    prompt = _build_default_fallback_prompt(output, rule, config.extra_instructions)
    log.debug("default_fallback_prompt", prompt_len=len(prompt))

    try:
        provider = provider_factory(provider_name)
    except Exception:
        log.info(
            "extraction_fallback_invoked",
            source=resolved.source,
            strategy_list=rule.strategy,
            outcome="error",
            error="provider_init_failed",
        )
        event = FallbackEvent(
            source=resolved.source,
            outcome="error",
            state_name=state_name,
            pattern=rule.pattern,
            error_kind="provider_init_failed",
        )
        if on_fallback:
            on_fallback(event)
        return None

    try:
        result = provider.execute(
            prompt=prompt, model=model, timeout=None, output_callback=None
        )
    except (TimeoutError, subprocess.TimeoutExpired):
        log.info(
            "extraction_fallback_invoked",
            source=resolved.source,
            strategy_list=rule.strategy,
            outcome="error",
            error="timeout",
        )
        event = FallbackEvent(
            source=resolved.source,
            outcome="error",
            state_name=state_name,
            pattern=rule.pattern,
            error_kind="timeout",
        )
        if on_fallback:
            on_fallback(event)
        return None
    except Exception:
        log.info(
            "extraction_fallback_invoked",
            source=resolved.source,
            strategy_list=rule.strategy,
            outcome="error",
            error="provider_call_failed",
        )
        event = FallbackEvent(
            source=resolved.source,
            outcome="error",
            state_name=state_name,
            pattern=rule.pattern,
            error_kind="provider_call_failed",
        )
        if on_fallback:
            on_fallback(event)
        return None

    if result.exit_code != 0:
        log.info(
            "extraction_fallback_invoked",
            source=resolved.source,
            strategy_list=rule.strategy,
            outcome="error",
            error="non_zero_exit",
        )
        event = FallbackEvent(
            source=resolved.source,
            outcome="error",
            state_name=state_name,
            pattern=rule.pattern,
            error_kind="non_zero_exit",
        )
        if on_fallback:
            on_fallback(event)
        return None

    llm_output: str = result.stdout.strip()
    log.debug("default_fallback_raw_response", response_len=len(llm_output))

    if llm_output == "NONE":
        log.info(
            "extraction_fallback_invoked",
            source=resolved.source,
            strategy_list=rule.strategy,
            outcome="rejected",
            reason="model_returned_none",
        )
        event = FallbackEvent(
            source=resolved.source,
            outcome="rejected",
            state_name=state_name,
            pattern=rule.pattern,
            value_preview=llm_output,
        )
        if on_fallback:
            on_fallback(event)
        return None

    if "keyword" in rule.strategy:
        keywords = rule.pattern.split("|")
        llm_lower = llm_output.lower()
        for keyword in keywords:
            if keyword.lower() == llm_lower:
                log.info(
                    "extraction_fallback_invoked",
                    source=resolved.source,
                    strategy_list=rule.strategy,
                    outcome="recovered",
                )
                event = FallbackEvent(
                    source=resolved.source,
                    outcome="recovered",
                    state_name=state_name,
                    pattern=rule.pattern,
                    value_preview=keyword,
                )
                if on_fallback:
                    on_fallback(event)
                return keyword
        log.info(
            "extraction_fallback_invoked",
            source=resolved.source,
            strategy_list=rule.strategy,
            outcome="rejected",
        )
        event = FallbackEvent(
            source=resolved.source,
            outcome="rejected",
            state_name=state_name,
            pattern=rule.pattern,
            value_preview=llm_output,
        )
        if on_fallback:
            on_fallback(event)
        return None

    log.info(
        "extraction_fallback_invoked",
        source=resolved.source,
        strategy_list=rule.strategy,
        outcome="recovered",
    )
    event = FallbackEvent(
        source=resolved.source,
        outcome="recovered",
        state_name=state_name,
        pattern=rule.pattern,
        value_preview=llm_output,
    )
    if on_fallback:
        on_fallback(event)
    return llm_output

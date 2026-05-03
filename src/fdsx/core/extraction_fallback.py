from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog

from fdsx.models.flow import ExtractionFallback, ExtractRule, LLMClassifyFallback

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig
    from fdsx.models.flow import Flow

log = structlog.get_logger(__name__)

__all__ = ["ResolvedFallback", "resolve_fallback"]


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

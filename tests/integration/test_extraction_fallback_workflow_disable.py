"""Integration tests for per-workflow extraction_fallback disable (T004)."""

from unittest.mock import patch

import pytest

from fdsx.core.compiler import compile_flow
from fdsx.core.config import FdsxConfig
from fdsx.models.flow import (
    ExtractionFallback,
    ExtractRule,
    Flow,
    LLMClassifyFallback,
    TaskState,
)
from fdsx.providers.base import ProviderResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL = "claude-sonnet-4-5"
_CODEX_MODEL = "gpt-4o"

_NO_MATCH = ProviderResult(exit_code=0, stdout="processing complete", stderr="")
_APPROVED = ProviderResult(exit_code=0, stdout="APPROVED", stderr="")


def _flow(
    extraction_fallback: ExtractionFallback | bool | None = None,
    per_rule_fallback: LLMClassifyFallback | None = None,
    profiles: dict | None = None,
) -> Flow:
    """Single claude task with keyword extraction that always misses."""
    ef = False if extraction_fallback is False else extraction_fallback

    return Flow(
        name="test_workflow_disable",
        description="Test per-workflow extraction_fallback disable",
        start_at="step1",
        profiles=profiles,
        extraction_fallback=ef,  # type: ignore[arg-type]
        states={
            "step1": TaskState(
                type="task",
                provider="claude",
                model=_MODEL,
                prompt_template="classify the output",
                result_path="$.task_result",
                extract=ExtractRule(
                    strategy=["keyword"],
                    pattern="APPROVED|REJECTED",
                    result_path="$.decision",
                    fallback=per_rule_fallback,
                ),
                retry=0,
                end=True,
            )
        },
    )


# ---------------------------------------------------------------------------
# T004-1: flow.extraction_fallback = false disables config-level default
# ---------------------------------------------------------------------------


class TestDisableSuppressesGlobalDefault:
    def test_false_disables_config_fallback(self):
        flow = _flow(extraction_fallback=False)
        config = FdsxConfig(
            extraction_fallback=ExtractionFallback(
                provider="claude", model="claude-sonnet-4-6"
            )
        )

        with patch(
            "fdsx.providers.claude._run_subprocess",
            return_value=_NO_MATCH,
        ) as mock_claude:
            compiled = compile_flow(flow, config=config)
            with pytest.raises(RuntimeError, match="Extraction failed"):
                compiled.graph.invoke({})

        assert mock_claude.call_count == 1


# ---------------------------------------------------------------------------
# T004-2: Per-rule explicit fallback fires even when workflow disables
# ---------------------------------------------------------------------------


class TestPerRuleStillFiresWhenDisabled:
    def test_per_rule_fallback_fires_despite_workflow_disable(self):
        flow = _flow(
            extraction_fallback=False,
            per_rule_fallback=LLMClassifyFallback(
                provider="claude",
                model="claude-sonnet-4-6",
                prompt="Classify as APPROVED or REJECTED: {output}",
            ),
        )
        config = FdsxConfig(
            extraction_fallback=ExtractionFallback(
                provider="codex", model="claude-sonnet-4-6"
            )
        )

        claude_responses = [_NO_MATCH, _APPROVED]

        with (
            patch(
                "fdsx.providers.claude._run_subprocess",
                side_effect=claude_responses,
            ) as mock_claude,
            patch("fdsx.providers.codex._run_subprocess") as mock_codex,
        ):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"
        assert mock_claude.call_count == 2
        mock_codex.assert_not_called()


# ---------------------------------------------------------------------------
# T004-3: Removing the disable restores inheritance of the config default
# ---------------------------------------------------------------------------


class TestRemovingDisableRestoresInheritance:
    def test_toggling_disable_off_restores_global_default(self):
        config = FdsxConfig(
            extraction_fallback=ExtractionFallback(
                provider="claude", model="claude-sonnet-4-6"
            )
        )

        # Phase A — disabled: extraction fails
        with patch(
            "fdsx.providers.claude._run_subprocess",
            return_value=_NO_MATCH,
        ) as mock_claude:
            compiled = compile_flow(_flow(extraction_fallback=False), config=config)
            with pytest.raises(RuntimeError, match="Extraction failed"):
                compiled.graph.invoke({})
        assert mock_claude.call_count == 1

        # Phase B — disable removed: global default fires
        with patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=[_NO_MATCH, _APPROVED],
        ) as mock_claude:
            compiled = compile_flow(_flow(extraction_fallback=None), config=config)
            result = compiled.graph.invoke({})
        assert result.get("decision") == "APPROVED"
        assert mock_claude.call_count == 2

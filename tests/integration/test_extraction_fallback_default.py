"""Integration tests for global extraction_fallback default (T002).

Tests verify the five behaviors of the global extraction_fallback config field
when all extraction strategies miss on a task state's output.
"""

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


def _claude_task_flow(extra_extract_kwargs: dict | None = None) -> Flow:
    """Single claude task (retry=0) with keyword extraction."""
    extract_kwargs: dict = dict(
        strategy=["keyword"],
        pattern="APPROVED|REJECTED",
        result_path="$.decision",
    )
    if extra_extract_kwargs:
        extract_kwargs.update(extra_extract_kwargs)
    return Flow(
        name="test_fallback",
        description="Test global extraction fallback",
        start_at="step1",
        states={
            "step1": TaskState(
                type="task",
                provider="claude",
                model=_MODEL,
                prompt_template="classify the output",
                result_path="$.task_result",
                extract=ExtractRule(**extract_kwargs),
                retry=0,
                end=True,
            )
        },
    )


def _system_task_flow() -> Flow:
    """Single system task with keyword extraction that misses."""
    return Flow(
        name="test_system_fallback",
        description="Test system provider guard",
        start_at="step1",
        states={
            "step1": TaskState(
                type="task",
                provider="system",
                command="echo 'processing complete'",
                result_path="$.task_result",
                extract=ExtractRule(
                    strategy=["keyword"],
                    pattern="APPROVED|REJECTED",
                    result_path="$.decision",
                ),
                end=True,
            )
        },
    )


# ---------------------------------------------------------------------------
# T002-1: Global default fallback recovers value when strategies miss
# ---------------------------------------------------------------------------


class TestGlobalDefaultFallback:
    def test_global_default_recovers_value_when_strategies_miss(self):
        """When all strategies miss and a global extraction_fallback is set, the
        LLM fallback is invoked and the recovered value is stored in result_path."""
        flow = _claude_task_flow()
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        # Call 1: main task — output has no keyword
        # Call 2: global default fallback — should return APPROVED
        responses = [
            ProviderResult(exit_code=0, stdout="processing complete", stderr=""),
            ProviderResult(exit_code=0, stdout="APPROVED", stderr=""),
        ]

        with patch("fdsx.providers.claude._run_subprocess", side_effect=responses):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"


# ---------------------------------------------------------------------------
# T002-2: System provider guard blocks LLM fallback
# ---------------------------------------------------------------------------


class TestSystemProviderGuard:
    def test_shell_command_extraction_does_not_invoke_fallback(self):
        """When the source state is provider: system, the LLM fallback is never
        invoked even when a global extraction_fallback is configured.

        The workflow fails with RuntimeError (extraction failed); what matters is
        that claude was never called.
        """
        flow = _system_task_flow()
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        with patch("fdsx.providers.claude._run_subprocess") as mock_claude:
            compiled = compile_flow(flow, config=config)
            with pytest.raises(RuntimeError, match="Extraction failed"):
                compiled.graph.invoke({})

        mock_claude.assert_not_called()


# ---------------------------------------------------------------------------
# T002-3: Out-of-set LLM response treated as no recovery
# ---------------------------------------------------------------------------


class TestOutOfSetResponse:
    def test_out_of_set_response_treated_as_no_recovery(self):
        """When the global LLM fallback returns a keyword outside the rule's
        allowed set, the extraction result is None.

        The fallback must have been invoked (call_count == 2) even though the
        workflow ultimately fails — proving the response was filtered, not skipped.
        """
        flow = _claude_task_flow()
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        # Call 1: main task misses keyword
        # Call 2: fallback returns MAYBE (outside APPROVED|REJECTED)
        responses = [
            ProviderResult(exit_code=0, stdout="processing complete", stderr=""),
            ProviderResult(exit_code=0, stdout="MAYBE", stderr=""),
        ]

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=responses
        ) as mock_claude:
            compiled = compile_flow(flow, config=config)
            with pytest.raises(RuntimeError, match="Extraction failed"):
                compiled.graph.invoke({})

        # Fallback must have been called (main task + fallback = 2 calls)
        assert mock_claude.call_count == 2


# ---------------------------------------------------------------------------
# T002-4: Strategies hit → global fallback not invoked
# ---------------------------------------------------------------------------


class TestStrategiesHit:
    def test_workflow_with_no_extraction_miss_unaffected(self):
        """When a strategy succeeds on the first attempt, the global default
        fallback is not invoked at all."""
        flow = _claude_task_flow()
        # Use codex as fallback provider so we can mock it independently
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="codex"))

        main_task = ProviderResult(
            exit_code=0, stdout="The decision is APPROVED", stderr=""
        )

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=main_task),
            patch("fdsx.providers.codex._run_subprocess") as mock_codex,
        ):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        mock_codex.assert_not_called()
        assert result.get("decision") == "APPROVED"


# ---------------------------------------------------------------------------
# T002-5: Per-rule fallback wins over global default
# ---------------------------------------------------------------------------


class TestPerRuleFallbackWins:
    def test_per_rule_fallback_unchanged_with_global_default_present(self):
        """When a rule has its own per-rule LLMClassifyFallback and a global
        default is also configured, the per-rule fallback wins and the global
        default provider is never called."""
        # Per-rule: LLMClassifyFallback using claude
        # Global default: ExtractionFallback using codex (different provider)
        flow = _claude_task_flow(
            extra_extract_kwargs={
                "fallback": LLMClassifyFallback(
                    provider="claude",
                    prompt="Classify as APPROVED or REJECTED: {output}",
                )
            }
        )
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="codex"))

        # Call 1: main task misses keyword
        # Call 2: per-rule claude fallback returns APPROVED
        claude_responses = [
            ProviderResult(exit_code=0, stdout="processing complete", stderr=""),
            ProviderResult(exit_code=0, stdout="APPROVED", stderr=""),
        ]

        with (
            patch(
                "fdsx.providers.claude._run_subprocess", side_effect=claude_responses
            ),
            patch("fdsx.providers.codex._run_subprocess") as mock_codex,
        ):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        mock_codex.assert_not_called()
        assert result.get("decision") == "APPROVED"

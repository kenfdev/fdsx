"""Integration tests for per-workflow extraction_fallback override (T003).

Tests verify that flow-level extraction_fallback correctly overrides the
config-level default, merges profiles in the right order, forwards
extra_instructions, and that per-rule fallback still beats workflow override.
"""

from unittest.mock import patch

from fdsx.core.compiler import compile_flow
from fdsx.core.config import FdsxConfig
from fdsx.models.flow import (
    ExtractionFallback,
    ExtractRule,
    Flow,
    LLMClassifyFallback,
    ProfileConfig,
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
    profiles: dict | None = None,
    per_rule_fallback: LLMClassifyFallback | None = None,
) -> Flow:
    """Single claude task with keyword extraction that always misses."""
    ef = extraction_fallback

    return Flow(
        name="test_workflow_override",
        description="Test per-workflow extraction_fallback override",
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
# T003-1: Flow-level provider overrides config-level provider
# ---------------------------------------------------------------------------


class TestWorkflowOverrideProvider:
    def test_flow_override_provider_fires_instead_of_config_default(self):
        """When flow.extraction_fallback.provider = 'codex' and
        config.extraction_fallback.provider = 'claude', the codex subprocess is
        called for the fallback and claude is called only for the main task."""
        flow = _flow(
            extraction_fallback=ExtractionFallback(
                provider="codex", model="claude-sonnet-4-6"
            )
        )
        config = FdsxConfig(
            extraction_fallback=ExtractionFallback(
                provider="claude", model="claude-sonnet-4-6"
            )
        )

        # Call 1 (claude): main task — no keyword match
        # Call 1 (codex): fallback — returns APPROVED
        claude_responses = [_NO_MATCH]
        codex_response = _APPROVED

        with (
            patch(
                "fdsx.providers.claude._run_subprocess", side_effect=claude_responses
            ) as mock_claude,
            patch(
                "fdsx.providers.codex._run_subprocess", return_value=codex_response
            ) as mock_codex,
        ):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"
        assert mock_claude.call_count == 1  # main task only; no claude fallback
        assert mock_codex.call_count == 1  # flow-level codex fallback fired


# ---------------------------------------------------------------------------
# T003-2/3/4: Profile resolution (flow profiles, config profiles, precedence)
# ---------------------------------------------------------------------------


class TestWorkflowOverrideProfile:
    def test_profile_in_flow_profiles_only(self):
        """When the override names a profile that exists only in flow.profiles
        (not in config.profiles), the fallback succeeds using that profile."""
        flow = _flow(
            extraction_fallback=ExtractionFallback(profile="fast-llm"),
            profiles={"fast-llm": {"provider": "codex", "model": _CODEX_MODEL}},
        )
        config = FdsxConfig(
            extraction_fallback=ExtractionFallback(
                provider="claude", model="claude-sonnet-4-6"
            )
        )

        claude_responses = [_NO_MATCH]
        codex_response = _APPROVED

        with (
            patch(
                "fdsx.providers.claude._run_subprocess", side_effect=claude_responses
            ) as mock_claude,
            patch(
                "fdsx.providers.codex._run_subprocess", return_value=codex_response
            ) as mock_codex,
        ):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"
        assert mock_claude.call_count == 1  # main task only
        assert mock_codex.call_count == 1  # profile resolved to codex

    def test_profile_in_config_profiles_only(self):
        """When the override names a profile that exists only in config.profiles
        (not in flow.profiles), the fallback succeeds using that profile."""
        flow = _flow(
            extraction_fallback=ExtractionFallback(profile="shared-model"),
        )
        config = FdsxConfig(
            profiles={
                "shared-model": ProfileConfig(provider="codex", model=_CODEX_MODEL)
            },
            extraction_fallback=ExtractionFallback(
                provider="claude", model="claude-sonnet-4-6"
            ),
        )

        claude_responses = [_NO_MATCH]
        codex_response = _APPROVED

        with (
            patch(
                "fdsx.providers.claude._run_subprocess", side_effect=claude_responses
            ) as mock_claude,
            patch(
                "fdsx.providers.codex._run_subprocess", return_value=codex_response
            ) as mock_codex,
        ):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"
        assert mock_claude.call_count == 1  # main task only
        assert mock_codex.call_count == 1  # config profile resolved to codex

    def test_flow_profile_wins_over_config_profile(self):
        """When flow.profiles and config.profiles both define the same profile
        name with different providers, the flow-level provider is used."""
        flow = _flow(
            extraction_fallback=ExtractionFallback(profile="shared-model"),
            profiles={"shared-model": {"provider": "codex", "model": _CODEX_MODEL}},
        )
        config = FdsxConfig(
            profiles={"shared-model": ProfileConfig(provider="claude", model=_MODEL)},
            extraction_fallback=ExtractionFallback(
                provider="claude", model="claude-sonnet-4-6"
            ),
        )

        # If flow profile wins (codex): claude called 1x (main task), codex called 1x
        # If config profile wins (claude): claude called 2x, codex never
        claude_responses = [_NO_MATCH]
        codex_response = _APPROVED

        with (
            patch(
                "fdsx.providers.claude._run_subprocess", side_effect=claude_responses
            ) as mock_claude,
            patch(
                "fdsx.providers.codex._run_subprocess", return_value=codex_response
            ) as mock_codex,
        ):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"
        assert mock_claude.call_count == 1  # main task only; flow profile wins
        assert mock_codex.call_count == 1  # codex used because flow profile wins


# ---------------------------------------------------------------------------
# T003-5: extra_instructions appears in the prompt sent to the provider
# ---------------------------------------------------------------------------


class TestWorkflowOverrideExtraInstructions:
    def test_extra_instructions_appear_in_prompt(self):
        """The extra_instructions string from flow.extraction_fallback is
        included verbatim in the prompt passed to the provider subprocess."""
        instructions = "Use formal language and return uppercase only"
        flow = _flow(
            extraction_fallback=ExtractionFallback(
                provider="claude",
                model="claude-sonnet-4-6",
                extra_instructions=instructions,
            )
        )
        config = FdsxConfig()

        # Call 1: main task — no match
        # Call 2: fallback — prompt contains extra_instructions
        responses = [
            _NO_MATCH,
            _APPROVED,
        ]

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=responses
        ) as mock_claude:
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"
        assert mock_claude.call_count == 2

        # The second call is the fallback — inspect its args for extra_instructions
        fallback_call = mock_claude.call_args_list[1]
        # args is a list like ["claude", "-p", "<prompt>"] or uses stdin_data
        call_kwargs = fallback_call.kwargs
        args_list: list[str] = call_kwargs.get("args", [])
        stdin_data: str | None = call_kwargs.get("stdin_data")

        prompt_text = stdin_data if stdin_data is not None else " ".join(args_list)
        assert instructions in prompt_text


# ---------------------------------------------------------------------------
# T003-6: Per-rule explicit fallback wins over workflow override
# ---------------------------------------------------------------------------


class TestPerRuleWinsOverWorkflowOverride:
    def test_per_rule_explicit_fallback_wins(self):
        """When ExtractRule.fallback.provider = 'claude' and
        flow.extraction_fallback.provider = 'codex', the per-rule claude
        fallback is used and codex is never called."""
        flow = _flow(
            extraction_fallback=ExtractionFallback(
                provider="codex", model="claude-sonnet-4-6"
            ),
            per_rule_fallback=LLMClassifyFallback(
                provider="claude",
                model="claude-sonnet-4-6",
                prompt="Classify as APPROVED or REJECTED: {output}",
            ),
        )
        config = FdsxConfig()

        # Call 1 (claude): main task — no match
        # Call 2 (claude): per-rule fallback — returns APPROVED
        claude_responses = [_NO_MATCH, _APPROVED]

        with (
            patch(
                "fdsx.providers.claude._run_subprocess", side_effect=claude_responses
            ) as mock_claude,
            patch("fdsx.providers.codex._run_subprocess") as mock_codex,
        ):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"
        assert mock_claude.call_count == 2  # main task + per-rule fallback
        mock_codex.assert_not_called()


# ---------------------------------------------------------------------------
# T003-7: No workflow override → config default applies (regression guard)
# ---------------------------------------------------------------------------


class TestNoOverrideFallsBackToGlobal:
    def test_no_workflow_override_uses_config_default(self):
        """When flow.extraction_fallback is absent and
        config.extraction_fallback.provider = 'claude', the config default
        fires and the recovered value is stored in result_path."""
        flow = _flow(extraction_fallback=None)
        config = FdsxConfig(
            extraction_fallback=ExtractionFallback(
                provider="claude", model="claude-sonnet-4-6"
            )
        )

        responses = [_NO_MATCH, _APPROVED]

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=responses
        ) as mock_claude:
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"
        assert mock_claude.call_count == 2  # main task + global fallback

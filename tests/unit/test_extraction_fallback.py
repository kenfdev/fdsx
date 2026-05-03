"""Failing-first tests for ExtractionFallback model and its integration into Flow / FdsxConfig."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fdsx.core.config import FdsxConfig
from fdsx.models.flow import Flow


def _minimal_flow(**overrides):
    base = {
        "name": "t",
        "description": "test flow",
        "start_at": "s1",
        "states": {
            "s1": {
                "type": "task",
                "provider": "system",
                "command": "echo hi",
                "result_path": "$.out",
                "end": True,
            }
        },
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# ExtractionFallback model validation
# ---------------------------------------------------------------------------


class TestExtractionFallbackValidation:
    def test_provider_only_is_valid(self):
        from fdsx.models.flow import ExtractionFallback

        fb = ExtractionFallback(provider="claude")
        assert fb.profile is None

    def test_profile_only_is_valid(self):
        from fdsx.models.flow import ExtractionFallback

        fb = ExtractionFallback(profile="fast")
        assert fb.provider is None

    def test_both_provider_and_profile_rejected(self):
        from fdsx.models.flow import ExtractionFallback

        with pytest.raises(ValidationError):
            ExtractionFallback(provider="claude", profile="fast")

    def test_neither_provider_nor_profile_rejected(self):
        from fdsx.models.flow import ExtractionFallback

        with pytest.raises(ValidationError):
            ExtractionFallback()

    def test_invalid_provider_rejected(self):
        from fdsx.models.flow import ExtractionFallback

        with pytest.raises(ValidationError):
            ExtractionFallback(provider="invalid_xyz")

    def test_extra_instructions_defaults_to_none(self):
        from fdsx.models.flow import ExtractionFallback

        fb = ExtractionFallback(provider="claude")
        assert fb.extra_instructions is None

    def test_extra_instructions_accepted_with_valid_provider(self):
        from fdsx.models.flow import ExtractionFallback

        fb = ExtractionFallback(provider="claude", extra_instructions="append this")
        assert fb.extra_instructions == "append this"

    def test_extra_unknown_keys_rejected(self):
        from fdsx.models.flow import ExtractionFallback

        with pytest.raises(ValidationError):
            ExtractionFallback(provider="claude", unknown_key="oops")


# ---------------------------------------------------------------------------
# Flow.extraction_fallback field parsing
# ---------------------------------------------------------------------------


class TestFlowExtractionFallbackField:
    def test_field_omitted_gives_none(self):
        flow = Flow(**_minimal_flow())
        assert flow.extraction_fallback is None

    def test_field_null_gives_none(self):
        flow = Flow(**_minimal_flow(extraction_fallback=None))
        assert flow.extraction_fallback is None

    def test_field_false_gives_false(self):
        flow = Flow(**_minimal_flow(extraction_fallback=False))
        assert flow.extraction_fallback is False

    def test_field_mapping_gives_extraction_fallback_instance(self):
        from fdsx.models.flow import ExtractionFallback

        flow = Flow(**_minimal_flow(extraction_fallback={"provider": "claude"}))
        assert isinstance(flow.extraction_fallback, ExtractionFallback)
        assert flow.extraction_fallback.provider == "claude"

    def test_field_invalid_mapping_both_set_rejected(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            Flow(
                **_minimal_flow(
                    extraction_fallback={"provider": "claude", "profile": "fast"}
                )
            )


# ---------------------------------------------------------------------------
# FdsxConfig.extraction_fallback field parsing
# ---------------------------------------------------------------------------


class TestFdsxConfigExtractionFallbackField:
    def test_field_omitted_gives_none(self):
        cfg = FdsxConfig()
        assert cfg.extraction_fallback is None

    def test_field_mapping_gives_extraction_fallback_instance(self):
        from fdsx.models.flow import ExtractionFallback

        cfg = FdsxConfig(extraction_fallback={"provider": "opencode"})
        assert isinstance(cfg.extraction_fallback, ExtractionFallback)
        assert cfg.extraction_fallback.provider == "opencode"

    def test_field_empty_mapping_rejected(self):
        with pytest.raises(ValidationError, match="exactly one of"):
            FdsxConfig(extraction_fallback={})

    def test_field_extra_unknown_keys_rejected(self):
        with pytest.raises(ValidationError, match=r"extraction_fallback\.bogus"):
            FdsxConfig(extraction_fallback={"provider": "claude", "bogus": "val"})


# ---------------------------------------------------------------------------
# resolve_fallback resolver
# ---------------------------------------------------------------------------


class TestResolveFallback:
    """Tests for the resolve_fallback pure function and ResolvedFallback dataclass."""

    def _rule(self, with_fallback: bool = False):
        from fdsx.models.flow import ExtractRule, LLMClassifyFallback

        fb = (
            LLMClassifyFallback(provider="claude", prompt="classify")
            if with_fallback
            else None
        )
        return ExtractRule(
            strategy=["keyword"], pattern="yes|no", fallback=fb, result_path="$.out"
        )

    def _flow(self, ef=None):
        return Flow(**_minimal_flow(extraction_fallback=ef))

    def _cfg(self, ef=None):
        return FdsxConfig(extraction_fallback=ef)

    def test_rule_fallback_returns_rule_source(self):
        from fdsx.core.extraction_fallback import ResolvedFallback, resolve_fallback
        from fdsx.models.flow import LLMClassifyFallback

        rule = self._rule(with_fallback=True)
        result = resolve_fallback(rule, self._flow(), self._cfg())

        assert result is not None
        assert result.source == "rule"
        assert isinstance(result.config, LLMClassifyFallback)
        assert result == ResolvedFallback(config=rule.fallback, source="rule")

    def test_flow_disabled_no_rule_fallback_returns_none(self):
        from fdsx.core.extraction_fallback import resolve_fallback

        rule = self._rule(with_fallback=False)
        result = resolve_fallback(rule, self._flow(ef=False), self._cfg())

        assert result is None

    def test_flow_disabled_beats_global_config(self):
        from fdsx.core.extraction_fallback import resolve_fallback
        from fdsx.models.flow import ExtractionFallback

        rule = self._rule(with_fallback=False)
        global_ef = ExtractionFallback(provider="claude")
        result = resolve_fallback(rule, self._flow(ef=False), self._cfg(ef=global_ef))

        assert result is None

    def test_flow_override_returns_workflow_source(self):
        from fdsx.core.extraction_fallback import ResolvedFallback, resolve_fallback
        from fdsx.models.flow import ExtractionFallback

        rule = self._rule(with_fallback=False)
        flow_ef = ExtractionFallback(provider="opencode")
        result = resolve_fallback(rule, self._flow(ef=flow_ef), self._cfg())

        assert result is not None
        assert result.source == "workflow"
        assert result == ResolvedFallback(config=flow_ef, source="workflow")

    def test_global_config_returns_global_source(self):
        from fdsx.core.extraction_fallback import ResolvedFallback, resolve_fallback
        from fdsx.models.flow import ExtractionFallback

        rule = self._rule(with_fallback=False)
        global_ef = ExtractionFallback(provider="claude")
        result = resolve_fallback(rule, self._flow(ef=None), self._cfg(ef=global_ef))

        assert result is not None
        assert result.source == "global"
        assert result == ResolvedFallback(config=global_ef, source="global")

    def test_nothing_set_returns_none(self):
        from fdsx.core.extraction_fallback import resolve_fallback

        rule = self._rule(with_fallback=False)
        result = resolve_fallback(rule, self._flow(ef=None), self._cfg(ef=None))

        assert result is None

    def test_purity_repeated_calls_return_equal_results(self):
        from fdsx.core.extraction_fallback import resolve_fallback
        from fdsx.models.flow import ExtractionFallback

        rule = self._rule(with_fallback=False)
        flow_ef = ExtractionFallback(provider="claude")
        flow = self._flow(ef=flow_ef)
        cfg = self._cfg()

        result1 = resolve_fallback(rule, flow, cfg)
        result2 = resolve_fallback(rule, flow, cfg)

        assert result1 == result2
        assert result1 is not None
        assert result1.source == "workflow"

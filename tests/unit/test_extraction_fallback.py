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

        fb = ExtractionFallback(provider="claude", model="claude-sonnet-4-6")
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

        fb = ExtractionFallback(provider="claude", model="claude-sonnet-4-6")
        assert fb.extra_instructions is None

    def test_extra_instructions_accepted_with_valid_provider(self):
        from fdsx.models.flow import ExtractionFallback

        fb = ExtractionFallback(
            provider="claude",
            model="claude-sonnet-4-6",
            extra_instructions="append this",
        )
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

        flow = Flow(
            **_minimal_flow(
                extraction_fallback={"provider": "claude", "model": "claude-sonnet-4-6"}
            )
        )
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

        cfg = FdsxConfig(
            extraction_fallback={"provider": "opencode", "model": "claude-sonnet-4-6"}
        )
        assert isinstance(cfg.extraction_fallback, ExtractionFallback)
        assert cfg.extraction_fallback.provider == "opencode"

    def test_field_empty_mapping_rejected(self):
        with pytest.raises(ValidationError, match="exactly one of"):
            FdsxConfig(extraction_fallback={})

    def test_field_extra_unknown_keys_rejected(self):
        with pytest.raises(ValidationError, match=r"extraction_fallback\.bogus"):
            FdsxConfig(
                extraction_fallback={
                    "provider": "claude",
                    "model": "claude-sonnet-4-6",
                    "bogus": "val",
                }
            )


# ---------------------------------------------------------------------------
# resolve_fallback resolver
# ---------------------------------------------------------------------------


class TestResolveFallback:
    """Tests for the resolve_fallback pure function and ResolvedFallback dataclass."""

    def _rule(self, with_fallback: bool = False):
        from fdsx.models.flow import ExtractRule, LLMClassifyFallback

        fb = (
            LLMClassifyFallback(
                provider="claude", model="claude-sonnet-4-6", prompt="classify"
            )
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
        global_ef = ExtractionFallback(provider="claude", model="claude-sonnet-4-6")
        result = resolve_fallback(rule, self._flow(ef=False), self._cfg(ef=global_ef))

        assert result is None

    def test_flow_disabled_with_rule_fallback_returns_rule(self):
        from fdsx.core.extraction_fallback import resolve_fallback

        rule = self._rule(with_fallback=True)  # has explicit LLMClassifyFallback
        result = resolve_fallback(rule, self._flow(ef=False), self._cfg())

        assert result is not None
        assert result.source == "rule"
        assert result.config is rule.fallback

    def test_flow_override_returns_workflow_source(self):
        from fdsx.core.extraction_fallback import ResolvedFallback, resolve_fallback
        from fdsx.models.flow import ExtractionFallback

        rule = self._rule(with_fallback=False)
        flow_ef = ExtractionFallback(provider="opencode", model="claude-sonnet-4-6")
        result = resolve_fallback(rule, self._flow(ef=flow_ef), self._cfg())

        assert result is not None
        assert result.source == "workflow"
        assert result == ResolvedFallback(config=flow_ef, source="workflow")

    def test_global_config_returns_global_source(self):
        from fdsx.core.extraction_fallback import ResolvedFallback, resolve_fallback
        from fdsx.models.flow import ExtractionFallback

        rule = self._rule(with_fallback=False)
        global_ef = ExtractionFallback(provider="claude", model="claude-sonnet-4-6")
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
        flow_ef = ExtractionFallback(provider="claude", model="claude-sonnet-4-6")
        flow = self._flow(ef=flow_ef)
        cfg = self._cfg()

        result1 = resolve_fallback(rule, flow, cfg)
        result2 = resolve_fallback(rule, flow, cfg)

        assert result1 == result2
        assert result1 is not None
        assert result1.source == "workflow"


# ---------------------------------------------------------------------------
# _build_default_fallback_prompt
# ---------------------------------------------------------------------------


def _keyword_rule():
    from fdsx.models.flow import ExtractRule

    return ExtractRule(
        strategy=["keyword"], pattern="APPROVED|REJECTED", result_path="$.out"
    )


def _regex_rule():
    from fdsx.models.flow import ExtractRule

    return ExtractRule(strategy=["regex"], pattern=r"\d+", result_path="$.val")


def _multi_strategy_rule():
    from fdsx.models.flow import ExtractRule

    return ExtractRule(
        strategy=["keyword", "regex"], pattern="YES|NO", result_path="$.ans"
    )


class TestDefaultFallbackPrompt:
    def test_role_line_present(self):
        from fdsx.core.extraction_fallback import _build_default_fallback_prompt

        result = _build_default_fallback_prompt("some output", _keyword_rule(), None)
        assert result.startswith("You are a recovery extractor.")

    def test_strategies_attempted_lists_all_strategies(self):
        from fdsx.core.extraction_fallback import _build_default_fallback_prompt

        rule = _multi_strategy_rule()
        result = _build_default_fallback_prompt("output text", rule, None)
        assert "keyword, regex" in result

    def test_pattern_present_verbatim(self):
        from fdsx.core.extraction_fallback import _build_default_fallback_prompt

        rule = _keyword_rule()
        result = _build_default_fallback_prompt("output text", rule, None)
        assert rule.pattern in result

    def test_raw_output_under_output_header(self):
        from fdsx.core.extraction_fallback import _build_default_fallback_prompt

        raw = "This is the raw tool output"
        result = _build_default_fallback_prompt(raw, _keyword_rule(), None)
        assert "OUTPUT:" in result
        output_section_start = result.index("OUTPUT:")
        assert raw in result[output_section_start:]

    def test_keyword_strategy_lists_allowed_values(self):
        from fdsx.core.extraction_fallback import _build_default_fallback_prompt

        rule = _keyword_rule()
        result = _build_default_fallback_prompt("output", rule, None)
        assert "APPROVED | REJECTED" in result

    def test_non_keyword_strategy_no_allowed_values_block(self):
        from fdsx.core.extraction_fallback import _build_default_fallback_prompt

        rule = _regex_rule()
        result = _build_default_fallback_prompt("output", rule, None)
        assert "APPROVED | REJECTED" not in result
        assert "NONE" in result

    def test_extra_instructions_block_appended_when_set(self):
        from fdsx.core.extraction_fallback import _build_default_fallback_prompt

        result = _build_default_fallback_prompt(
            "output", _keyword_rule(), "Always prefer APPROVED."
        )
        assert "ADDITIONAL INSTRUCTIONS:" in result
        assert "Always prefer APPROVED." in result

    def test_no_extra_instructions_block_when_none(self):
        from fdsx.core.extraction_fallback import _build_default_fallback_prompt

        result = _build_default_fallback_prompt("output", _keyword_rule(), None)
        assert "ADDITIONAL INSTRUCTIONS:" not in result

    def test_examples_section_present(self):
        from fdsx.core.extraction_fallback import _build_default_fallback_prompt

        result = _build_default_fallback_prompt("some output", _keyword_rule(), None)
        assert "EXAMPLES:" in result


# ---------------------------------------------------------------------------
# execute_default_fallback
# ---------------------------------------------------------------------------


def _make_resolved(provider=None, profile=None, extra_instructions=None):
    from fdsx.core.extraction_fallback import ResolvedFallback
    from fdsx.models.flow import ExtractionFallback

    if provider is not None:
        cfg = ExtractionFallback(provider=provider, model="claude-sonnet-4-6")
    else:
        cfg = ExtractionFallback(profile=profile)
    if extra_instructions is not None:
        cfg = cfg.model_copy(update={"extra_instructions": extra_instructions})
    return ResolvedFallback(config=cfg, source="global")


def _make_factory(stdout="APPROVED", exit_code=0):
    from unittest.mock import MagicMock

    from fdsx.providers.base import ProviderResult

    stub_provider = MagicMock()
    stub_provider.execute.return_value = ProviderResult(
        exit_code=exit_code, stdout=stdout, stderr=""
    )
    factory = MagicMock(return_value=stub_provider)
    return factory, stub_provider


class TestExecuteDefaultFallback:
    def _keyword_rule(self):
        from fdsx.models.flow import ExtractRule

        return ExtractRule(
            strategy=["keyword"], pattern="APPROVED|REJECTED", result_path="$.out"
        )

    def _regex_rule(self):
        from fdsx.models.flow import ExtractRule

        return ExtractRule(strategy=["regex"], pattern=r"\d+", result_path="$.val")

    def test_provider_keyword_exact_match_returns_value(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="APPROVED")
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result == "APPROVED"
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(entry.get("outcome") == "recovered" for entry in info_logs)

    def test_provider_keyword_lowercase_normalised(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="approved")
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result == "APPROVED"
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(entry.get("outcome") == "recovered" for entry in info_logs)

    def test_provider_keyword_out_of_set_returns_none(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="MAYBE")
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result is None
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(entry.get("outcome") == "rejected" for entry in info_logs)

    def test_non_keyword_strategy_verbatim_passthrough(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="42")
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._regex_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result == "42"
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(entry.get("outcome") == "recovered" for entry in info_logs)

    def test_none_sentinel_returns_none(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="NONE")
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result is None
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(
            entry.get("outcome") == "rejected"
            and entry.get("reason") == "model_returned_none"
            for entry in info_logs
        )

    def test_whitespace_stripped_from_response(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="  value  ")
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._regex_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result == "value"
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(entry.get("outcome") == "recovered" for entry in info_logs)

    def test_non_zero_exit_returns_none(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="", exit_code=1)
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result is None
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(
            entry.get("outcome") == "error" and entry.get("error") == "non_zero_exit"
            for entry in info_logs
        )

    def test_timeout_error_returns_none(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        stub_provider = MagicMock()
        stub_provider.execute.side_effect = TimeoutError("timed out")
        factory = MagicMock(return_value=stub_provider)
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result is None
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(
            entry.get("outcome") == "error" and entry.get("error") == "timeout"
            for entry in info_logs
        )

    def test_provider_factory_raises_returns_none(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory = MagicMock(side_effect=RuntimeError("no binary"))
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result is None
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(
            entry.get("outcome") == "error"
            and entry.get("error") == "provider_init_failed"
            for entry in info_logs
        )

    def test_profile_resolution_success(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="42")
        resolved = _make_resolved(profile="fast")
        merged = {"fast": {"provider": "opencode", "model": "gpt-4o"}}
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._regex_rule(),
                resolved=resolved,
                merged_profiles=merged,
                source_provider="system",
                provider_factory=factory,
            )
        assert result == "42"
        factory.assert_called_once_with("opencode")
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(entry.get("outcome") == "recovered" for entry in info_logs)

    def test_profile_not_found_returns_none(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory()
        resolved = _make_resolved(profile="missing")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result is None
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(
            entry.get("outcome") == "error"
            and entry.get("error") == "profile_not_found"
            for entry in info_logs
        )

    def test_provider_execute_raises_returns_none(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        stub_provider = MagicMock()
        stub_provider.execute.side_effect = RuntimeError("unexpected provider error")
        factory = MagicMock(return_value=stub_provider)
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            result = execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        assert result is None
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert any(
            entry.get("outcome") == "error"
            and entry.get("error") == "provider_call_failed"
            for entry in info_logs
        )

    def test_info_logs_carry_source_and_strategy_list(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="APPROVED")
        resolved = _make_resolved(provider="claude")
        rule = self._keyword_rule()
        with structlog.testing.capture_logs() as logs:
            execute_default_fallback(
                output="text",
                rule=rule,
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        info_logs = [entry for entry in logs if entry["log_level"] == "info"]
        assert info_logs, "expected at least one INFO log"
        for entry in info_logs:
            assert entry.get("source") == resolved.source
            assert entry.get("strategy_list") == rule.strategy

    # ------------------------------------------------------------------
    # T005: on_fallback callback and structlog event rename
    # ------------------------------------------------------------------

    def test_on_fallback_called_once_on_recovered(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="APPROVED")
        resolved = _make_resolved(provider="claude")
        callback = MagicMock()
        with structlog.testing.capture_logs():
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
                on_fallback=callback,
            )
        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.outcome == "recovered"
        assert event.source == "global"

    def test_on_fallback_called_once_on_rejected_keyword(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="MAYBE")
        resolved = _make_resolved(provider="claude")
        callback = MagicMock()
        with structlog.testing.capture_logs():
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
                on_fallback=callback,
            )
        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.outcome == "rejected"

    def test_on_fallback_called_once_on_model_returned_none(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="NONE")
        resolved = _make_resolved(provider="claude")
        callback = MagicMock()
        with structlog.testing.capture_logs():
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
                on_fallback=callback,
            )
        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.outcome == "rejected"

    def test_on_fallback_called_once_on_timeout(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        stub_provider = MagicMock()
        stub_provider.execute.side_effect = TimeoutError("timed out")
        factory = MagicMock(return_value=stub_provider)
        resolved = _make_resolved(provider="claude")
        callback = MagicMock()
        with structlog.testing.capture_logs():
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
                on_fallback=callback,
            )
        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.outcome == "error"
        assert event.error_kind == "timeout"

    def test_on_fallback_called_once_on_provider_init_failed(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory = MagicMock(side_effect=RuntimeError("no binary"))
        resolved = _make_resolved(provider="claude")
        callback = MagicMock()
        with structlog.testing.capture_logs():
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
                on_fallback=callback,
            )
        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.outcome == "error"
        assert event.error_kind == "provider_init_failed"

    def test_on_fallback_called_once_on_non_zero_exit(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="", exit_code=1)
        resolved = _make_resolved(provider="claude")
        callback = MagicMock()
        with structlog.testing.capture_logs():
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
                on_fallback=callback,
            )
        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.outcome == "error"
        assert event.error_kind == "non_zero_exit"

    def test_on_fallback_called_once_on_profile_not_found(self):
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory()
        resolved = _make_resolved(profile="missing")
        callback = MagicMock()
        with structlog.testing.capture_logs():
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
                on_fallback=callback,
            )
        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.outcome == "error"
        assert event.error_kind == "profile_not_found"

    def test_structlog_event_renamed_to_extraction_fallback_invoked(self):
        import structlog.testing

        from fdsx.core.extraction_fallback import execute_default_fallback

        factory, _ = _make_factory(stdout="APPROVED")
        resolved = _make_resolved(provider="claude")
        with structlog.testing.capture_logs() as logs:
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="system",
                provider_factory=factory,
            )
        info_events = [e["event"] for e in logs if e["log_level"] == "info"]
        assert any(e == "extraction_fallback_invoked" for e in info_events), (
            f"Expected 'extraction_fallback_invoked' but got: {info_events}"
        )
        assert not any(e == "default_fallback_invoked" for e in info_events), (
            f"Found deprecated 'default_fallback_invoked' in logs: {info_events}"
        )

    def test_model_is_passed_to_provider_execute(self):
        # Executor must read config.model and forward it to provider.execute().
        # Currently extraction_fallback.py hard-codes model=None on the provider branch.
        # FAILS RED: execute receives model=None instead of "claude-sonnet-4-6".
        import structlog.testing

        from fdsx.core.extraction_fallback import (
            ResolvedFallback,
            execute_default_fallback,
        )
        from fdsx.models.flow import ExtractionFallback

        factory, mock_provider = _make_factory(stdout="APPROVED")
        # model_construct bypasses Pydantic validation so we can attach the model
        # field before it exists in the schema.
        cfg = ExtractionFallback.model_construct(
            provider="claude", model="claude-sonnet-4-6"
        )
        resolved = ResolvedFallback(config=cfg, source="global")

        with structlog.testing.capture_logs():
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="claude",
                provider_factory=factory,
            )

        mock_provider.execute.assert_called_once()
        call_kwargs = mock_provider.execute.call_args
        passed_model = (
            call_kwargs.kwargs.get("model")
            if call_kwargs.kwargs
            else (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
        )
        assert passed_model == "claude-sonnet-4-6", (
            f"Expected model='claude-sonnet-4-6' forwarded to provider.execute, got: {passed_model!r}"
        )

    def test_fallback_event_carries_provider_and_model_on_success(self):
        # FallbackEvent must expose provider and model so display/recorder can use them.
        # FAILS RED: FallbackEvent dataclass has no provider/model fields yet.
        from unittest.mock import MagicMock

        import structlog.testing

        from fdsx.core.extraction_fallback import (
            ResolvedFallback,
            execute_default_fallback,
        )
        from fdsx.models.flow import ExtractionFallback

        factory, _ = _make_factory(stdout="APPROVED")
        cfg = ExtractionFallback.model_construct(
            provider="claude", model="claude-sonnet-4-6"
        )
        resolved = ResolvedFallback(config=cfg, source="global")
        callback = MagicMock()

        with structlog.testing.capture_logs():
            execute_default_fallback(
                output="text",
                rule=self._keyword_rule(),
                resolved=resolved,
                merged_profiles={},
                source_provider="claude",
                provider_factory=factory,
                on_fallback=callback,
            )

        callback.assert_called_once()
        event = callback.call_args[0][0]
        assert event.provider == "claude", (
            f"Expected event.provider='claude', got: {event.provider!r}"
        )
        assert event.model == "claude-sonnet-4-6", (
            f"Expected event.model='claude-sonnet-4-6', got: {event.model!r}"
        )


# ---------------------------------------------------------------------------
# ExtractionFallback — new model field and updated XOR rules (RED)
# ---------------------------------------------------------------------------


class TestExtractionFallbackModelField:
    """Tests for the new `model` field on ExtractionFallback and revised XOR rules.

    All tests in this class FAIL in RED because the model field does not yet
    exist and the XOR validator does not yet require model alongside provider.
    """

    def test_provider_and_model_constructs_successfully(self):
        # FAILS RED: ExtractionFallback has extra="forbid" and no model field.
        # ValidationError: Extra inputs are not permitted for 'model'.
        from fdsx.models.flow import ExtractionFallback

        fb = ExtractionFallback(provider="claude", model="claude-sonnet-4-6")
        assert fb.model == "claude-sonnet-4-6"
        assert fb.provider == "claude"

    def test_provider_without_model_raises(self):
        # FAILS RED: ExtractionFallback(provider="claude") currently succeeds.
        # After GREEN the XOR validator requires model when provider is set.
        from fdsx.models.flow import ExtractionFallback

        with pytest.raises(ValidationError, match="model"):
            ExtractionFallback(provider="claude")

    def test_profile_and_model_raises(self):
        # FAILS RED: model is extra and rejected before the new XOR check fires,
        # so the test cannot distinguish the intended error from the extra-field error.
        # After GREEN ExtractionFallback accepts model and the XOR raises the right message.
        from fdsx.models.flow import ExtractionFallback

        with pytest.raises(ValidationError, match=r"mutually exclusive|profile"):
            ExtractionFallback(profile="fast", model="claude-sonnet-4-6")

    def test_all_three_raises(self):
        # FAILS RED: same extra-field problem as above.
        # After GREEN the XOR check fires and raises the right message.
        from fdsx.models.flow import ExtractionFallback

        with pytest.raises(ValidationError, match=r"mutually exclusive|profile"):
            ExtractionFallback(
                profile="fast", provider="claude", model="claude-sonnet-4-6"
            )

    def test_empty_raises_mentioning_provider_and_profile(self):
        # Already covered by test_neither_provider_nor_profile_rejected, but the
        # new error message must mention both options.  FAILS RED when the message
        # changes from "provider or profile" to include "model" wording.
        from fdsx.models.flow import ExtractionFallback

        with pytest.raises(ValidationError, match=r"provider|profile"):
            ExtractionFallback()


# ---------------------------------------------------------------------------
# LLMClassifyFallback — new model + profile fields and XOR rules (RED)
# ---------------------------------------------------------------------------


class TestLLMClassifyFallbackValidation:
    """Tests for the new model and profile fields on LLMClassifyFallback.

    All tests that assert a NEW constraint FAIL in RED because the fields do
    not yet exist and no XOR validator is wired.
    """

    def test_provider_and_model_constructs_successfully(self):
        # FAILS RED: model is silently dropped (no model_config extra="forbid"),
        # so fb.model raises AttributeError or returns wrong value.
        from fdsx.models.flow import LLMClassifyFallback

        fb = LLMClassifyFallback(
            provider="claude", model="claude-sonnet-4-6", prompt="classify this"
        )
        assert fb.model == "claude-sonnet-4-6"

    def test_profile_only_constructs_successfully(self):
        # FAILS RED: provider is currently required (Field(...)); passing only
        # profile raises ValidationError "Field required".
        from fdsx.models.flow import LLMClassifyFallback

        fb = LLMClassifyFallback(profile="fast", prompt="classify this")
        assert fb.profile == "fast"
        assert fb.provider is None

    def test_provider_without_model_raises(self):
        # FAILS RED: LLMClassifyFallback(provider="claude", prompt="x") currently
        # succeeds; after GREEN the XOR validator requires model.
        from fdsx.models.flow import LLMClassifyFallback

        with pytest.raises(ValidationError, match="model"):
            LLMClassifyFallback(provider="claude", prompt="classify this")

    def test_profile_and_provider_raises(self):
        # FAILS RED: profile and model are silently ignored (no extra="forbid"),
        # so the construction currently succeeds.  After GREEN the XOR raises.
        from fdsx.models.flow import LLMClassifyFallback

        with pytest.raises(ValidationError, match=r"mutually exclusive|profile"):
            LLMClassifyFallback(
                profile="fast",
                provider="claude",
                model="claude-sonnet-4-6",
                prompt="classify this",
            )

    def test_profile_and_model_raises(self):
        # FAILS RED: model is silently ignored; profile is silently ignored.
        # Construction currently succeeds (uses required provider from Field(...)).
        # After GREEN, profile+model without provider raises XOR error.
        from fdsx.models.flow import LLMClassifyFallback

        with pytest.raises(ValidationError, match=r"mutually exclusive|profile"):
            LLMClassifyFallback(
                profile="fast",
                model="claude-sonnet-4-6",
                prompt="classify this",
            )


# ---------------------------------------------------------------------------
# FallbackEvent — new provider / model fields (RED)
# ---------------------------------------------------------------------------


class TestFallbackEventFields:
    """FallbackEvent must expose provider and model after the plan is implemented.

    Both tests FAIL RED because the frozen dataclass has no such fields yet.
    """

    def test_event_exposes_provider_and_model(self):
        # FAILS RED: frozen dataclass raises TypeError for unexpected kwargs.
        from fdsx.core.extraction_fallback import FallbackEvent

        event = FallbackEvent(
            source="global",
            outcome="recovered",
            state_name="step1",
            pattern="APPROVED|REJECTED",
            provider="claude",
            model="claude-sonnet-4-6",
        )
        assert event.provider == "claude"
        assert event.model == "claude-sonnet-4-6"

    def test_event_provider_model_default_none(self):
        # FAILS RED: FallbackEvent has no provider/model attributes; AttributeError.
        from fdsx.core.extraction_fallback import FallbackEvent

        event = FallbackEvent(
            source="global",
            outcome="recovered",
            state_name="step1",
            pattern="APPROVED|REJECTED",
        )
        assert event.provider is None
        assert event.model is None

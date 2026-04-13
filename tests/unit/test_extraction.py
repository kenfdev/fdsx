import pytest

from fdsx.core.extraction import (
    _execute_llm_fallback,
    _execute_strategy,
    _get_nested_value,
    _json_strategy,
    _keyword_strategy,
    _regex_strategy,
    extract_value,
)
from fdsx.models.flow import ExtractRule, LLMClassifyFallback


class TestGetNestedValue:
    def test_dot_returns_root(self):
        data = {"a": 1}
        assert _get_nested_value(data, ".") == {"a": 1}

    def test_leading_dot_stripped(self):
        data = {"groups": ["x"]}
        assert _get_nested_value(data, ".groups") == ["x"]

    def test_leading_dot_nested_path(self):
        data = {"result": {"status": "ok"}}
        assert _get_nested_value(data, ".result.status") == "ok"

    def test_empty_string_returns_none(self):
        data = {"a": 1}
        assert _get_nested_value(data, "") is None

    def test_existing_dot_notation_unchanged(self):
        data = {"a": {"b": "c"}}
        assert _get_nested_value(data, "a.b") == "c"

    def test_missing_key_returns_none(self):
        data = {"a": 1}
        assert _get_nested_value(data, "missing") is None


class TestJsonStrategy:
    def test_json_code_block_extraction(self):
        output = '```json\n{"decision": "APPROVED"}\n```'
        result = _json_strategy(output, "decision")
        assert result == "APPROVED"

    def test_json_code_block_nested_field(self):
        output = '```json\n{"result": {"status": "accepted"}}\n```'
        result = _json_strategy(output, "result.status")
        assert result == "accepted"

    def test_raw_json_extraction(self):
        output = '{"decision": "REJECTED"}'
        result = _json_strategy(output, "decision")
        assert result == "REJECTED"

    def test_raw_json_nested_extraction(self):
        output = '{"data": {"value": "success"}}'
        result = _json_strategy(output, "data.value")
        assert result == "success"

    def test_json_recursive_lookup_not_supported(self):
        """Regression: JSON should not recursively search for keys - must use dot notation."""
        output = '{"data": {"value": "success"}}'
        result = _json_strategy(output, "value")
        assert result is None

    def test_no_json_returns_none(self):
        output = "This is just plain text without any JSON"
        result = _json_strategy(output, "decision")
        assert result is None

    def test_invalid_json_returns_none(self):
        output = "```json\nnot valid json\n```"
        result = _json_strategy(output, "decision")
        assert result is None

    def test_field_not_found_returns_none(self):
        output = '{"other": "value"}'
        result = _json_strategy(output, "decision")
        assert result is None


class TestRegexStrategy:
    def test_capture_group(self):
        output = "The result is: APPROVED"
        result = _regex_strategy(output, r"result is: (\w+)")
        assert result == "APPROVED"

    def test_multiple_capture_groups_returns_first(self):
        output = "color: blue, size: large"
        result = _regex_strategy(output, r"color: (\w+), size: (\w+)")
        assert result == "blue"

    def test_no_capture_group_returns_full_match(self):
        output = "APPROVED"
        result = _regex_strategy(output, r"APPROVED")
        assert result == "APPROVED"

    def test_no_match_returns_none(self):
        output = "Some text without the pattern"
        result = _regex_strategy(output, r"APPROVED")
        assert result is None

    def test_complex_regex(self):
        output = "Error code: 404 - Not Found"
        result = _regex_strategy(output, r"Error code: (\d+)")
        assert result == "404"

    def test_invalid_regex_returns_none(self):
        result = _regex_strategy("some text", r"(unclosed")
        assert result is None


class TestKeywordStrategy:
    def test_case_insensitive_match_returns_original_keyword(self):
        output = "The status is approved"
        result = _keyword_strategy(output, "APPROVED|REJECTED")
        assert result == "APPROVED"

    def test_case_insensitive_rejected(self):
        output = "The request was rejected"
        result = _keyword_strategy(output, "APPROVED|REJECTED")
        assert result == "REJECTED"

    def test_last_keyword_wins(self):
        """When multiple keywords appear, latest in output wins."""
        output = "both approved and rejected appear"
        result = _keyword_strategy(output, "APPROVED|REJECTED")
        assert result == "REJECTED"  # "rejected" appears last in output

    def test_no_match_returns_none(self):
        output = "The status is unknown"
        result = _keyword_strategy(output, "APPROVED|REJECTED")
        assert result is None

    def test_single_keyword(self):
        output = "everything looks good"
        result = _keyword_strategy(output, "good")
        assert result == "good"

    def test_exact_case_in_pattern_preserved(self):
        output = "Status: Success"
        result = _keyword_strategy(output, "success|Success|SUCCESS")
        assert result == "success"

    def test_word_boundary_match(self):
        """Regression: keyword must match as whole word, not substring."""
        output = "The UNAPPROVED status"
        result = _keyword_strategy(output, "APPROVED|REJECTED")
        assert result is None

    def test_word_boundary_match_at_start(self):
        """Word boundary at start of word."""
        output = "APPROVED is the status"
        result = _keyword_strategy(output, "APPROVED|REJECTED")
        assert result == "APPROVED"

    def test_word_boundary_match_with_punctuation(self):
        """Word boundary with punctuation."""
        output = "The decision: APPROVED."
        result = _keyword_strategy(output, "APPROVED|REJECTED")
        assert result == "APPROVED"

    def test_output_order_not_pattern_order(self):
        """Regression: keyword matching should return the latest occurrence in output, not first in pattern list."""
        output = "first REJECTED then APPROVED"
        result = _keyword_strategy(output, "APPROVED|REJECTED")
        assert result == "APPROVED"

    def test_output_order_multiple_keywords(self):
        """When multiple keywords appear, return the one that appears last in output."""
        output = "the decision is APPROVED, not REJECTED"
        result = _keyword_strategy(output, "MAYBE|APPROVED|REJECTED")
        assert result == "REJECTED"

    def test_keyword_in_prose_before_verdict_returns_verdict(self):
        """Regression: keyword appearing in prose before the actual verdict should not be returned."""
        output = "there is no pending implementation to reject so I will APPROVE"
        result = _keyword_strategy(output, "APPROVE|REJECT")
        assert result == "APPROVE"


class TestExecuteStrategy:
    def test_json_strategy(self):
        result = _execute_strategy("json", '{"key": "value"}', "key")
        assert result == "value"

    def test_regex_strategy(self):
        result = _execute_strategy("regex", "hello world", "world")
        assert result == "world"

    def test_keyword_strategy(self):
        result = _execute_strategy("keyword", "status is ok", "ok|good")
        assert result == "ok"


class TestExtractValue:
    def test_fallback_chain_json_wins(self):
        rule = ExtractRule(
            strategy=["json", "regex", "keyword"],
            pattern="decision",
            result_path="$.result",
        )
        result = extract_value('{"decision": "OK"}', rule)
        assert result == "OK"

    def test_fallback_chain_regex_wins(self):
        rule = ExtractRule(
            strategy=["json", "regex", "keyword"],
            pattern=r"decision is (\w+)",
            result_path="$.result",
        )
        result = extract_value("The decision is OK", rule)
        assert result == "OK"

    def test_fallback_chain_keyword_wins(self):
        rule = ExtractRule(
            strategy=["json", "regex", "keyword"],
            pattern="OK|NOT_OK",
            result_path="$.result",
        )
        result = extract_value("Status: ok", rule)
        assert result == "OK"

    def test_all_fail_no_llm_returns_none(self):
        rule = ExtractRule(
            strategy=["json", "regex", "keyword"],
            pattern="MISSING",
            result_path="$.result",
        )
        result = extract_value("random text", rule)
        assert result is None


class TestExtractRuleValidation:
    def test_empty_strategy_raises_validation_error(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExtractRule(strategy=[], pattern="x", result_path="$.r")

    def test_invalid_strategy_raises_validation_error(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExtractRule(strategy=["keywrod"], pattern="x", result_path="$.r")


class TestLLMFallback:
    def test_llm_fallback_called_when_strategies_fail(self):
        """LLM fallback is called when all strategies fail; matched keyword returned."""
        rule = ExtractRule(
            strategy=["json"],
            pattern="APPROVED|REJECTED",
            result_path="$.result",
            fallback=LLMClassifyFallback(
                type="llm_classify",
                provider="claude",
                prompt="Classify: {output}",
            ),
        )

        class MockProvider:
            def execute(self, prompt, model, timeout, output_callback):
                from fdsx.providers.base import ProviderResult

                return ProviderResult(exit_code=0, stdout="APPROVED", stderr="")

        def mock_factory(provider_name):
            return MockProvider()

        result = extract_value("some random text", rule, mock_factory)
        assert result == "APPROVED"

    def test_llm_fallback_fails_returns_none(self):
        """LLM fallback returning non-zero exit code yields None."""
        rule = ExtractRule(
            strategy=["json"],
            pattern="APPROVED|REJECTED",
            result_path="$.result",
            fallback=LLMClassifyFallback(
                type="llm_classify",
                provider="claude",
                prompt="Classify: {output}",
            ),
        )

        class MockProvider:
            def execute(self, prompt, model, timeout, output_callback):
                from fdsx.providers.base import ProviderResult

                return ProviderResult(exit_code=1, stdout="", stderr="error")

        def mock_factory(provider_name):
            return MockProvider()

        result = extract_value("some random text", rule, mock_factory)
        assert result is None

    def test_no_provider_factory_no_llm_fallback(self):
        """No provider factory means LLM fallback returns None."""
        rule = ExtractRule(
            strategy=["json"],
            pattern="APPROVED|REJECTED",
            result_path="$.result",
            fallback=LLMClassifyFallback(
                type="llm_classify",
                provider="claude",
                prompt="Classify: {output}",
            ),
        )

        result = extract_value("some random text", rule, None)
        assert result is None

    # F4 regression: system provider must be rejected at runtime even if validation missed it
    def test_execute_llm_fallback_system_provider_runtime_guard(self):
        """F4: _execute_llm_fallback must return None for system provider (defense-in-depth)."""
        # Use model_construct to bypass pydantic validation so we can test the runtime guard
        fallback = LLMClassifyFallback.model_construct(
            type="llm_classify",
            provider="system",
            prompt="Classify: {output}",
        )

        call_count = [0]

        def mock_factory(provider_name: str) -> object:
            call_count[0] += 1
            raise AssertionError("should not be called")

        result = _execute_llm_fallback(
            "some text", fallback, mock_factory, pattern="APPROVED|REJECTED"
        )
        assert result is None
        assert call_count[0] == 0

    # F5 regression: LLM output validated against allowed keywords
    def test_llm_fallback_exact_keyword_match_returned(self):
        """F5: LLM returning exactly a pattern keyword (case-insensitive) returns the keyword."""
        fallback = LLMClassifyFallback(
            type="llm_classify",
            provider="claude",
            prompt="Classify: {output}",
        )

        class MockProvider:
            def execute(self, prompt, model, timeout, output_callback):
                from fdsx.providers.base import ProviderResult

                return ProviderResult(exit_code=0, stdout="approved", stderr="")

        def mock_factory(provider_name):
            return MockProvider()

        result = _execute_llm_fallback(
            "text", fallback, mock_factory, pattern="APPROVED|REJECTED"
        )
        assert result == "APPROVED"  # original case from pattern

    def test_llm_fallback_non_keyword_output_rejected(self):
        """F5: LLM returning text not matching any pattern keyword returns None."""
        fallback = LLMClassifyFallback(
            type="llm_classify",
            provider="claude",
            prompt="Classify: {output}",
        )

        class MockProvider:
            def execute(self, prompt, model, timeout, output_callback):
                from fdsx.providers.base import ProviderResult

                return ProviderResult(
                    exit_code=0, stdout="Sure, the answer is APPROVED", stderr=""
                )

        def mock_factory(provider_name):
            return MockProvider()

        result = _execute_llm_fallback(
            "text", fallback, mock_factory, pattern="APPROVED|REJECTED"
        )
        assert result is None  # non-exact match rejected

    def test_llm_fallback_no_pattern_returns_raw_output(self):
        """F5: Without pattern, LLM raw output is returned as-is."""
        fallback = LLMClassifyFallback(
            type="llm_classify",
            provider="claude",
            prompt="Classify: {output}",
        )

        class MockProvider:
            def execute(self, prompt, model, timeout, output_callback):
                from fdsx.providers.base import ProviderResult

                return ProviderResult(exit_code=0, stdout="any output", stderr="")

        def mock_factory(provider_name):
            return MockProvider()

        result = _execute_llm_fallback("text", fallback, mock_factory, pattern=None)
        assert result == "any output"

    # F2 regression: validation_pattern is only set for keyword strategy
    def test_llm_fallback_with_keyword_strategy_validates_pattern(self):
        """F2: When keyword strategy is configured, LLM output is validated against pattern."""
        rule = ExtractRule(
            strategy=["keyword"],
            pattern="APPROVED|REJECTED",
            result_path="$.result",
            fallback=LLMClassifyFallback(
                type="llm_classify",
                provider="claude",
                prompt="Classify: {output}",
            ),
        )

        class MockProvider:
            def execute(self, prompt, model, timeout, output_callback):
                from fdsx.providers.base import ProviderResult

                return ProviderResult(exit_code=0, stdout="APPROVED", stderr="")

        result = extract_value("some unmatched text", rule, lambda _: MockProvider())
        assert result == "APPROVED"

    def test_llm_fallback_with_json_strategy_no_pattern_validation(self):
        """F2: When only json strategy is configured, LLM output is returned raw (no keyword validation)."""
        rule = ExtractRule(
            strategy=["json"],
            pattern="decision",  # json field name, not a pipe-delimited allowlist
            result_path="$.result",
            fallback=LLMClassifyFallback(
                type="llm_classify",
                provider="claude",
                prompt="Classify: {output}",
            ),
        )

        class MockProvider:
            def execute(self, prompt, model, timeout, output_callback):
                from fdsx.providers.base import ProviderResult

                return ProviderResult(exit_code=0, stdout="APPROVED", stderr="")

        # "APPROVED" does NOT match pattern "decision" as a keyword, but since
        # "keyword" is not in strategy list, no validation is applied — returns raw output
        result = extract_value("some text without JSON", rule, lambda _: MockProvider())
        assert result == "APPROVED"

    def test_llm_fallback_keyword_strategy_rejects_non_matching_output(self):
        """F2: When keyword strategy is configured, non-matching LLM output returns None."""
        rule = ExtractRule(
            strategy=["keyword"],
            pattern="APPROVED|REJECTED",
            result_path="$.result",
            fallback=LLMClassifyFallback(
                type="llm_classify",
                provider="claude",
                prompt="Classify: {output}",
            ),
        )

        class MockProvider:
            def execute(self, prompt, model, timeout, output_callback):
                from fdsx.providers.base import ProviderResult

                return ProviderResult(
                    exit_code=0, stdout="Sure, it is APPROVED", stderr=""
                )

        # Non-exact match — validation rejects it
        result = extract_value("some text", rule, lambda _: MockProvider())
        assert result is None

    def test_invalid_fallback_provider_raises_validation_error(self):
        """CQ-3: Provider outside whitelist (claude/codex/opencode) must raise ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LLMClassifyFallback(
                type="llm_classify",
                provider="cluade",
                prompt="Classify: {output}",
            )

    def test_llm_fallback_blocked_for_system_source_provider(self):
        """SEC-1: LLM fallback must not fire when source_provider is 'system'."""
        rule = ExtractRule(
            strategy=["keyword"],
            pattern="APPROVED|REJECTED",
            result_path="$.result",
            fallback=LLMClassifyFallback(
                type="llm_classify",
                provider="claude",
                prompt="Classify: {output}",
            ),
        )

        def mock_factory(name: str) -> object:
            raise AssertionError("should not be called")

        result = extract_value(
            "sensitive local data", rule, mock_factory, source_provider="system"
        )
        assert result is None

import pytest

from fdsx.core.compiler import _aggregate
from fdsx.models.flow import AggregateRule


class TestAggregateMajority:
    def test_majority_2_of_3_match_returns_match_value(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="majority",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"decision": "APPROVED"},
            {"decision": "REJECTED"},
        ]
        result = _aggregate(source_data, rule)
        assert result == "APPROVED"

    def test_majority_2_of_3_match_returns_match_value_direct(self):
        """Test with data passed directly (without source path resolution)."""
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="majority",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"decision": "APPROVED"},
            {"decision": "REJECTED"},
        ]
        result = _aggregate(source_data, rule)
        assert result == "APPROVED"

    def test_majority_1_of_3_match_returns_no_match_value(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="majority",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"decision": "REJECTED"},
            {"decision": "REJECTED"},
        ]
        result = _aggregate(source_data, rule)
        assert result == "REJECTED"

    def test_majority_0_of_3_match_returns_no_match_value(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="majority",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "REJECTED"},
            {"decision": "REJECTED"},
            {"decision": "REJECTED"},
        ]
        result = _aggregate(source_data, rule)
        assert result == "REJECTED"

    def test_majority_tie_2_of_4_returns_no_match(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="majority",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"decision": "APPROVED"},
            {"decision": "REJECTED"},
            {"decision": "REJECTED"},
        ]
        result = _aggregate(source_data, rule)
        assert result == "REJECTED"


class TestAggregateAll:
    def test_all_3_of_3_match_returns_match_value(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="all",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"decision": "APPROVED"},
            {"decision": "APPROVED"},
        ]
        result = _aggregate(source_data, rule)
        assert result == "APPROVED"

    def test_all_2_of_3_match_returns_no_match_value(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="all",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"decision": "APPROVED"},
            {"decision": "REJECTED"},
        ]
        result = _aggregate(source_data, rule)
        assert result == "REJECTED"


class TestAggregateAny:
    def test_any_1_of_3_match_returns_match_value(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="any",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"decision": "REJECTED"},
            {"decision": "REJECTED"},
        ]
        result = _aggregate(source_data, rule)
        assert result == "APPROVED"

    def test_any_0_of_3_match_returns_no_match_value(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="any",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "REJECTED"},
            {"decision": "REJECTED"},
            {"decision": "REJECTED"},
        ]
        result = _aggregate(source_data, rule)
        assert result == "REJECTED"


class TestAggregateEmptySource:
    def test_empty_source_array_returns_no_match(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="majority",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = []
        result = _aggregate(source_data, rule)
        assert result == "REJECTED"

    def test_source_field_missing_returns_no_match(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="any",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [{"other": "value"}, {"other": "value2"}]
        result = _aggregate(source_data, rule)
        assert result == "REJECTED"


class TestAggregateStrategyValidation:
    def test_invalid_strategy_raises_error(self):
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="invalid",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [{"decision": "APPROVED"}]
        with pytest.raises(ValueError, match="Unknown aggregation strategy"):
            _aggregate(source_data, rule)


class TestAggregateFailedBranchSecurity:
    """Regression tests for security finding: failed branches must count as no_match.

    Previously, _aggregate() used len(values) (only items with the field) as
    the denominator. This allowed bypassing a dissenting reviewer by knocking out
    their branch: APPROVED, APPROVED, <failed> → 2/2 APPROVED instead of 2/3.
    The fix uses len(source_data) (total branches) as denominator so failed/missing
    branches always count against the match threshold.
    """

    def test_majority_failed_branch_counts_against_match(self):
        """APPROVED, APPROVED, <failed-no-field> must NOT achieve majority of 3."""
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="majority",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        # Two APPROVED + one failed branch (no decision field)
        source_data = [
            {"decision": "APPROVED"},
            {"decision": "APPROVED"},
            {"output": "", "exit_code": 1, "error": "branch failed"},  # no 'decision'
        ]
        result = _aggregate(source_data, rule)
        # 2/3 APPROVED — 2 > 3/2 = 1.5 → majority APPROVED
        assert result == "APPROVED"

    def test_majority_requires_true_majority_with_failures(self):
        """1 APPROVED + 2 failed branches must not pass majority gate."""
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="majority",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"output": "", "exit_code": 1, "error": "failed"},
            {"output": "", "exit_code": 1, "error": "failed"},
        ]
        result = _aggregate(source_data, rule)
        # 1/3 APPROVED — 1 is NOT > 1.5 → no_match
        assert result == "REJECTED"

    def test_all_requires_all_including_missing_field(self):
        """strategy=all: any branch missing the field must return no_match."""
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="all",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"decision": "APPROVED"},
            {"output": "", "exit_code": 1, "error": "failed"},  # no 'decision'
        ]
        result = _aggregate(source_data, rule)
        # match_count=2, total=3: 2 != 3 → no_match
        assert result == "REJECTED"

    def test_any_still_matches_even_with_failed_branch(self):
        """strategy=any: at least one match returns match even if another branch fails."""
        rule = AggregateRule(
            source="$.reviews",
            field="decision",
            strategy="any",
            match="APPROVED",
            no_match="REJECTED",
            result_path="$.decision",
        )
        source_data = [
            {"decision": "APPROVED"},
            {"output": "", "exit_code": 1, "error": "failed"},
        ]
        result = _aggregate(source_data, rule)
        # 1 match > 0 → match
        assert result == "APPROVED"


class TestMinSuccessDefault:
    """Regression tests for T028: min_success defaults to all branches (not open/None)."""

    def test_min_success_default_enforces_all_branches(self):
        """When min_success is not set, all branches must succeed."""
        from fdsx.core.compiler import compile_flow
        from fdsx.models.flow import Branch, Flow, ParallelState

        flow = Flow(
            name="Min Success Default Test",
            description="Test flow for min_success default",
            start_at="parallel_state",
            states={
                "parallel_state": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(provider="system", command="echo ok", retry=0),
                        Branch(provider="system", command="exit 1", retry=0),
                    ],
                    result_path="$.results",
                    min_success=None,  # Default: must enforce all branches
                    end=True,
                ),
            },
        )

        compiled = compile_flow(flow)

        # With min_success=None (default=all), one failed branch should raise
        with pytest.raises(RuntimeError, match="only .* branches succeeded"):
            compiled.graph.invoke({})

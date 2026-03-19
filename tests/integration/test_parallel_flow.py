from pathlib import Path

import pytest

from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow


class TestParallelFlow:
    def test_parallel_review_majority_aggregation(self):
        """Test parallel review with majority aggregation and choice routing."""
        path = Path("tests/fixtures/parallel_review.yaml")

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path)

        assert "reviews" in result
        assert len(result["reviews"]) == 3

        for review in result["reviews"]:
            assert "output" in review
            assert "exit_code" in review

        assert "decision" in result
        assert result["decision"] == "APPROVED"

    def test_parallel_branch_results_have_output_field(self):
        """Verify branch results array contains output field."""
        path = Path("tests/fixtures/parallel_review.yaml")

        result = run_flow(path)

        assert "reviews" in result
        assert len(result["reviews"]) == 3
        for review in result["reviews"]:
            assert "output" in review


class TestParallelMinSuccess:
    def test_min_success_tolerates_partial_failure(self):
        """Test that min_success allows flow to continue with partial branch failures."""
        path = Path("tests/fixtures/parallel_min_success.yaml")

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path)

        assert "results" in result
        assert len(result["results"]) == 3

        successful = sum(1 for r in result["results"] if r.get("exit_code") == 0)
        assert successful == 2

        assert "success_check" in result
        assert result["success_check"] == "Flow continued after partial failure"

    def test_min_success_failure_raises_error(self):
        """Test that when too many branches fail, flow raises error."""
        from fdsx.models.flow import Flow, ParallelState, Branch

        flow = Flow(
            name="Parallel All Fail",
            description="Test flow for min_success failure",
            start_at="parallel_state",
            states={
                "parallel_state": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(
                            provider="system",
                            command="exit 1",
                            retry=0,
                        ),
                        Branch(
                            provider="system",
                            command="exit 1",
                            retry=0,
                        ),
                        Branch(
                            provider="system",
                            command="exit 1",
                            retry=0,
                        ),
                    ],
                    result_path="$.results",
                    min_success=2,
                    end=True,
                ),
            },
        )

        from fdsx.core.compiler import compile_flow

        compiled = compile_flow(flow)

        with pytest.raises(RuntimeError, match="only .* branches succeeded"):
            compiled.graph.invoke({})

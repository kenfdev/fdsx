"""Integration tests for loop termination via RemainingSteps channel (T018/T019).

These tests verify that a loop terminates gracefully via the remaining_steps
conditional guard rather than raising GraphRecursionError.
"""

from fdsx.core.engine import FlowResult, run_flow
from tests import FIXTURES_DIR


class TestLoopRemainingSteps:
    def test_loop_terminates_without_recursion_error(self, tmp_path):
        """T018: Loop exits cleanly at max_loop without raising GraphRecursionError."""
        path = FIXTURES_DIR / "loop_flow.yaml"

        result = run_flow(path, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        assert result.status != "error", (
            f"Expected clean exit, got error status. results={result.results}"
        )

    def test_loop_result_contains_last_iteration_state(self, tmp_path):
        """T019: Loop results contain complete state from the last iteration."""
        path = FIXTURES_DIR / "loop_flow.yaml"

        result = run_flow(path, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        # All three result_paths from the loop body must be present
        assert "plan_output" in result.results, (
            f"plan_output missing from results: {list(result.results.keys())}"
        )
        assert "impl_output" in result.results, (
            f"impl_output missing from results: {list(result.results.keys())}"
        )
        assert "review_output" in result.results, (
            f"review_output missing from results: {list(result.results.keys())}"
        )

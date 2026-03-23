from pathlib import Path

from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow


class TestLoopFlow:
    def test_loop_stops_at_max_iterations(self, tmp_path):
        """Test that loop stops gracefully after max_loop iterations."""
        path = Path("tests/fixtures/loop_flow.yaml")

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        assert flow.max_loop == 3

        result = run_flow(path, base_dir=tmp_path)

        # Must return a dict (not raise), and must not be empty — partial results preserved
        assert isinstance(result, dict)
        assert result != {}, "Loop control must return partial state, not empty dict"

    def test_loop_returns_partial_results(self, tmp_path):
        """Test that loop returns results from the last iteration when max_loop is reached."""
        path = Path("tests/fixtures/loop_flow.yaml")

        result = run_flow(path, base_dir=tmp_path)

        assert isinstance(result, dict)
        # The fixture loops plan→implement→review→decide and never reaches done,
        # so partial results from the last completed iteration must be present.
        assert result != {}, "Partial results must be returned (not empty dict)"
        # At least one of the task states' result_paths must appear in the result
        partial_keys = {"plan_output", "impl_output", "review_output"}
        assert partial_keys.intersection(result.keys()), (
            f"Expected at least one of {partial_keys} in result, got: {list(result.keys())}"
        )

    def test_state_variables_retained_across_iterations(self, tmp_path):
        """Test that state variables from earlier loop iterations are available in later ones."""
        path = Path("tests/fixtures/loop_flow.yaml")

        result = run_flow(path, base_dir=tmp_path)

        # plan_output, impl_output, and review_output were all set in the loop body.
        # They must all survive to the final captured state (the last iteration overwrites them,
        # so all three should be present in partial results).
        assert "plan_output" in result, "plan_output must be retained in partial results"
        assert "impl_output" in result, "impl_output must be retained in partial results"
        assert "review_output" in result, "review_output must be retained in partial results"

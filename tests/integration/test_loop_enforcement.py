from fdsx.core.engine import run_flow
from tests import FIXTURES_DIR


class TestLoopEnforcement:
    def test_max_loop_prevents_infinite_cycle(self, tmp_path):
        """R2-F5: flow.max_loop must bound execution via LangGraph recursion_limit."""
        path = FIXTURES_DIR / "loop_flow.yaml"
        result = run_flow(path, base_dir=tmp_path)
        assert isinstance(result, dict)
        # Loop control must return partial results rather than empty dict or an exception
        assert result != {}, "Loop control must return partial state, not empty dict"
        # Verify that state from the last completed iteration is preserved
        assert "plan_output" in result, (
            "plan_output from last loop iteration must be present in partial results"
        )

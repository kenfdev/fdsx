from pathlib import Path

from fdsx.core.engine import run_flow


class TestLoopEnforcement:
    def test_max_loop_prevents_infinite_cycle(self):
        """R2-F5: flow.max_loop must bound execution via LangGraph recursion_limit."""
        path = Path("tests/fixtures/loop_flow.yaml")
        result = run_flow(path)
        assert isinstance(result, dict)
        # Loop control must return partial results rather than empty dict or an exception
        assert result != {}, "Loop control must return partial state, not empty dict"
        # Verify that state from the last completed iteration is preserved
        assert "plan_output" in result, (
            "plan_output from last loop iteration must be present in partial results"
        )

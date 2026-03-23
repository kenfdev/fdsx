"""Integration tests for per-state max_iterations (T007).

Tests verify:
- Workflow with max_iterations: 2 on plan fails after 2 entries to plan
  with RuntimeError "State 'plan' reached max_iterations limit (2)"
- max_iterations and max_loop coexist — max_iterations triggers first when limit is lower
"""

import pytest

from fdsx.core.engine import run_flow
from tests import FIXTURES_DIR

MAX_ITERATIONS_FLOW = FIXTURES_DIR / "max_iterations_flow.yaml"


def _get_log_dir(tmp_path):
    """Return the logs directory from a single run under tmp_path/runs/."""
    runs_dir = tmp_path / "runs"
    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1, f"Expected 1 run dir, found: {run_dirs}"
    log_dir = run_dirs[0] / "logs"
    assert log_dir.exists(), f"logs/ directory not found in {run_dirs[0]}"
    return log_dir


class TestMaxIterationsFlow:
    """Integration tests for per-state max_iterations in looping workflows."""

    def test_flow_fails_at_max_iterations(self, tmp_path):
        """Flow fails with RuntimeError after plan is entered 3 times (limit is 2).

        The loop is: plan -> review -> decide -> plan (since review always outputs REJECTED).
        With max_iterations: 2 on plan, the flow should raise RuntimeError on the 3rd entry.
        """
        with pytest.raises(
            RuntimeError,
            match="State 'plan' reached max_iterations limit \\(2\\)",
        ):
            run_flow(MAX_ITERATIONS_FLOW, base_dir=tmp_path, quiet=True)

    def test_max_iterations_and_max_loop_coexist(self, tmp_path):
        """max_iterations: 2 triggers before max_loop: 10.

        The flow has max_loop=10 but max_iterations=2 on plan.
        Since max_iterations is stricter (2 < 10), it should fire first.
        """
        with pytest.raises(
            RuntimeError,
            match="State 'plan' reached max_iterations limit \\(2\\)",
        ):
            run_flow(MAX_ITERATIONS_FLOW, base_dir=tmp_path, quiet=True)

    def test_log_files_created_for_each_iteration(self, tmp_path):
        """Log files plan_1.log and plan_2.log are created before the error on iteration 3."""
        with pytest.raises(RuntimeError, match="max_iterations limit"):
            run_flow(MAX_ITERATIONS_FLOW, base_dir=tmp_path, quiet=True)

        log_dir = _get_log_dir(tmp_path)
        # Iterations 1 and 2 should have log files (3rd entry fails before StreamLogger)
        assert (log_dir / "plan_1.log").exists(), (
            f"plan_1.log not found in {log_dir}, files: {list(log_dir.iterdir())}"
        )
        assert (log_dir / "plan_2.log").exists(), (
            f"plan_2.log not found in {log_dir}, files: {list(log_dir.iterdir())}"
        )

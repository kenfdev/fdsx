"""Integration tests for per-state max_iterations (T007, T010f).

Tests verify:
- Workflow with max_iterations: 2 on plan fails after 2 entries to plan
  with RuntimeError "State 'plan' reached max_iterations limit (2)"
- max_iterations and max_loop coexist — max_iterations triggers first when limit is lower
- max_iterations interaction with resume: iteration count preserved through WaitState checkpoint
"""

from unittest.mock import patch

import pytest

from fdsx.core.engine import run_flow
from tests import FIXTURES_DIR

MAX_ITERATIONS_FLOW = FIXTURES_DIR / "max_iterations_flow.yaml"
MAX_ITERATIONS_WAIT_FLOW = FIXTURES_DIR / "max_iterations_wait_flow.yaml"


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

        Uses the same flow (max_loop=10, max_iterations=2 on plan).
        Verifies the error is specifically max_iterations, NOT max_loop.
        Also verifies only 2 plan iterations ran (far below max_loop=10).
        """
        with pytest.raises(
            RuntimeError,
            match="State 'plan' reached max_iterations limit \\(2\\)",
        ):
            run_flow(MAX_ITERATIONS_FLOW, base_dir=tmp_path, quiet=True)

        # Verify only 2 plan iterations ran — proves max_iterations fired before max_loop
        log_dir = _get_log_dir(tmp_path)
        assert (log_dir / "plan_1.log").exists()
        assert (log_dir / "plan_2.log").exists()
        assert not (log_dir / "plan_3.log").exists(), (
            "plan_3.log should not exist — max_iterations should prevent 3rd execution"
        )

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


class TestMaxIterationsResumeInteraction:
    """Edge case tests: max_iterations interaction with WaitState checkpoint resume (T010f)."""

    def test_max_iterations_preserved_through_wait_checkpoint(self, tmp_path):
        """_state_iterations is preserved through WaitState checkpoint on resume.

        Verifies that when a flow resumes from a WaitState checkpoint, the iteration
        count for 'plan' is correctly carried forward. With max_iterations=2, the flow
        allows plan to execute twice (via 'retry' responses) and raises RuntimeError
        on the 3rd entry — proving the checkpoint correctly preserved _state_iterations.

        Flow: plan (iter=1) → wait → [retry] → plan (iter=2) → wait → [retry] → plan (iter=3→ERROR)
        """
        # First "retry" allows plan to run a 2nd time.
        # Second "retry" would route back to plan a 3rd time, triggering max_iterations.
        with patch("fdsx.core.engine.interrupts.display_wait_prompt", side_effect=["retry", "retry"]):
            with pytest.raises(
                RuntimeError,
                match="State 'plan' reached max_iterations limit \\(2\\)",
            ):
                run_flow(MAX_ITERATIONS_WAIT_FLOW, base_dir=tmp_path, quiet=True)

    def test_max_iterations_wait_log_files_for_successful_iterations(self, tmp_path):
        """Log files plan_1.log and plan_2.log exist; plan_3.log does not (error on entry).

        Verifies that log files are created for iterations that completed successfully
        before max_iterations was triggered.
        """
        with patch("fdsx.core.engine.interrupts.display_wait_prompt", side_effect=["retry", "retry"]):
            with pytest.raises(RuntimeError, match="max_iterations limit"):
                run_flow(MAX_ITERATIONS_WAIT_FLOW, base_dir=tmp_path, quiet=True)

        runs_dir = tmp_path / "runs"
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1, f"Expected 1 run dir, found: {run_dirs}"
        log_dir = run_dirs[0] / "logs"
        assert log_dir.exists(), f"logs/ not found in {run_dirs[0]}"

        # Iterations 1 and 2 completed → log files exist
        assert (log_dir / "plan_1.log").exists(), (
            f"plan_1.log not found in {log_dir}, files: {list(log_dir.iterdir())}"
        )
        assert (log_dir / "plan_2.log").exists(), (
            f"plan_2.log not found in {log_dir}, files: {list(log_dir.iterdir())}"
        )
        # Iteration 3 failed before StreamLogger was created → no plan_3.log
        assert not (log_dir / "plan_3.log").exists(), (
            f"plan_3.log should not exist (error fires before execution)"
        )

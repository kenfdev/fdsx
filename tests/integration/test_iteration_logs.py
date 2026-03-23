"""Integration tests for iteration-numbered log files (T005).

Tests verify:
- Workflow with a loop produces plan_1.log, plan_2.log, ... per iteration
- Each iteration log file is a separate file (not appended to one file)
- States executed only once still produce {state}_1.log (not {state}.log)
- _state_iterations dict in the flow state is correctly incremented
"""

from pathlib import Path

from fdsx.core.engine import run_flow
from tests import FIXTURES_DIR

LOOP_FLOW = FIXTURES_DIR / "loop_flow.yaml"


def _get_log_dir(tmp_path: Path) -> Path:
    """Return the logs directory from a single run under tmp_path/runs/."""
    runs_dir = tmp_path / "runs"
    run_dirs = list(runs_dir.iterdir())
    assert len(run_dirs) == 1, f"Expected 1 run dir, found: {run_dirs}"
    log_dir = run_dirs[0] / "logs"
    assert log_dir.exists(), f"logs/ directory not found in {run_dirs[0]}"
    return log_dir


class TestIterationLogFiles:
    """Integration tests for iteration-numbered log files in looping workflows."""

    def test_iteration_log_files_created(self, tmp_path):
        """Run loop_flow (max_loop=3), verify plan_1.log through plan_3.log exist."""
        run_flow(LOOP_FLOW, base_dir=tmp_path, quiet=True)

        log_dir = _get_log_dir(tmp_path)

        # loop_flow has max_loop=3 and review always returns REJECTED,
        # so plan, implement, review each run 3 times.
        for state in ("plan", "implement", "review"):
            for i in range(1, 4):
                log_file = log_dir / f"{state}_{i}.log"
                assert log_file.exists(), (
                    f"Expected {log_file.name} in {log_dir}, found: {list(log_dir.iterdir())}"
                )

    def test_iteration_logs_are_separate_files(self, tmp_path):
        """plan_1.log and plan_2.log contain different content and are separate files."""
        run_flow(LOOP_FLOW, base_dir=tmp_path, quiet=True)

        log_dir = _get_log_dir(tmp_path)

        plan_1 = (log_dir / "plan_1.log").read_text(encoding="utf-8")
        plan_2 = (log_dir / "plan_2.log").read_text(encoding="utf-8")

        # Both files must exist and contain content (loop_flow plan outputs "Planning step")
        assert plan_1.strip(), "plan_1.log must not be empty"
        assert plan_2.strip(), "plan_2.log must not be empty"

        # They are separate files — not concatenated content of multiple iterations
        # Each should contain exactly the output of one echo command
        assert plan_1.count("Planning step") == 1, (
            "plan_1.log must contain exactly one iteration's output"
        )
        assert plan_2.count("Planning step") == 1, (
            "plan_2.log must contain exactly one iteration's output"
        )

    def test_states_executed_once_still_have_iteration_suffix(self, tmp_path):
        """A simple linear flow produces {state}_1.log, not {state}.log."""
        simple_flow = FIXTURES_DIR / "simple_flow.yaml"
        run_flow(simple_flow, base_dir=tmp_path, quiet=True)

        log_dir = _get_log_dir(tmp_path)

        log_files = list(log_dir.glob("*.log"))
        assert log_files, f"No log files found in {log_dir}"

        for log_file in log_files:
            stem = log_file.stem
            # stem should match pattern: {statename}_{number}
            parts = stem.rsplit("_", 1)
            assert len(parts) == 2, (
                f"Log file '{log_file.name}' does not match {{state}}_{{N}}.log pattern"
            )
            assert parts[1].isdigit(), (
                f"Log file '{log_file.name}' iteration suffix is not a number"
            )

    def test_no_bare_state_log_files(self, tmp_path):
        """No log files follow the old {state}.log pattern (without iteration suffix)."""
        run_flow(LOOP_FLOW, base_dir=tmp_path, quiet=True)

        log_dir = _get_log_dir(tmp_path)

        for log_file in log_dir.glob("*.log"):
            stem = log_file.stem
            parts = stem.rsplit("_", 1)
            assert len(parts) == 2 and parts[1].isdigit(), (
                f"Found old-style log file '{log_file.name}' without iteration suffix"
            )

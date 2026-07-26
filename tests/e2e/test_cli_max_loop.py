from pathlib import Path

from tests.e2e.cli_test_utils import run_fdsx


def test_loop_exhaustion_returns_nonzero_and_explicit_status(tmp_path: Path) -> None:
    (tmp_path / ".fdsx").mkdir()
    flow_path = tmp_path / "workflow.yaml"
    flow_path.write_text(
        """
name: never-converges
description: Exercise the terminal loop guard
max_loop: 2
start_at: work
states:
  work:
    type: task
    provider: system
    command: echo partial
    result_path: $.partial
    next: decide
  decide:
    type: choice
    choices:
      - variable: $.partial
        operator: equals
        value: converged
        next: done
    default: work
  done:
    type: pass
    end: true
""".lstrip()
    )

    completed = run_fdsx(
        ["run", str(flow_path), "--thread-id", "max-loop-e2e"], cwd=tmp_path
    )

    assert completed.returncode != 0
    assert "max_loop_reached" in completed.stderr

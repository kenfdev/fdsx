"""Integration tests for explicit recovery jumps after non-success outcomes."""

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.core.engine import resume_flow, run_flow


def _write_review_loop(
    tmp_path: Path,
    *,
    marker: Path,
    setup_count: Path,
) -> Path:
    flow_path = tmp_path / "recovery.yaml"
    flow_path.write_text(
        textwrap.dedent(
            f"""\
            name: recovery-review
            description: Review loop used to exercise explicit recovery
            start_at: setup
            max_loop: 1
            states:
              setup:
                type: task
                provider: system
                command: "printf x >> {setup_count}"
                next: review
              review:
                type: task
                provider: system
                command: "if [ -f {marker} ]; then echo APPROVE; else echo REJECT; fi"
                result_path: $.review_output
                extract:
                  strategy: [keyword]
                  pattern: "APPROVE|REJECT"
                  result_path: $.review_decision
                next: route
              route:
                type: choice
                choices:
                  - variable: $.review_decision
                    operator: equals
                    value: APPROVE
                    next: done
                default: review
              done:
                type: task
                provider: system
                command: "echo recovered"
                result_path: $.result
                end: true
            """
        )
    )
    return flow_path


def test_recovery_jump_resumes_max_loop_from_selected_state(tmp_path: Path) -> None:
    base_dir = tmp_path / ".fdsx"
    marker = tmp_path / "fixed"
    setup_count = tmp_path / "setup-count"
    flow_path = _write_review_loop(
        tmp_path,
        marker=marker,
        setup_count=setup_count,
    )
    thread_id = "recovery-max-loop"

    first = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert first.status == "max_loop_reached"
    assert setup_count.read_text() == "x"

    marker.touch()
    recovered = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="review",
    )

    assert recovered.status == "completed"
    assert recovered.results["result"] == "recovered"
    assert setup_count.read_text() == "x"

    run_log = json.loads((base_dir / "runs" / thread_id / "run.json").read_text())
    assert [state["name"] for state in run_log["states"]].count("setup") == 1
    assert len(run_log["recoveries"]) == 1
    assert run_log["recoveries"][0]["from_state"] == "review"
    assert run_log["recoveries"][0]["started_at"]


def test_recovery_jump_can_be_repeated_after_another_non_success(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / ".fdsx"
    marker = tmp_path / "fixed"
    setup_count = tmp_path / "setup-count"
    flow_path = _write_review_loop(
        tmp_path,
        marker=marker,
        setup_count=setup_count,
    )
    thread_id = "recovery-repeated"

    first = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert first.status == "max_loop_reached"

    still_failing = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="review",
    )
    assert still_failing.status == "max_loop_reached"

    marker.touch()
    recovered = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="review",
    )
    assert recovered.status == "completed"
    assert setup_count.read_text() == "x"

    run_log = json.loads((base_dir / "runs" / thread_id / "run.json").read_text())
    assert [recovery["from_state"] for recovery in run_log["recoveries"]] == [
        "review",
        "review",
    ]


def test_recovery_jump_supersedes_pending_failed_task(tmp_path: Path) -> None:
    base_dir = tmp_path / ".fdsx"
    setup_count = tmp_path / "setup-count"
    failed_count = tmp_path / "failed-count"
    flow_path = tmp_path / "pending.yaml"
    flow_path.write_text(
        textwrap.dedent(
            f"""\
            name: pending-recovery
            description: Pending task must be superseded
            start_at: setup
            states:
              setup:
                type: task
                provider: system
                command: "printf s >> {setup_count}"
                next: fragile
              fragile:
                type: task
                provider: system
                command: "printf f >> {failed_count}; exit 1"
                retry: 0
                next: done
              done:
                type: task
                provider: system
                command: "echo recovered"
                result_path: $.result
                end: true
            """
        )
    )
    thread_id = "recovery-pending"

    with pytest.raises(RuntimeError, match="Flow execution failed"):
        run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert setup_count.read_text() == "s"
    assert failed_count.read_text() == "f"

    flow_path.write_text(flow_path.read_text().replace("next: fragile", "next: done"))
    recovered = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="setup",
    )

    assert recovered.status == "completed"
    assert recovered.results["result"] == "recovered"
    assert setup_count.read_text() == "ss"
    assert failed_count.read_text() == "f"


def test_terminal_resume_without_state_lists_recovery_candidates(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / ".fdsx"
    marker = tmp_path / "fixed"
    setup_count = tmp_path / "setup-count"
    flow_path = _write_review_loop(
        tmp_path,
        marker=marker,
        setup_count=setup_count,
    )
    thread_id = "recovery-candidates"
    result = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert result.status == "max_loop_reached"

    with pytest.raises(RuntimeError, match="Eligible states: setup, review, route"):
        resume_flow(thread_id, base_dir=base_dir)

    run_log = json.loads((base_dir / "runs" / thread_id / "run.json").read_text())
    assert run_log["status"] == "max_loop_reached"


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("missing", "does not exist in the current workflow"),
        ("done", "was not executed in this thread"),
        ("__fdsx_max_loop__", "does not exist in the current workflow"),
    ],
)
def test_recovery_jump_rejects_invalid_targets(
    tmp_path: Path,
    target: str,
    message: str,
) -> None:
    base_dir = tmp_path / ".fdsx"
    marker = tmp_path / "fixed"
    setup_count = tmp_path / "setup-count"
    flow_path = _write_review_loop(
        tmp_path,
        marker=marker,
        setup_count=setup_count,
    )
    thread_id = f"recovery-invalid-{target}"
    result = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert result.status == "max_loop_reached"

    with pytest.raises(RuntimeError, match=message):
        resume_flow(
            thread_id,
            base_dir=base_dir,
            from_state=target,
        )


def test_recovery_jump_resumes_after_max_iterations(tmp_path: Path) -> None:
    base_dir = tmp_path / ".fdsx"
    marker = tmp_path / "fixed"
    flow_path = tmp_path / "max-iterations.yaml"
    flow_path.write_text(
        textwrap.dedent(
            f"""\
            name: max-iterations-recovery
            description: Recover after a state exhausts its entry budget
            start_at: review
            max_loop: 10
            states:
              review:
                type: task
                provider: system
                command: "if [ -f {marker} ]; then echo APPROVE; else echo REJECT; fi"
                result_path: $.review_output
                extract:
                  strategy: [keyword]
                  pattern: "APPROVE|REJECT"
                  result_path: $.review_decision
                max_iterations: 1
                next: route
              route:
                type: choice
                choices:
                  - variable: $.review_decision
                    operator: equals
                    value: APPROVE
                    next: done
                default: review
              done:
                type: task
                provider: system
                command: "echo recovered"
                result_path: $.result
                end: true
            """
        )
    )
    thread_id = "recovery-max-iterations"

    with pytest.raises(RuntimeError, match="max_iterations limit"):
        run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)

    with pytest.raises(RuntimeError, match="requires an explicit recovery state"):
        resume_flow(thread_id, base_dir=base_dir)

    marker.touch()
    recovered = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="review",
    )
    assert recovered.status == "completed"
    assert recovered.results["result"] == "recovered"


def test_recovery_jump_can_leave_explicit_fail_terminal(tmp_path: Path) -> None:
    base_dir = tmp_path / ".fdsx"
    flow_path = tmp_path / "fail.yaml"
    flow_path.write_text(
        textwrap.dedent(
            """\
            name: fail-recovery
            description: Recover after an explicit fail state
            start_at: setup
            states:
              setup:
                type: task
                provider: system
                command: "echo original"
                result_path: $.latest
                next: stop
              stop:
                type: fail
                error: ReviewDidNotConverge
                cause: Human intervention is required
            """
        )
    )
    thread_id = "recovery-fail"
    result = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert result.status == "aborted"

    with pytest.raises(RuntimeError, match="cannot be used as a recovery target"):
        resume_flow(thread_id, base_dir=base_dir, from_state="stop")

    flow_path.write_text(flow_path.read_text().replace("echo original", "echo updated"))
    still_failing = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="setup",
    )
    assert still_failing.status == "aborted"

    flow_path.write_text(
        flow_path.read_text()
        .replace(
            "echo updated",
            "test {latest} = updated && echo recovered",
        )
        .replace("next: stop", "next: done")
        .replace(
            "  stop:\n",
            "  done:\n"
            "    type: task\n"
            "    provider: system\n"
            '    command: "echo recovered"\n'
            "    result_path: $.result\n"
            "    end: true\n"
            "  stop:\n",
        )
    )
    recovered = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="setup",
    )
    assert recovered.status == "completed"
    assert recovered.results["result"] == "recovered"


def test_recovery_jump_can_leave_abort_named_terminal(tmp_path: Path) -> None:
    base_dir = tmp_path / ".fdsx"
    flow_path = tmp_path / "abort.yaml"
    flow_path.write_text(
        textwrap.dedent(
            """\
            name: abort-recovery
            description: Recover after an abort-named terminal state
            start_at: setup
            states:
              setup:
                type: task
                provider: system
                command: "echo setup"
                next: abort_blocked
              abort_blocked:
                type: task
                provider: system
                command: "echo blocked"
                end: true
            """
        )
    )
    thread_id = "recovery-abort"
    result = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert result.status == "aborted"

    flow_path.write_text(
        flow_path.read_text()
        .replace("next: abort_blocked", "next: done")
        .replace(
            "  abort_blocked:\n",
            "  done:\n"
            "    type: task\n"
            "    provider: system\n"
            '    command: "echo recovered"\n'
            "    result_path: $.result\n"
            "    end: true\n"
            "  abort_blocked:\n",
        )
    )
    recovered = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="setup",
    )

    assert recovered.status == "completed"
    assert recovered.results["result"] == "recovered"


def test_recovery_jump_rejects_successfully_completed_thread(tmp_path: Path) -> None:
    base_dir = tmp_path / ".fdsx"
    flow_path = tmp_path / "completed.yaml"
    flow_path.write_text(
        textwrap.dedent(
            """\
            name: completed-recovery
            description: Completed workflows cannot recover
            start_at: done
            states:
              done:
                type: task
                provider: system
                command: "echo done"
                end: true
            """
        )
    )
    thread_id = "recovery-completed"
    result = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert result.status == "completed"

    with pytest.raises(RuntimeError, match="already completed successfully"):
        resume_flow(
            thread_id,
            base_dir=base_dir,
            from_state="done",
        )


def test_recovery_jump_validates_current_workflow_inputs_before_execution(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / ".fdsx"
    side_effect = tmp_path / "review-ran"
    flow_path = tmp_path / "workflow-drift.yaml"
    flow_path.write_text(
        textwrap.dedent(
            """\
            name: workflow-drift
            description: Current workflow prerequisites must be present
            start_at: setup
            states:
              setup:
                type: task
                provider: system
                command: "echo setup"
                next: review
              review:
                type: task
                provider: system
                command: "echo old review"
                next: stop
              stop:
                type: fail
                error: NeedsRecovery
                cause: Update the workflow before recovery
            """
        )
    )
    thread_id = "recovery-workflow-drift"
    result = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert result.status == "aborted"

    flow_path.write_text(
        textwrap.dedent(
            f"""\
            name: workflow-drift
            description: Current workflow prerequisites must be present
            start_at: setup
            states:
              setup:
                type: task
                provider: system
                command: "echo setup"
                next: context
              context:
                type: task
                provider: system
                command: "echo context"
                result_path: $.new_context
                next: review
              review:
                type: task
                provider: system
                command: "printf ran >> {side_effect}; echo {{new_context}}"
                next: stop
              stop:
                type: fail
                error: NeedsRecovery
                cause: Update the workflow before recovery
            """
        )
    )

    with pytest.raises(RuntimeError, match="missing required variables: new_context"):
        resume_flow(
            thread_id,
            base_dir=base_dir,
            from_state="review",
        )
    assert not side_effect.exists()


def test_recovery_jump_resets_failed_parallel_branch_accumulator(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / ".fdsx"
    flow_path = tmp_path / "parallel.yaml"
    flow_path.write_text(
        textwrap.dedent(
            """\
            name: parallel-recovery
            description: Parallel recovery must not duplicate old branches
            start_at: review
            states:
              review:
                type: parallel
                branches:
                  - provider: system
                    command: "echo first"
                  - provider: system
                    command: "echo failed; exit 1"
                    retry: 0
                result_path: $.reviews
                min_success: 2
                next: done
              done:
                type: task
                provider: system
                command: "echo recovered"
                result_path: $.result
                end: true
            """
        )
    )
    thread_id = "recovery-parallel"
    with pytest.raises(RuntimeError, match="Parallel state 'review' failed"):
        run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)

    flow_path.write_text(
        flow_path.read_text().replace("echo failed; exit 1", "echo second")
    )
    recovered = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="review",
    )

    assert recovered.status == "completed"
    assert len(recovered.results["reviews"]) == 2
    assert [review["output"] for review in recovered.results["reviews"]] == [
        "first",
        "second",
    ]


def test_recovery_jump_resets_map_progress(tmp_path: Path) -> None:
    base_dir = tmp_path / ".fdsx"
    iteration_log = tmp_path / "map-iterations"
    flow_path = tmp_path / "map.yaml"
    flow_path.write_text(
        textwrap.dedent(
            f"""\
            name: map-recovery
            description: Map recovery must start a fresh pass
            start_at: setup
            states:
              setup:
                type: pass
                parameters:
                  $.items: [1, 2]
                next: process
              process:
                type: map
                items_path: $.items
                iterator:
                  states:
                    - type: task
                      name: item
                      provider: system
                      command: "printf {{item}} >> {iteration_log}; if [ {{item}} = 2 ]; then exit 1; fi; echo {{item}}"
                      result_path: $.value
                      retry: 0
                result_path: $.results
                fail_fast: true
                next: done
              done:
                type: task
                provider: system
                command: "echo recovered"
                result_path: $.result
                end: true
            """
        )
    )
    thread_id = "recovery-map"
    with pytest.raises(RuntimeError, match="iteration 1 failed"):
        run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert iteration_log.read_text() == "12"

    flow_path.write_text(
        flow_path.read_text().replace(
            "if [ {item} = 2 ]; then exit 1; fi; ",
            "",
        )
    )
    recovered = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="process",
    )

    assert recovered.status == "completed"
    assert recovered.results["results"] == ["1", "2"]
    assert iteration_log.read_text() == "1212"


def test_recovery_jump_accepts_map_iterator_internal_dependencies(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / ".fdsx"
    flow_path = tmp_path / "map-pipeline.yaml"
    flow_path.write_text(
        textwrap.dedent(
            """\
            name: map-pipeline-recovery
            description: Iterator-local outputs are not checkpoint prerequisites
            start_at: setup
            states:
              setup:
                type: pass
                parameters:
                  $.items: [one]
                next: process
              process:
                type: map
                items_path: $.items
                iterator:
                  states:
                    - type: task
                      name: prepare
                      provider: system
                      command: "echo prepared-{item}"
                      result_path: $.prepared
                    - type: task
                      name: consume
                      provider: system
                      command: "echo {prepared}"
                      result_path: $.value
                result_path: $.results
                next: stop
              stop:
                type: fail
                error: NeedsRecovery
                cause: Recover the completed map
            """
        )
    )
    thread_id = "recovery-map-internal-dependency"
    first = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert first.status == "aborted"

    flow_path.write_text(
        flow_path.read_text()
        .replace("next: stop", "next: done")
        .replace(
            "  stop:\n",
            "  done:\n"
            "    type: task\n"
            "    provider: system\n"
            '    command: "echo recovered"\n'
            "    end: true\n"
            "  stop:\n",
        )
    )
    recovered = resume_flow(
        thread_id,
        base_dir=base_dir,
        from_state="process",
    )

    assert recovered.status == "completed"
    assert recovered.results["results"] == ["prepared-one"]


def test_wait_recovery_preserves_workflow_and_state_hook_lifecycle(
    tmp_path: Path,
) -> None:
    base_dir = tmp_path / ".fdsx"
    workflow_start_log = tmp_path / "workflow-start"
    workflow_end_log = tmp_path / "workflow-end"
    state_start_log = tmp_path / "state-start"
    state_end_log = tmp_path / "state-end"
    wait_start_log = tmp_path / "wait-start"
    wait_end_log = tmp_path / "wait-end"
    flow_path = tmp_path / "wait.yaml"
    flow_path.write_text(
        textwrap.dedent(
            f"""\
            name: wait-recovery
            description: Wait recovery follows normal hook lifecycle
            start_at: approval
            hooks:
              on_workflow_start:
                - command: 'printf "$FDSX_STATUS," >> {workflow_start_log}'
              on_workflow_end:
                - command: 'printf "$FDSX_STATUS," >> {workflow_end_log}'
            states:
              approval:
                type: wait
                message: "Continue?"
                choices: ["yes"]
                result_path: $.answer
                hooks:
                  on_state_start:
                    - command: 'printf "$FDSX_STATUS," >> {state_start_log}'
                  on_state_end:
                    - command: 'printf "$FDSX_STATUS," >> {state_end_log}'
                  on_wait_start:
                    - command: 'printf "$FDSX_STATUS," >> {wait_start_log}'
                  on_wait_end:
                    - command: 'printf "$FDSX_STATUS," >> {wait_end_log}'
                next: stop
              stop:
                type: fail
                error: NeedsRecovery
                cause: Change the route and recover
            """
        )
    )
    thread_id = "recovery-wait-hooks"
    with patch("builtins.input", return_value="1"):
        first = run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)
    assert first.status == "aborted"

    flow_path.write_text(
        flow_path.read_text()
        .replace("next: stop", "next: done")
        .replace(
            "  stop:\n",
            "  done:\n"
            "    type: task\n"
            "    provider: system\n"
            '    command: "echo recovered"\n'
            "    end: true\n"
            "  stop:\n",
        )
    )
    with patch("builtins.input", return_value="1"):
        recovered = resume_flow(
            thread_id,
            base_dir=base_dir,
            from_state="approval",
        )

    assert recovered.status == "completed"
    assert workflow_start_log.read_text() == "starting,"
    assert workflow_end_log.read_text() == "aborted,completed,"
    assert state_start_log.read_text() == "starting,starting,"
    assert state_end_log.read_text() == ("failed,completed,failed,completed,")
    assert wait_start_log.read_text() == "starting,starting,"
    assert wait_end_log.read_text() == "completed,completed,"

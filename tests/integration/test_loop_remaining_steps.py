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

    def test_loop_fires_at_exact_max_loop_with_postloop_states(self, tmp_path):
        """Guard fires at exactly max_loop=2 even when 8 non-loop states exist.

        Regression test for the bug where loop_guard_threshold = total_states caused
        the guard to fire after 6 iterations instead of 2 for a plan→decide→plan loop
        with 8 extra post-loop states.
        """
        import textwrap

        counter_file = tmp_path / "plan_counter.txt"

        # Build a flow inline that counts plan invocations via a file counter
        flow_yaml = textwrap.dedent(f"""
            name: Loop With Post-Loop States
            description: Loop body with 8 extra post-loop states to test guard fires at exact max_loop
            start_at: plan
            max_loop: 2

            states:
              plan:
                type: task
                provider: system
                command: "echo x >> {counter_file} && echo Planning step"
                result_path: $.plan_output
                next: decide

              decide:
                type: choice
                choices:
                  - variable: $.plan_output
                    operator: equals
                    value: "APPROVED"
                    next: post1
                default: plan

              post1:
                type: task
                provider: system
                command: echo post1
                result_path: $.post1
                next: post2
              post2:
                type: task
                provider: system
                command: echo post2
                result_path: $.post2
                next: post3
              post3:
                type: task
                provider: system
                command: echo post3
                result_path: $.post3
                next: post4
              post4:
                type: task
                provider: system
                command: echo post4
                result_path: $.post4
                next: post5
              post5:
                type: task
                provider: system
                command: echo post5
                result_path: $.post5
                next: post6
              post6:
                type: task
                provider: system
                command: echo post6
                result_path: $.post6
                next: post7
              post7:
                type: task
                provider: system
                command: echo post7
                result_path: $.post7
                next: post8
              post8:
                type: task
                provider: system
                command: echo post8
                result_path: $.post8
                end: true
        """)

        flow_path = tmp_path / "loop_postloop.yaml"
        flow_path.write_text(flow_yaml)

        result = run_flow(flow_path, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        assert result.status != "error", (
            f"Expected clean exit, got error status. results={result.results}"
        )
        assert "plan_output" in result.results, (
            f"plan_output missing: {list(result.results.keys())}"
        )

        # Count how many times plan actually ran — must be exactly 2 (max_loop=2)
        plan_count = len(counter_file.read_text().strip().splitlines())
        assert plan_count == 2, (
            f"Expected plan to run exactly 2 times (max_loop=2), ran {plan_count} times"
        )

"""Integration tests for partial state update behavior.

T006 and T008 are intentionally RED (failing) until the node refactors in T010/T012
replace set_jsonpath with set_jsonpath_partial in the task and pass node factories.
T007 is a regression guard that is GREEN now and must remain GREEN after the refactors.
"""

from fdsx.core.compiler.nodes import _create_pass_node, _create_task_node
from fdsx.core.engine import FlowResult, run_flow
from fdsx.models.flow import ExtractRule, Flow, PassState, TaskState
from tests import FIXTURES_DIR


class TestTaskNodePartialUpdate:
    """T006: Task node should return only modified keys (partial update)."""

    def test_task_node_returns_only_modified_keys(self):
        """Task node must not echo back keys it did not modify.

        RED until T010 replaces set_jsonpath with set_jsonpath_partial in
        _create_task_node.
        """
        state_def = TaskState(
            type="task",
            provider="system",
            command="echo hello",
            result_path="$.result",
            end=True,
        )
        flow = Flow(
            name="t",
            description="partial update test",
            start_at="s1",
            states={"s1": state_def},
        )
        node_fn = _create_task_node("s1", state_def, flow)
        result = node_fn(
            {"unrelated_key": "should_not_appear", "_state_iterations": {}}
        )

        # RED until T010: currently full state dict is returned, including unrelated_key
        assert "unrelated_key" not in result
        assert set(result) == {"result", "_state_iterations"}


class TestParallelFlowPartialUpdate:
    """T007: Parallel flow regression guard — must remain GREEN before and after refactors."""

    def test_parallel_review_accumulation(self, tmp_path):
        """End-to-end parallel flow with majority aggregation produces correct results.

        This test verifies the parallel branch accumulation pattern works correctly.
        It is GREEN now and must remain GREEN after all node refactors land.
        """
        path = FIXTURES_DIR / "parallel_review.yaml"
        result = run_flow(path, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        assert "reviews" in result.results
        assert len(result.results["reviews"]) == 3
        assert "decision" in result.results
        assert result.results["decision"] == "APPROVED"


class TestTaskNodeSiblingPaths:
    """When extract.result_path and result_path share a parent, both keys survive."""

    def test_task_node_with_extract_siblings_both_preserved(self):
        """When extract.result_path and result_path share a parent, both keys survive."""
        state_def = TaskState(
            type="task",
            provider="system",
            command="echo '{\"value\": 42}'",
            result_path="$.result.raw",
            extract=ExtractRule(
                strategy=["json"],
                pattern="value",
                result_path="$.result.parsed",
            ),
            end=True,
        )
        flow = Flow(
            name="t",
            description="sibling path test",
            start_at="s1",
            states={"s1": state_def},
        )
        node_fn = _create_task_node("s1", state_def, flow)
        result = node_fn({"_state_iterations": {}})

        assert "raw" in result["result"]
        assert "parsed" in result["result"]


class TestPassNodePartialUpdate:
    """T008: Pass node should return only modified keys (partial update)."""

    def test_pass_node_returns_only_parameter_targets(self):
        """Pass node must not echo back keys not targeted by parameters.

        RED until T012 replaces full-state mutations with partial returns in
        _create_pass_node.
        """
        state_def = PassState(
            type="pass",
            parameters={"$.output": "hello"},
            end=True,
        )
        flow = Flow(
            name="t",
            description="partial update test",
            start_at="s1",
            states={"s1": state_def},
        )
        node_fn = _create_pass_node("s1", state_def, flow)
        result = node_fn({"noise_key": "noise_value", "_state_iterations": {}})

        # RED until T012: currently full state_dict is returned, including noise_key
        assert "noise_key" not in result
        assert "output" in result
        assert "_state_iterations" in result

    def test_pass_node_sibling_parameters_both_preserved(self):
        """Two parameters targeting sibling paths under the same parent must both survive."""
        state_def = PassState(
            type="pass",
            parameters={"$.review.summary": "good", "$.review.decision": "APPROVED"},
            end=True,
        )
        flow = Flow(
            name="t",
            description="sibling test",
            start_at="s1",
            states={"s1": state_def},
        )
        node_fn = _create_pass_node("s1", state_def, flow)
        result = node_fn({"_state_iterations": {}})

        assert result["review"]["summary"] == "good"
        assert result["review"]["decision"] == "APPROVED"

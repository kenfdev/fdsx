from __future__ import annotations

import pytest

from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow
from fdsx.models.flow import PassState
from tests import FIXTURES_DIR


class TestMapBasic:
    def test_map_basic_ordered_results(self, tmp_path):
        """Test basic map iteration with 3 items and 2-state iterator.

        Verifies:
        - Ordered results array at $.map_results
        - Each item is processed by both iterator states
        """
        path = FIXTURES_DIR / "map_basic.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "map_results" in result
        assert len(result["map_results"]) == 3

        assert result["map_results"][0] == "step2-item1"
        assert result["map_results"][1] == "step2-item2"
        assert result["map_results"][2] == "step2-item3"

        assert "after_result" in result
        assert result["after_result"] == "done"


class TestMapEmptyItems:
    def test_map_empty_items_continues(self, tmp_path):
        """Test that empty items array results in empty results and flow continues."""
        path = FIXTURES_DIR / "map_empty_items.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "map_results" in result
        assert result["map_results"] == []

        assert "after_result" in result
        assert result["after_result"] == "map-completed"


class TestMapFailFast:
    def test_map_fail_fast_true_raises(self, tmp_path):
        """Test that fail_fast: true raises error on iteration failure."""
        from fdsx.core.compiler import compile_flow

        flow = flow_with_failing_iter()
        compiled = compile_flow(flow)

        with pytest.raises(RuntimeError, match=r"iteration 1 failed"):
            compiled.graph.invoke({})


class TestMapFailFastFalse:
    def test_map_fail_fast_false_collects_results(self, tmp_path):
        """Test fail_fast: false collects results from all iterations even with failures.

        With fail_fast: false, all iterations are attempted, the results array is
        populated with nulls for failed iterations, but an error is still raised
        after all iterations complete.
        """
        from unittest.mock import patch

        from fdsx.core.compiler import compile_flow
        from fdsx.models.flow import (
            Flow,
            IteratorDef,
            IteratorTaskState,
            MapState,
            PassState,
        )

        flow = Flow(
            name="Fail Fast False Test",
            description="Test fail_fast false behavior",
            start_at="setup",
            states={
                "setup": PassState(
                    type="pass",
                    parameters={"$.items": ["ok1", "fail", "ok2"]},
                    next="map_state",
                ),
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo_item",
                                provider="system",
                                command='sh -c "test {item} = fail && exit 1 || echo ok-{item}"',
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    fail_fast=False,
                    next="after_map",
                ),
                "after_map": PassState(
                    type="pass",
                    parameters={"$.after_result": "done"},
                    end=True,
                ),
            },
        )

        compiled = compile_flow(flow)
        initial_state = {
            "_meta": {
                "thread_id": "test",
                "flow_path": "",
                "flow_name": "test",
                "run_dir": "",
            },
            "_state_iterations": {},
        }

        captured_results = []
        original_set = __import__(
            "fdsx.core.variables", fromlist=["set_jsonpath"]
        ).set_jsonpath

        def spy_set_jsonpath(path, state, value):
            if path == "$.results":
                captured_results.append(value)
            return original_set(path, state, value)

        with (
            patch(
                "fdsx.core.compiler.map_iteration.set_jsonpath",
                side_effect=spy_set_jsonpath,
            ),
            pytest.raises(RuntimeError, match=r"1 of 3 iterations failed"),
        ):
            compiled.graph.invoke(initial_state)

        assert len(captured_results) == 1
        results = captured_results[0]
        assert len(results) == 3
        assert results[0] is not None
        assert results[0] == "ok-ok1"
        assert results[1] is None
        assert results[2] is not None
        assert results[2] == "ok-ok2"


class TestMapInsideParallel:
    def test_map_after_parallel_no_interference(self, tmp_path):
        """Test that map state after parallel state works correctly.

        Verifies:
        - Parallel branch results are collected
        - Map results are collected separately
        - Both features don't interfere with each other
        """
        path = FIXTURES_DIR / "map_inside_parallel.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "parallel_results" in result
        assert len(result["parallel_results"]) == 2

        assert "map_results" in result
        assert len(result["map_results"]) == 2
        assert result["map_results"][0] == "mapped-x"
        assert result["map_results"][1] == "mapped-y"


class TestMapItemVariableResolution:
    def test_map_resolves_item_variable(self, tmp_path):
        """Test that ${item} is properly resolved in iterator task commands."""
        from fdsx.core.compiler import compile_flow
        from fdsx.models.flow import Flow, IteratorDef, IteratorTaskState, MapState

        flow = Flow(
            name="Item Resolution Test",
            description="Test item variable resolution",
            start_at="map_state",
            states={
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo_item",
                                provider="system",
                                command="echo ITEM_IS_{item}",
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    end=True,
                ),
            },
        )

        compiled = compile_flow(flow)
        initial_state = {
            "items": ["alpha", "beta", "gamma"],
            "_meta": {
                "thread_id": "test",
                "flow_path": "",
                "flow_name": "test",
                "run_dir": "",
            },
            "_state_iterations": {},
        }

        result = compiled.graph.invoke(initial_state)

        assert "results" in result
        assert len(result["results"]) == 3
        assert result["results"][0] == "ITEM_IS_alpha"
        assert result["results"][1] == "ITEM_IS_beta"
        assert result["results"][2] == "ITEM_IS_gamma"


class TestMapInvalidItemsPath:
    def test_map_items_path_not_resolved_raises(self, tmp_path):
        """items_path that doesn't resolve raises RuntimeError."""
        from fdsx.core.compiler import compile_flow
        from fdsx.models.flow import Flow, IteratorDef, IteratorTaskState, MapState

        flow = Flow(
            name="Invalid Items Path",
            description="Test unresolved items_path",
            start_at="map_state",
            states={
                "map_state": MapState(
                    type="map",
                    items_path="$.nonexistent",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo",
                                provider="system",
                                command="echo hi",
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    end=True,
                ),
            },
        )

        compiled = compile_flow(flow)
        with pytest.raises(RuntimeError, match=r"did not resolve to a value"):
            compiled.graph.invoke({})

    def test_map_items_path_non_list_raises(self, tmp_path):
        """items_path resolving to non-list raises RuntimeError."""
        from fdsx.core.compiler import compile_flow
        from fdsx.models.flow import (
            Flow,
            IteratorDef,
            IteratorTaskState,
            MapState,
            PassState,
        )

        flow = Flow(
            name="Non-List Items",
            description="Test non-list items_path",
            start_at="setup",
            states={
                "setup": PassState(
                    type="pass",
                    parameters={"$.items": "not-a-list"},
                    next="map_state",
                ),
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo",
                                provider="system",
                                command="echo hi",
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    end=True,
                ),
            },
        )

        compiled = compile_flow(flow)
        with pytest.raises(RuntimeError, match=r"expected list"):
            compiled.graph.invoke(
                {
                    "_meta": {
                        "thread_id": "t",
                        "flow_path": "",
                        "flow_name": "t",
                        "run_dir": "",
                    },
                    "_state_iterations": {},
                }
            )


class TestMapItemFieldResolution:
    def test_map_resolves_item_field(self, tmp_path):
        """Test that ${item.field} resolves nested object fields."""
        from fdsx.core.compiler import compile_flow
        from fdsx.models.flow import Flow, IteratorDef, IteratorTaskState, MapState

        flow = Flow(
            name="Item Field Resolution",
            description="Test nested item field resolution",
            start_at="map_state",
            states={
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo_field",
                                provider="system",
                                command="echo {item.name}-{item.value}",
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    end=True,
                ),
            },
        )

        compiled = compile_flow(flow)
        initial_state = {
            "items": [
                {"name": "alice", "value": "100"},
                {"name": "bob", "value": "200"},
            ],
            "_meta": {
                "thread_id": "t",
                "flow_path": "",
                "flow_name": "t",
                "run_dir": "",
            },
            "_state_iterations": {},
        }

        result = compiled.graph.invoke(initial_state)

        assert "results" in result
        assert len(result["results"]) == 2
        assert result["results"][0] == "alice-100"
        assert result["results"][1] == "bob-200"


def flow_with_failing_iter():
    """Create a flow with a map state that fails on second iteration."""
    from fdsx.models.flow import Flow, IteratorDef, IteratorTaskState, MapState

    return Flow(
        name="Fail Fast Test",
        description="Test fail_fast true behavior",
        start_at="setup",
        states={
            "setup": _pass_state("$.items", ["ok", "fail"]),
            "map_state": MapState(
                type="map",
                items_path="$.items",
                iterator=IteratorDef(
                    states=[
                        IteratorTaskState(
                            name="echo_item",
                            provider="system",
                            command='if [ "{item}" = "fail" ]; then exit 1; fi; echo "{item}"',
                            result_path="$.result",
                        )
                    ]
                ),
                result_path="$.results",
                fail_fast=True,
                end=True,
            ),
        },
    )


def _pass_state(result_path: str, value: list) -> PassState:
    """Helper to create a pass state that sets a variable to a value."""
    return PassState(
        type="pass",
        parameters={result_path: value},
        next="map_state",
    )

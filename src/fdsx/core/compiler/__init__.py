"""Compiler package re-export facade.

All public and tested-private symbols are re-exported here so that
existing imports like ``from fdsx.core.compiler import compile_flow`` continue
to work without changes.
"""

from .aggregation import _aggregate
from .compile import CompiledGraph, FlowState, _wrap_with_hooks, compile_flow
from .helpers import (
    _check_max_iterations,
    _extract_result_paths,
    _get_next_state,
    _merge_provider_options,
    _parallel_branch_reducer,
    _set_next_state_meta,
    _top_level_key,
)
from .nodes import _create_task_node
from .parallel import _create_branch_executor, _create_collector_node
from .routing import _create_routing_function, _evaluate_condition, _resolve_jsonpath

__all__ = [
    "CompiledGraph",
    "FlowState",
    "_aggregate",
    "_check_max_iterations",
    "_create_branch_executor",
    "_create_collector_node",
    "_create_routing_function",
    "_create_task_node",
    "_evaluate_condition",
    "_extract_result_paths",
    "_get_next_state",
    "_merge_provider_options",
    "_parallel_branch_reducer",
    "_resolve_jsonpath",
    "_set_next_state_meta",
    "_top_level_key",
    "_wrap_with_hooks",
    "compile_flow",
]

import sys
import uuid
from pathlib import Path
from typing import Any

from langgraph.errors import GraphRecursionError

from fdsx.core.compiler import compile_flow
from fdsx.core.loader import load_flow


class FlowValidationError(Exception):
    """Raised when flow validation fails."""

    pass


def run_flow(
    flow_path: Path,
    inputs: dict[str, str] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Run a flow from a YAML file.

    Args:
        flow_path: Path to the YAML workflow file
        inputs: Optional input variables
        thread_id: Optional thread ID (generated if not provided)

    Returns:
        Final state variables as result dict. When max_loop is reached,
        returns partial results from the last completed iteration rather
        than raising an error.

    Raises:
        RuntimeError: If flow validation fails or execution fails
    """
    if thread_id is None:
        thread_id = str(uuid.uuid4())

    print(f"Thread ID: {thread_id}", file=sys.stderr)

    flow, errors = load_flow(
        flow_path, input_keys=set(inputs.keys()) if inputs else None
    )
    if flow is None:
        raise FlowValidationError(f"Flow validation failed: {', '.join(errors)}")

    compiled = compile_flow(flow, input_keys=set(inputs.keys()) if inputs else None)

    initial_state: dict[str, Any] = {"_meta": {"thread_id": thread_id}}

    if inputs:
        for key, value in inputs.items():
            initial_state[key] = value

    # Compute recursion limit: account for extra nodes added by Send API fan-out/fan-in
    from fdsx.models.flow import ParallelState

    parallel_extra = sum(
        len(s.branches) + 1
        for s in flow.states.values()
        if isinstance(s, ParallelState)
    )
    steps_per_iter = len(flow.states) + parallel_extra
    recursion_limit = flow.max_loop * steps_per_iter + 1

    config: dict[str, Any] = {"recursion_limit": recursion_limit}

    # Track the last successfully completed state so it can be returned on loop exhaustion
    last_state: dict[str, Any] = initial_state.copy()

    try:
        for state_snapshot in compiled.graph.stream(
            initial_state, config=config, stream_mode="values"
        ):
            last_state = state_snapshot
        return _extract_results(last_state, compiled.result_paths)
    except GraphRecursionError:
        print(f"Loop completed after {flow.max_loop} iterations", file=sys.stderr)
        return _extract_results(last_state, compiled.result_paths)
    except Exception as e:
        raise RuntimeError(f"Flow execution failed: {e}")


def _extract_results(state: dict[str, Any], result_paths: list[str]) -> dict[str, Any]:
    """Extract result values from final state preserving nested paths."""
    from fdsx.core.variables import resolve_jsonpath, set_jsonpath

    results: dict[str, Any] = {}
    for path in result_paths:
        clean_path = path[2:] if path.startswith("$.") else path
        value = resolve_jsonpath(clean_path, state)
        if value is not None:
            results = set_jsonpath(clean_path, results, value)

    return results


def validate_flow(flow_path: Path) -> tuple[bool, list[str]]:
    """Validate a flow without executing it.

    Args:
        flow_path: Path to the YAML workflow file

    Returns:
        tuple of (is_valid, list of error messages)
    """
    flow, errors = load_flow(flow_path)
    return flow is not None, errors

import sys
import uuid
from pathlib import Path
from typing import Any

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
        Final state variables as result dict

    Raises:
        RuntimeError: If flow validation fails or execution fails
    """
    if thread_id is None:
        thread_id = str(uuid.uuid4())

    print(f"Thread ID: {thread_id}", file=sys.stderr)

    flow, errors = load_flow(flow_path, input_keys=set(inputs.keys()) if inputs else None)
    if flow is None:
        raise FlowValidationError(f"Flow validation failed: {', '.join(errors)}")

    compiled = compile_flow(flow)

    initial_state: dict[str, Any] = {"_meta": {"thread_id": thread_id}}

    if inputs:
        for key, value in inputs.items():
            initial_state[key] = value

    try:
        recursion_limit = flow.max_loop * len(flow.states) + 1
        result = compiled.graph.invoke(
            initial_state,
            config={"recursion_limit": recursion_limit},
        )
    except Exception as e:
        raise RuntimeError(f"Flow execution failed: {e}")

    return _extract_results(result, compiled.result_paths)


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

import sys
import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from fdsx.core.compiler import compile_flow
from fdsx.core.loader import load_flow
from fdsx.display.terminal import display_wait_prompt


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

    # Check if we need a checkpointer (for Wait states, interrupt requires checkpointing)
    from fdsx.models.flow import WaitState

    needs_checkpointer = any(isinstance(s, WaitState) for s in flow.states.values())
    checkpointer = MemorySaver() if needs_checkpointer else None

    compiled = compile_flow(
        flow,
        input_keys=set(inputs.keys()) if inputs else None,
        checkpointer=checkpointer,
    )

    initial_state: dict[str, Any] = {"_meta": {"thread_id": thread_id}}

    if inputs:
        for key, value in inputs.items():
            initial_state[key] = value

    # Compute recursion limit: account for extra graph nodes not in flow.states count.
    # ParallelState adds dispatch + N branch + collector (len(branches)+1 extra beyond the
    # one already counted in len(flow.states)).
    # WaitState is split into notify-pre + interrupt (1 extra beyond the one counted).
    from fdsx.models.flow import ParallelState

    parallel_extra = sum(
        len(s.branches) + 1
        for s in flow.states.values()
        if isinstance(s, ParallelState)
    )
    wait_extra = sum(1 for s in flow.states.values() if isinstance(s, WaitState))
    steps_per_iter = len(flow.states) + parallel_extra + wait_extra
    recursion_limit = flow.max_loop * steps_per_iter + 1

    config: dict[str, Any] = {
        "recursion_limit": recursion_limit,
        "configurable": {"thread_id": thread_id},
    }

    # Track the last successfully completed state so it can be returned on loop exhaustion
    last_state: dict[str, Any] = initial_state.copy()

    try:
        # Initial run — streams graph to completion or first interrupt
        for state_snapshot in compiled.graph.stream(
            initial_state, config=config, stream_mode="values"
        ):
            if "__interrupt__" not in state_snapshot:
                last_state = state_snapshot

        # Interrupt handling loop — only runs when a checkpointer is configured (Wait states)
        # Uses get_state() to inspect pending interrupts and Command(resume=) + stream() to continue.
        # Never re-passes last_state to stream(); the checkpointer handles continuity.
        if needs_checkpointer:
            while True:
                state_info = compiled.graph.get_state(config)

                # No pending tasks means the graph has completed
                if not state_info.tasks:
                    break

                # Extract interrupt payload from the first pending interrupted task
                payload = None
                for task in state_info.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        payload = task.interrupts[0].value
                        break

                if payload is None:
                    break

                message = payload.get("message", "")
                choices = payload.get("choices", [])
                state_name = payload.get("state_name", "wait")

                user_selection = display_wait_prompt(state_name, message, choices)

                # Resume from checkpoint — continues execution from the interrupt point
                for state_snapshot in compiled.graph.stream(
                    Command(resume=user_selection),
                    config=config,
                    stream_mode="values",
                ):
                    if "__interrupt__" not in state_snapshot:
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

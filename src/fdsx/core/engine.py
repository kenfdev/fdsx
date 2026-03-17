import sys
import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from fdsx.checkpoint.manager import CheckpointManager, _extract_meta_from_checkpoint
from fdsx.core.compiler import compile_flow
from fdsx.core.loader import load_flow
from fdsx.display.terminal import _sanitize_output, display_wait_prompt


class FlowValidationError(Exception):
    """Raised when flow validation fails."""

    pass


def run_flow(
    flow_path: Path,
    inputs: dict[str, str] | None = None,
    thread_id: str | None = None,
    base_dir: Path | None = None,
) -> dict[str, Any]:
    """Run a flow from a YAML file.

    Args:
        flow_path: Path to the YAML workflow file
        inputs: Optional input variables
        thread_id: Optional thread ID (generated if not provided)
        base_dir: Optional base directory for checkpoints (.fdsx/).
                  If None, uses MemorySaver (no persistence).

    Returns:
        Final state variables as result dict. When max_loop is reached,
        returns partial results from the last completed iteration rather
        than raising an error.

    Raises:
        RuntimeError: If flow validation fails or execution fails
    """
    if thread_id is None:
        thread_id = str(uuid.uuid4())

    print(f"Thread ID: {_sanitize_output(thread_id)}", file=sys.stderr)

    flow, errors = load_flow(
        flow_path, input_keys=set(inputs.keys()) if inputs else None
    )
    if flow is None:
        raise FlowValidationError(f"Flow validation failed: {', '.join(errors)}")

    from fdsx.models.flow import WaitState, ParallelState

    needs_checkpointer = any(isinstance(s, WaitState) for s in flow.states.values())

    checkpoint_manager: CheckpointManager | None = None
    checkpointer: Any = None
    if base_dir is not None:
        checkpoint_manager = CheckpointManager(base_dir=base_dir)
        if not checkpoint_manager.acquire_lock(thread_id):
            locked, pid = checkpoint_manager.is_locked(thread_id)
            if locked:
                raise RuntimeError(f"Thread {thread_id} is locked by PID {pid}")
        checkpointer = checkpoint_manager.get_checkpointer()
        needs_checkpointer = True
    elif needs_checkpointer:
        checkpointer = MemorySaver()

    compiled = compile_flow(
        flow,
        input_keys=set(inputs.keys()) if inputs else None,
        checkpointer=checkpointer,
    )

    initial_state: dict[str, Any] = {
        "_meta": {
            "thread_id": thread_id,
            "flow_path": str(flow_path),
            "flow_name": flow.name,
        }
    }

    if inputs:
        for key, value in inputs.items():
            initial_state[key] = value

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

    last_state: dict[str, Any] = initial_state.copy()

    try:
        for state_snapshot in compiled.graph.stream(
            initial_state, config=config, stream_mode="values"
        ):
            if "__interrupt__" not in state_snapshot:
                last_state = state_snapshot

        if needs_checkpointer:
            while True:
                state_info = compiled.graph.get_state(config)

                if not state_info.tasks:
                    break

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

                for state_snapshot in compiled.graph.stream(
                    Command(resume=user_selection),
                    config=config,
                    stream_mode="values",
                ):
                    if "__interrupt__" not in state_snapshot:
                        last_state = state_snapshot

        if needs_checkpointer and checkpoint_manager is not None:
            final_state_info = compiled.graph.get_state(config)
            if final_state_info.values:
                last_state = final_state_info.values

        return _extract_results(last_state, compiled.result_paths)
    except GraphRecursionError:
        print(f"Loop completed after {flow.max_loop} iterations", file=sys.stderr)
        return _extract_results(last_state, compiled.result_paths)
    except Exception as e:
        if checkpoint_manager is not None:
            print(
                f"Checkpoint saved. Resume with: fdsx resume --thread-id {_sanitize_output(thread_id)}",
                file=sys.stderr,
            )
        raise RuntimeError(f"Flow execution failed: {e}")
    finally:
        if checkpoint_manager is not None:
            checkpoint_manager.release_lock(thread_id)


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


def resume_flow(
    thread_id: str,
    base_dir: Path | None = None,
    flow_path: Path | None = None,
) -> dict[str, Any]:
    """Resume a flow from a checkpoint.

    Args:
        thread_id: The thread ID to resume
        base_dir: Base directory for checkpoints (.fdsx/). Defaults to '.fdsx/'.
        flow_path: Optional path to the flow YAML file. Required if not stored in checkpoint.

    Returns:
        Final state variables as result dict.

    Raises:
        RuntimeError: If checkpoint is corrupt or execution fails
    """
    if base_dir is None:
        base_dir = CheckpointManager.DEFAULT_BASE_DIR

    checkpoint_manager = CheckpointManager(base_dir=base_dir)

    if not checkpoint_manager.verify_checkpoint(thread_id):
        raise RuntimeError(f"No checkpoint found for thread ID {thread_id}")

    if not checkpoint_manager.acquire_lock(thread_id):
        locked, pid = checkpoint_manager.is_locked(thread_id)
        if locked:
            raise RuntimeError(f"Thread {thread_id} is locked by PID {pid}")

    print(f"Resuming from thread: {_sanitize_output(thread_id)}", file=sys.stderr)

    try:
        checkpointer = checkpoint_manager.get_checkpointer()

        if flow_path is None or not flow_path.exists():
            config_for_lookup: Any = {"configurable": {"thread_id": thread_id}}
            checkpoint = checkpointer.get(config_for_lookup)

            if checkpoint:
                stored_meta = _extract_meta_from_checkpoint(checkpoint)
                if isinstance(stored_meta, dict):
                    flow_path_str = stored_meta.get("flow_path")
                    if flow_path_str:
                        flow_path = Path(flow_path_str)

            if flow_path is None or not (flow_path and flow_path.exists()):
                raise RuntimeError(
                    f"Flow path not found for thread ID {thread_id}. "
                    "Please provide the flow YAML path using the flow_path parameter."
                )

        flow, errors = load_flow(flow_path)
        if flow is None:
            raise RuntimeError(f"Failed to load flow for resume: {', '.join(errors)}")

        from fdsx.models.flow import WaitState, ParallelState

        compiled = compile_flow(
            flow,
            checkpointer=checkpointer,
        )

        parallel_extra = sum(
            len(s.branches) + 1
            for s in flow.states.values()
            if isinstance(s, ParallelState)
        )
        wait_extra = sum(1 for s in flow.states.values() if isinstance(s, WaitState))
        steps_per_iter = len(flow.states) + parallel_extra + wait_extra
        recursion_limit = flow.max_loop * steps_per_iter + 1

        resume_config: dict[str, Any] = {
            "recursion_limit": recursion_limit,
            "configurable": {"thread_id": thread_id},
        }

        last_state: dict[str, Any] = {}

        state_info = compiled.graph.get_state(resume_config)
        if state_info.tasks:
            payload = None
            for task in state_info.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    payload = task.interrupts[0].value
                    break

            if payload:
                print(
                    f"Resuming from state: {_sanitize_output(payload.get('state_name', 'wait'))}",
                    file=sys.stderr,
                )
                message = payload.get("message", "")
                choices = payload.get("choices", [])
                state_name = payload.get("state_name", "wait")

                user_selection = display_wait_prompt(state_name, message, choices)

                for state_snapshot in compiled.graph.stream(
                    Command(resume=user_selection),
                    config=resume_config,
                    stream_mode="values",
                ):
                    if "__interrupt__" not in state_snapshot:
                        last_state = state_snapshot
            else:
                # Error/pending task (no interrupt) — re-execute from checkpoint
                for state_snapshot in compiled.graph.stream(
                    None, config=resume_config, stream_mode="values"
                ):
                    if "__interrupt__" not in state_snapshot:
                        last_state = state_snapshot
        else:
            for state_snapshot in compiled.graph.stream(
                None, config=resume_config, stream_mode="values"
            ):
                if "__interrupt__" not in state_snapshot:
                    last_state = state_snapshot

        # Continue handling any further interrupts (e.g. multi-Wait flows)
        while True:
            state_info = compiled.graph.get_state(resume_config)
            if not state_info.tasks:
                break
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
            for state_snapshot in compiled.graph.stream(
                Command(resume=user_selection),
                config=resume_config,
                stream_mode="values",
            ):
                if "__interrupt__" not in state_snapshot:
                    last_state = state_snapshot

        # Read authoritative state from checkpointer after resume completes
        final_state_info = compiled.graph.get_state(resume_config)
        if final_state_info.values:
            last_state = final_state_info.values

        return _extract_results(last_state, compiled.result_paths)
    except GraphRecursionError:
        if flow is not None:
            print(f"Loop completed after {flow.max_loop} iterations", file=sys.stderr)
        return {}
    except Exception as e:
        raise RuntimeError(f"Flow resume failed: {e}")
    finally:
        checkpoint_manager.release_lock(thread_id)


def validate_flow(flow_path: Path) -> tuple[bool, list[str]]:
    """Validate a flow without executing it.

    Args:
        flow_path: Path to the YAML workflow file

    Returns:
        tuple of (is_valid, list of error messages)
    """
    flow, errors = load_flow(flow_path)
    return flow is not None, errors

"""run_flow implementation for the engine package."""

import sys
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core.compiler import compile_flow
from fdsx.core.config import load_config
from fdsx.core.loader import load_flow
from fdsx.core.thread_id import generate_thread_id
from fdsx.display.terminal import (
    _sanitize_output,
    display_completion_summary,
)
from fdsx.logging import RunRecorder
from fdsx.logging.recorder import FDSX_DIR_NAME, LOGS_DIR_NAME, RUNS_DIR_NAME

from .interrupts import handle_interrupts
from .results import (
    FlowResult,
    _calc_elapsed,
    _detect_abort_status,
    _extract_results,
    _find_failed_state,
    _sanitize_state_for_log,
)
from .signals import SignalHandler
from .validate import FlowValidationError


def run_flow(
    flow_path: Path,
    inputs: dict[str, str] | None = None,
    thread_id: str | None = None,
    base_dir: Path | None = None,
    quiet: bool = False,
    task_file_path: Path | None = None,
    task_entry_index: int | None = None,
) -> FlowResult:
    """Run a flow from a YAML file.

    Args:
        flow_path: Path to the YAML workflow file
        inputs: Optional input variables
        thread_id: Optional thread ID (generated if not provided)
        base_dir: Optional base directory for checkpoints (.fdsx/).
                  If None, uses MemorySaver (no persistence).
        quiet: When True, suppresses stderr streaming output from StreamLogger.
               Log files are still written and completion summary is still shown.
        task_file_path: Optional path to the task YAML file. When provided along
                        with task_entry_index, stored in _meta so that resume_flow
                        can update the task entry status after completion.
        task_entry_index: Optional index of the task entry within task_file_path.

    Returns:
        Final state variables as result dict. When max_loop is reached,
        returns partial results from the last completed iteration rather
        than raising an error.

    Raises:
        RuntimeError: If flow validation fails or execution fails
    """
    if thread_id is None:
        thread_id = generate_thread_id()

    print(f"Thread ID: {_sanitize_output(thread_id)}", file=sys.stderr)

    fdsx_config = load_config(
        project_dir=base_dir.parent if base_dir is not None else None
    )

    config_profiles = None
    if fdsx_config.profiles:
        config_profiles = {
            name: prof.model_dump() for name, prof in fdsx_config.profiles.items()
        }

    flow, errors = load_flow(
        flow_path,
        input_keys=set(inputs.keys()) if inputs else None,
        config_profiles=config_profiles,
    )
    if flow is None:
        raise FlowValidationError(f"Flow validation failed: {', '.join(errors)}")

    from fdsx.models.flow import ParallelState, WaitState

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

    recorder = RunRecorder(
        thread_id=thread_id,
        flow_name=flow.name,
        flow_version=flow.version,
    )

    _runs_base = base_dir if base_dir is not None else Path.cwd() / FDSX_DIR_NAME
    run_dir = _runs_base / RUNS_DIR_NAME / thread_id
    log_dir = run_dir / LOGS_DIR_NAME

    handler = SignalHandler(checkpoint_manager, thread_id)

    compiled = compile_flow(
        flow,
        input_keys=set(inputs.keys()) if inputs else None,
        checkpointer=checkpointer,
        recorder=recorder,
        config=fdsx_config,
        log_dir=log_dir,
        quiet=quiet,
        on_process_start=handler.register_process,
    )

    initial_state: dict[str, Any] = {
        "_meta": {
            "thread_id": thread_id,
            "flow_path": str(flow_path),
            "flow_name": flow.name,
            "run_dir": str(run_dir),
            **({"task_file_path": str(task_file_path)} if task_file_path is not None else {}),
            **({"task_entry_index": task_entry_index} if task_entry_index is not None else {}),
        },
        "_state_iterations": {},
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
        with handler:
            for state_snapshot in compiled.graph.stream(
                initial_state, config=config, stream_mode="values"
            ):
                if "__interrupt__" not in state_snapshot:
                    last_state = state_snapshot

            if needs_checkpointer:
                last_state = handle_interrupts(compiled.graph, config, last_state)

        if needs_checkpointer:
            final_state_info = compiled.graph.get_state(config)
            if final_state_info.values:
                last_state = final_state_info.values

        results = _extract_results(last_state, compiled.result_paths)
        status, failed_state, error_msg = _detect_abort_status(recorder)
        recorder.finalize(_sanitize_state_for_log(last_state), status)
        recorder.save(base_dir=base_dir)
        if failed_state is not None:
            display_completion_summary(
                flow.name,
                _calc_elapsed(recorder),
                failed_state,
                error_msg or "workflow aborted",
            )
        else:
            display_completion_summary(flow.name, _calc_elapsed(recorder))
        return FlowResult(results=results, status=status, abort_state=failed_state)
    except GraphRecursionError:
        print(f"Loop completed after {flow.max_loop} iterations", file=sys.stderr)
        results = _extract_results(last_state, compiled.result_paths)
        status, failed_state, error_msg = _detect_abort_status(recorder)
        recorder.finalize(_sanitize_state_for_log(last_state), status)
        recorder.save(base_dir=base_dir)
        if failed_state is not None:
            display_completion_summary(
                flow.name,
                _calc_elapsed(recorder),
                failed_state,
                error_msg or "workflow aborted",
            )
        else:
            display_completion_summary(flow.name, _calc_elapsed(recorder))
        return FlowResult(results=results, status=status, abort_state=failed_state)
    except Exception as e:
        if checkpoint_manager is not None:
            print(
                f"Checkpoint saved. Resume with: fdsx resume --thread-id {_sanitize_output(thread_id)}",
                file=sys.stderr,
            )
        recorder.finalize(_sanitize_state_for_log(last_state), "error")
        recorder.save(base_dir=base_dir)
        failed = _find_failed_state(recorder)
        failed_state_name = failed[0] if failed else "unknown"
        error_message = failed[1] if (failed and failed[1]) else str(e)
        display_completion_summary(
            flow.name, _calc_elapsed(recorder), failed_state_name, error_message
        )
        raise RuntimeError(f"Flow execution failed: {e}") from e
    finally:
        if checkpoint_manager is not None:
            checkpoint_manager.release_lock(thread_id)

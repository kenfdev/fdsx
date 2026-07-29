"""run_flow implementation for the engine package."""

import sys
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Any

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import InvalidUpdateError
from structlog.contextvars import bind_contextvars, reset_contextvars

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core.compiler import compile_flow
from fdsx.core.config import load_config
from fdsx.core.hooks import (
    HookAbortError,
    collect_workflow_hooks,
    execute_workflow_hooks,
)
from fdsx.core.loader import load_flow
from fdsx.core.thread_id import generate_thread_id
from fdsx.display.terminal import _sanitize_output
from fdsx.logging import RunRecorder
from fdsx.logging.recorder import FDSX_DIR_NAME, LOGS_DIR_NAME, RUNS_DIR_NAME
from fdsx.models.flow import Flow, ParallelState, WaitState

from .errors import FlowExecutionError, RunLockedError
from .lifecycle import (
    GraphExecutionPlan,
    TerminalContext,
    checkpoint_lock,
    execute_lifecycle,
    finalize_failed_execution,
)
from .results import FlowResult, _detect_abort_status
from .signals import SignalHandler
from .validate import FlowValidationError

logger = structlog.get_logger(__name__)

_SETUP_ERRORS = (
    InvalidUpdateError,
    SQLiteError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    KeyError,
)


def _execute_fresh_flow(
    *,
    flow: Flow,
    flow_path: Path,
    inputs: dict[str, str] | None,
    thread_id: str,
    base_dir: Path | None,
    quiet: bool,
    task_file_path: Path | None,
    task_entry_index: int | None,
    fdsx_config: Any,
    checkpoint_manager: CheckpointManager | None,
) -> FlowResult:
    needs_checkpointer = any(isinstance(s, WaitState) for s in flow.states.values())
    checkpointer: Any = None
    if checkpoint_manager is not None:
        checkpointer = checkpoint_manager.get_checkpointer()
        needs_checkpointer = True
    elif needs_checkpointer:
        checkpointer = MemorySaver()

    recorder = RunRecorder(
        thread_id=thread_id,
        flow_name=flow.name,
        flow_version=flow.version,
        flow_path=str(flow_path),
    )
    runs_base = base_dir if base_dir is not None else Path.cwd() / FDSX_DIR_NAME
    run_dir = runs_base / RUNS_DIR_NAME / thread_id
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
            "input_keys": sorted(inputs) if inputs else [],
            **(
                {"task_file_path": str(task_file_path)}
                if task_file_path is not None
                else {}
            ),
            **(
                {"task_entry_index": task_entry_index}
                if task_entry_index is not None
                else {}
            ),
        },
        "_state_iterations": {},
    }
    if inputs:
        for key, value in inputs.items():
            if key != "_meta":
                initial_state[key] = value

    parallel_extra = sum(
        len(state.branches) + 1
        for state in flow.states.values()
        if isinstance(state, ParallelState)
    )
    wait_extra = sum(
        1 for state in flow.states.values() if isinstance(state, WaitState)
    )
    recursion_limit = (
        flow.max_loop * (len(flow.states) + parallel_extra + wait_extra) + 1
    )
    config: dict[str, Any] = {
        "recursion_limit": recursion_limit,
        "configurable": {"thread_id": thread_id},
    }
    terminal_context = TerminalContext(
        thread_id=thread_id,
        flow=flow,
        recorder=recorder,
        result_paths=compiled.result_paths,
        base_dir=base_dir,
        global_hooks=fdsx_config.hooks,
        workflow_hook_executor=execute_workflow_hooks,
        status_detector=_detect_abort_status,
    )

    is_fresh = checkpoint_manager is None or not checkpoint_manager.verify_checkpoint(
        thread_id
    )
    if is_fresh:
        try:
            execute_workflow_hooks(
                collect_workflow_hooks(
                    "on_workflow_start",
                    global_hooks=fdsx_config.hooks,
                    project_hooks=None,
                    flow_hooks=flow.hooks,
                ),
                status="starting",
                event="on_workflow_start",
                thread_id=thread_id,
                flow_name=flow.name,
            )
        except HookAbortError as error:
            logger.error("workflow_start_hook_aborted", error=str(error))
            finalize_failed_execution(terminal_context, initial_state, error)
            raise
    else:
        logger.debug("on_workflow_start_skipped")

    try:
        return execute_lifecycle(
            GraphExecutionPlan(
                graph=compiled.graph,
                signal_handler=handler,
                prepare_stream_input=lambda: initial_state,
                stream_config=config,
                continuation_config=config,
                initial_state=initial_state.copy(),
                checkpointed=needs_checkpointer,
            ),
            terminal_context,
            error_prefix="Flow execution failed",
        )
    except (FlowExecutionError, SystemExit):
        if checkpoint_manager is not None:
            print(
                "Checkpoint saved. Resume with: "
                f"fdsx resume --thread-id {_sanitize_output(thread_id)}",
                file=sys.stderr,
            )
        raise


def run_flow(
    flow_path: Path,
    inputs: dict[str, str] | None = None,
    thread_id: str | None = None,
    base_dir: Path | None = None,
    quiet: bool = False,
    task_file_path: Path | None = None,
    task_entry_index: int | None = None,
) -> FlowResult:
    """Load and execute a fresh workflow attempt."""
    if thread_id is None:
        thread_id = generate_thread_id()
    print(f"Thread ID: {_sanitize_output(thread_id)}", file=sys.stderr)

    fdsx_config = load_config(
        project_dir=base_dir.parent if base_dir is not None else None
    )
    config_profiles = (
        {name: profile.model_dump() for name, profile in fdsx_config.profiles.items()}
        if fdsx_config.profiles
        else None
    )
    flow, errors = load_flow(
        flow_path,
        input_keys=set(inputs.keys()) if inputs else None,
        config_profiles=config_profiles,
    )
    if flow is None:
        raise FlowValidationError(f"Flow validation failed: {', '.join(errors)}")

    context_tokens = bind_contextvars(thread_id=thread_id, flow_name=flow.name)
    checkpoint_manager: CheckpointManager | None = None

    def execute() -> FlowResult:
        return _execute_fresh_flow(
            flow=flow,
            flow_path=flow_path,
            inputs=inputs,
            thread_id=thread_id,
            base_dir=base_dir,
            quiet=quiet,
            task_file_path=task_file_path,
            task_entry_index=task_entry_index,
            fdsx_config=fdsx_config,
            checkpoint_manager=checkpoint_manager,
        )

    try:
        checkpoint_manager = (
            CheckpointManager(base_dir=base_dir) if base_dir is not None else None
        )
        if checkpoint_manager is not None:
            with checkpoint_lock(checkpoint_manager, thread_id):
                return execute()
        return execute()
    except (FlowExecutionError, HookAbortError, RunLockedError):
        raise
    except _SETUP_ERRORS as error:
        logger.error(
            "flow_setup_failed",
            thread_id=thread_id,
            flow_name=flow.name,
            error=str(error),
        )
        raise FlowExecutionError(f"Flow execution failed: {error}") from error
    finally:
        reset_contextvars(**context_tokens)

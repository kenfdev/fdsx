"""resume_flow implementation for the engine package."""

import json
import sys
from collections.abc import Mapping
from contextvars import Token
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Any, Literal, cast

import structlog
from langchain_core.runnables.config import RunnableConfig
from langgraph.errors import InvalidUpdateError
from langgraph.types import Command
from structlog.contextvars import bind_contextvars, reset_contextvars

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core.compiler import MaxIterationsReachedError, compile_flow
from fdsx.core.config import load_config
from fdsx.core.hooks import execute_workflow_hooks
from fdsx.core.loader import load_flow
from fdsx.display.terminal import (
    _sanitize_output,
    display_wait_prompt,
)
from fdsx.logging import RunRecorder
from fdsx.logging.recorder import LOGS_DIR_NAME, RUN_FILENAME, RUNS_DIR_NAME
from fdsx.models.task import load_task_file, save_task_file

from .errors import CheckpointNotFoundError, FlowExecutionError, RunLockedError
from .lifecycle import (
    GraphExecutionPlan,
    TerminalContext,
    emit_completion_event,
    execute_lifecycle,
    finalize_failed_execution,
)
from .recovery import (
    RecoveryStateRequiredError,
    RecoveryValidationError,
    build_recovery_update,
    recovery_state_required_message,
    reset_recovery_progress,
    validate_recovery_request,
)
from .results import FlowResult, _detect_abort_status
from .signals import SignalHandler

logger = structlog.get_logger(__name__)


def _update_task_entry(
    state: dict[str, Any],
    *,
    thread_id: str,
    status: str,
    failed_state: str | None,
) -> None:
    """Best-effort update of the tasks-directory entry stored in flow metadata."""
    meta = state.get("_meta", {})
    task_file_path_value = meta.get("task_file_path")
    task_entry_index = meta.get("task_entry_index")
    if task_file_path_value is None or task_entry_index is None:
        return

    try:
        task_file_path = Path(task_file_path_value)
        task_file = load_task_file(task_file_path)
        entry = task_file.entries[task_entry_index]
        new_status = "completed" if status == "completed" else "failed"
        entry.status = cast(
            Literal["pending", "running", "completed", "failed"], new_status
        )
        entry.thread_id = thread_id
        entry.error = (
            (
                f"workflow aborted at state '{failed_state}'"
                if status == "aborted"
                else status
            )
            if status != "completed"
            else None
        )
        save_task_file(task_file_path, task_file)
    except (FileNotFoundError, IndexError, ValueError):
        pass


def resume_flow(
    thread_id: str,
    base_dir: Path | None = None,
    flow_path: Path | None = None,
    from_state: str | None = None,
) -> FlowResult:
    """Resume a flow from a checkpoint.

    Args:
        thread_id: The thread ID to resume
        base_dir: Base directory for checkpoints (.fdsx/). Defaults to '.fdsx/'.
        flow_path: Optional path to the flow YAML file. Required if not stored in checkpoint.
        from_state: Optional executed state name for an explicit recovery jump.

    Returns:
        Final state variables as result dict.

    Raises:
        RuntimeError: If checkpoint is corrupt or execution fails
    """
    if base_dir is None:
        base_dir = CheckpointManager.DEFAULT_BASE_DIR

    checkpoint_manager = CheckpointManager(base_dir=base_dir)

    if not checkpoint_manager.verify_checkpoint(thread_id):
        raise CheckpointNotFoundError(f"No checkpoint found for thread ID {thread_id}")

    if not checkpoint_manager.acquire_lock(thread_id):
        locked, pid = checkpoint_manager.is_locked(thread_id)
        if locked:
            raise RunLockedError(f"Thread {thread_id} is locked by PID {pid}")

    print(f"Resuming from thread: {_sanitize_output(thread_id)}", file=sys.stderr)

    recorder: RunRecorder | None = None
    terminal_context: TerminalContext | None = None
    last_state: dict[str, Any] = {}
    context_tokens: Mapping[str, Token[Any]] | None = None

    try:
        checkpointer = checkpoint_manager.get_checkpointer()
        checkpoint_config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        checkpoint_tuple = checkpointer.get_tuple(checkpoint_config)
        input_keys: set[str] = set()
        if checkpoint_tuple is not None:
            channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
            checkpoint_meta = channel_values.get("_meta", {})
            if isinstance(checkpoint_meta, dict):
                stored_input_keys = checkpoint_meta.get("input_keys", [])
                if isinstance(stored_input_keys, list):
                    input_keys = {
                        key for key in stored_input_keys if isinstance(key, str)
                    }

        existing_log: dict[str, Any] = {}
        if flow_path is None or not flow_path.exists():
            # Read flow_path from run.json sidecar (written by RunRecorder on first run)
            _effective_base = (
                base_dir if base_dir is not None else checkpoint_manager.base_dir
            )
            run_log_path = _effective_base / RUNS_DIR_NAME / thread_id / RUN_FILENAME
            if run_log_path.is_file():
                try:
                    with run_log_path.open() as f:
                        existing_log = json.load(f)
                    _flow_path_str = existing_log.get("flow_path")
                    if _flow_path_str:
                        flow_path = Path(_flow_path_str)
                except (json.JSONDecodeError, OSError, KeyError):
                    pass

            if flow_path is None or not (flow_path and flow_path.exists()):
                raise RuntimeError(
                    f"Flow path not found for thread ID {thread_id}. "
                    "Please provide the flow YAML path using the flow_path parameter."
                )

        config = load_config(
            project_dir=base_dir.parent if base_dir is not None else None
        )
        config_profiles = None
        if config.profiles:
            config_profiles = {
                name: prof.model_dump() for name, prof in config.profiles.items()
            }

        flow, errors = load_flow(
            flow_path,
            input_keys=input_keys or None,
            config_profiles=config_profiles,
        )
        if flow is None:
            raise RuntimeError(f"Failed to load flow for resume: {', '.join(errors)}")
        context_tokens = bind_contextvars(
            thread_id=thread_id,
            flow_name=flow.name,
        )

        from fdsx.models.flow import ParallelState, WaitState

        runs_dir = base_dir / RUNS_DIR_NAME
        existing_log_path = runs_dir / thread_id / RUN_FILENAME

        if existing_log_path.exists():
            with existing_log_path.open() as f:
                existing_log = json.load(f)
            flow_name = existing_log.get("flow_name", flow.name)
            flow_version = existing_log.get("flow_version")
        else:
            flow_name = flow.name
            flow_version = flow.version

        recorder = RunRecorder(
            thread_id=thread_id,
            flow_name=flow_name,
            flow_version=flow_version,
            flow_path=str(flow_path),
        )

        resume_run_dir = base_dir / RUNS_DIR_NAME / thread_id
        resume_log_dir = resume_run_dir / LOGS_DIR_NAME

        handler = SignalHandler(checkpoint_manager, thread_id)

        compiled = compile_flow(
            flow,
            input_keys=input_keys or None,
            checkpointer=checkpointer,
            recorder=recorder,
            config=config,
            log_dir=resume_log_dir,
            on_process_start=handler.register_process,
        )
        terminal_context = TerminalContext(
            thread_id=thread_id,
            flow=flow,
            recorder=recorder,
            result_paths=compiled.result_paths,
            base_dir=base_dir,
            global_hooks=config.hooks,
            workflow_hook_executor=execute_workflow_hooks,
            status_detector=_detect_abort_status,
            on_terminal=lambda state, status, failed_state: _update_task_entry(
                state,
                thread_id=thread_id,
                status=status,
                failed_state=failed_state,
            ),
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
        latest_resume_config = resume_config

        state_info = compiled.graph.get_state(resume_config)
        if state_info.values:
            last_state = dict(state_info.values)
        existing_meta = state_info.values.get("_meta", {}) if state_info.values else {}
        if "run_dir" not in existing_meta:
            updated_meta = {**existing_meta, "run_dir": str(resume_run_dir)}
            compiled.graph.update_state(resume_config, {"_meta": updated_meta})

        state_info = compiled.graph.get_state(resume_config)

        _terminal_failure = (
            (state_info.values or {}).get("_meta", {}).get("terminal_failure")
        )
        _terminal_status = (
            (state_info.values or {}).get("_meta", {}).get("terminal_status")
        )
        _max_iterations_failure = any(
            isinstance(getattr(task, "error", None), MaxIterationsReachedError)
            for task in state_info.tasks
        )
        recovery_command: Command[Any] | None = None
        if from_state is not None:
            validate_recovery_request(
                flow,
                existing_log,
                from_state,
                dict(state_info.values or {}),
                config,
            )
            reset_recovery_progress(flow, dict(state_info.values or {}))
            resume_config = compiled.prepare_recovery(
                resume_config,
                build_recovery_update(
                    flow,
                    dict(state_info.values or {}),
                ),
            )
            state_info = compiled.graph.get_state(resume_config)
            recovery_command = Command(goto=from_state)
            recorder.record_recovery(from_state)
            print(
                f"Recovering from state: {_sanitize_output(from_state)}",
                file=sys.stderr,
            )
        elif (
            _terminal_status in {"max_loop_reached", "max_iterations_reached"}
            or _terminal_failure is not None
            or existing_log.get("status") == "aborted"
            or _max_iterations_failure
        ):
            raise RecoveryStateRequiredError(
                recovery_state_required_message(flow, existing_log)
            )

        stream_config = resume_config
        resume_config = latest_resume_config

        def prepare_resume_input() -> Command[Any] | None:
            if recovery_command is not None:
                return recovery_command
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
                    return Command(resume=user_selection)
            return None

        return execute_lifecycle(
            GraphExecutionPlan(
                graph=compiled.graph,
                signal_handler=handler,
                prepare_stream_input=prepare_resume_input,
                stream_config=stream_config,
                continuation_config=resume_config,
                initial_state=last_state,
                checkpointed=True,
            ),
            terminal_context,
            error_prefix="Flow resume failed",
        )
    except RecoveryValidationError as error:
        logger.warning(
            "recovery_validation_failed",
            thread_id=thread_id,
            from_state=from_state,
            error=str(error),
        )
        if terminal_context is not None:
            emit_completion_event(terminal_context, status="recovery_failed")
        raise
    except FlowExecutionError:
        raise
    except (
        InvalidUpdateError,
        SQLiteError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        KeyError,
        IndexError,
    ) as error:
        logger.error(
            "flow_resume_setup_failed",
            thread_id=thread_id,
            error=str(error),
        )
        if recorder is not None and terminal_context is not None:
            finalize_failed_execution(terminal_context, last_state, error)
        raise FlowExecutionError(f"Flow resume failed: {error}") from error
    finally:
        if context_tokens is not None:
            reset_contextvars(**context_tokens)
        active_error = sys.exc_info()[0] is not None
        try:
            checkpoint_manager.release_lock(thread_id)
        except (
            InvalidUpdateError,
            SQLiteError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as cleanup_error:
            logger.error(
                "checkpoint_lock_release_failed",
                thread_id=thread_id,
                error=str(cleanup_error),
            )
            if not active_error:
                raise FlowExecutionError(
                    f"Failed to release checkpoint lock for thread {thread_id}"
                ) from cleanup_error

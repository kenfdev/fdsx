"""Shared execution and terminal lifecycle for fresh and resumed flows."""

import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Error as SQLiteError
from typing import Any

import structlog
from langgraph.errors import InvalidUpdateError

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core.compiler import MaxIterationsReachedError
from fdsx.core.hooks import (
    HookAbortError,
    collect_workflow_hooks,
)
from fdsx.display.terminal import display_completion_summary
from fdsx.logging import RunRecorder
from fdsx.models.flow import Flow, HookConfig

from .errors import FlowExecutionError, RunLockedError
from .results import (
    AbortInfo,
    FlowResult,
    _calc_elapsed,
    _extract_results,
    _sanitize_state_for_log,
)
from .signals import SignalHandler
from .validate import FailStateTermination

# Keep lifecycle telemetry on stderr without reconfiguring structlog for library users.
logger = structlog.wrap_logger(structlog.PrintLogger(file=sys.stderr))

_LIFECYCLE_ERRORS = (
    InvalidUpdateError,
    SQLiteError,
    OSError,
    EOFError,
    RuntimeError,
    TypeError,
    ValueError,
    KeyError,
    IndexError,
)


@dataclass(frozen=True)
class TerminalContext:
    """Inputs needed to finalize one terminal execution attempt."""

    thread_id: str
    flow: Flow
    recorder: RunRecorder
    result_paths: list[str]
    base_dir: Path | None
    global_hooks: HookConfig | None
    workflow_hook_executor: Callable[..., None]
    status_detector: Callable[[RunRecorder], tuple[str, AbortInfo | None]]
    on_terminal: Callable[[dict[str, Any], str, str | None], None] | None = None


@dataclass(frozen=True)
class GraphExecutionPlan:
    """Prepared graph invocation shared by fresh and resumed executions."""

    graph: Any
    signal_handler: SignalHandler
    prepare_stream_input: Callable[[], Any]
    stream_config: dict[str, Any]
    continuation_config: dict[str, Any]
    initial_state: dict[str, Any]
    checkpointed: bool


def execute_graph(plan: GraphExecutionPlan) -> dict[str, Any]:
    """Stream a prepared graph invocation through interrupts to its latest state."""
    from .interrupts import handle_interrupts

    last_state = plan.initial_state
    with plan.signal_handler:
        stream_input = plan.prepare_stream_input()
        for chunk in plan.graph.stream(
            stream_input,
            config=plan.stream_config,
            stream_mode="values",
            version="v2",
        ):
            last_state = chunk["data"]

        if plan.checkpointed:
            last_state = handle_interrupts(
                plan.graph,
                plan.continuation_config,
                last_state,
            )

    if plan.checkpointed:
        state_info = plan.graph.get_state(plan.continuation_config)
        if state_info.values:
            last_state = dict(state_info.values)

    return last_state


@contextmanager
def checkpoint_lock(manager: CheckpointManager, thread_id: str) -> Any:
    """Own a checkpoint lock and preserve any active error during cleanup."""
    if not manager.acquire_lock(thread_id):
        locked, pid = manager.is_locked(thread_id)
        if locked:
            raise RunLockedError(f"Thread {thread_id} is locked by PID {pid}")
        raise RunLockedError(f"Could not acquire lock for thread {thread_id}")

    try:
        yield
    finally:
        active_error = sys.exc_info()[0] is not None
        try:
            manager.release_lock(thread_id)
        except _LIFECYCLE_ERRORS as cleanup_error:
            logger.error(
                "checkpoint_lock_release_failed",
                thread_id=thread_id,
                error=str(cleanup_error),
            )
            if not active_error:
                raise FlowExecutionError(
                    f"Failed to release checkpoint lock for thread {thread_id}"
                ) from cleanup_error


def _read_latest_state(
    plan: GraphExecutionPlan,
    last_state: dict[str, Any],
) -> dict[str, Any]:
    if not plan.checkpointed:
        return last_state
    try:
        state_info = plan.graph.get_state(plan.continuation_config)
        if state_info.values:
            return dict(state_info.values)
    except _LIFECYCLE_ERRORS as state_error:
        logger.error(
            "authoritative_state_read_failed",
            error=str(state_error),
        )
    return last_state


def _persist_terminal_marker(
    plan: GraphExecutionPlan,
    marker: dict[str, Any],
    *,
    event: str,
) -> None:
    if not plan.checkpointed:
        return
    try:
        state_info = plan.graph.get_state(plan.continuation_config)
        existing_meta = state_info.values.get("_meta", {}) if state_info.values else {}
        plan.graph.update_state(
            plan.continuation_config,
            {"_meta": {**existing_meta, **marker}},
        )
    except _LIFECYCLE_ERRORS as marker_error:
        logger.error(event, error=str(marker_error))


def execute_lifecycle(
    plan: GraphExecutionPlan,
    context: TerminalContext,
    *,
    error_prefix: str,
) -> FlowResult:
    """Execute one prepared graph and own its common terminal lifecycle."""
    last_state = plan.initial_state
    try:
        last_state = execute_graph(plan)
        return finalize_terminal_execution(context, last_state)
    except FailStateTermination as termination:
        _persist_terminal_marker(
            plan,
            {
                "terminal_failure": {
                    "state": termination.state_name,
                    "error": termination.error,
                    "cause": termination.cause,
                }
            },
            event="terminal_failure_marker_persist_failed",
        )
        last_state = _read_latest_state(plan, last_state)
        try:
            return finalize_terminal_execution(
                context,
                last_state,
                forced_status="aborted",
                forced_failed_state=termination.state_name,
            )
        except _LIFECYCLE_ERRORS as secondary_error:
            logger.error(
                "flow_terminal_cleanup_failed",
                thread_id=context.thread_id,
                status="aborted",
                error=str(secondary_error),
            )
            try:
                results = _extract_results(last_state, context.result_paths)
            except _LIFECYCLE_ERRORS as result_error:
                logger.error(
                    "flow_terminal_cleanup_failed",
                    thread_id=context.thread_id,
                    status="aborted",
                    step="result_extraction",
                    error=str(result_error),
                )
                results = {}
            emit_completion_event(context, status="aborted")
            return FlowResult(
                results=results,
                status="aborted",
                abort_state=termination.state_name,
            )
    except SystemExit:
        last_state = _read_latest_state(plan, last_state)
        emit_completion_event(context, status="interrupted")
        raise
    except _LIFECYCLE_ERRORS as error:
        if isinstance(error, MaxIterationsReachedError):
            _persist_terminal_marker(
                plan,
                {"terminal_status": "max_iterations_reached"},
                event="max_iterations_marker_persist_failed",
            )
        last_state = _read_latest_state(plan, last_state)
        logger.error(
            "flow_execution_failed",
            thread_id=context.thread_id,
            flow_name=context.recorder.flow_name,
            error=str(error),
        )
        finalize_failed_execution(context, last_state, error)
        if isinstance(error, (HookAbortError, FlowExecutionError)):
            raise
        raise FlowExecutionError(f"{error_prefix}: {error}") from error


def emit_completion_event(
    context: TerminalContext,
    *,
    status: str,
) -> None:
    """Emit the common completion event without mutating persisted run state."""
    try:
        elapsed = _calc_elapsed(context.recorder)
    except _LIFECYCLE_ERRORS as secondary_error:
        logger.error(
            "flow_failure_cleanup_failed",
            thread_id=context.thread_id,
            step="elapsed_calculation",
            error=str(secondary_error),
        )
        elapsed = 0.0
    logger.info(
        "flow_execution_completed",
        thread_id=context.thread_id,
        flow_name=context.recorder.flow_name,
        status=status,
        duration_seconds=elapsed,
        states_run=len(context.recorder.states),
    )


def finalize_terminal_execution(
    context: TerminalContext,
    last_state: dict[str, Any],
    *,
    forced_status: str | None = None,
    forced_failed_state: str | None = None,
) -> FlowResult:
    """Finalize and report a completed, aborted, or loop-limited execution."""
    results = _extract_results(last_state, context.result_paths)
    detected_status, abort_info = context.status_detector(context.recorder)
    status = forced_status or detected_status
    if last_state.get("_meta", {}).get("terminal_status") == "max_loop_reached":
        status = "max_loop_reached"
    failed_state = (
        forced_failed_state
        if forced_failed_state is not None
        else abort_info.state_name
        if abort_info is not None
        else None
    )

    context.workflow_hook_executor(
        collect_workflow_hooks(
            "on_workflow_end",
            global_hooks=context.global_hooks,
            project_hooks=None,
            flow_hooks=context.flow.hooks,
        ),
        status=status,
        event="on_workflow_end",
        thread_id=context.thread_id,
        flow_name=context.recorder.flow_name,
    )
    context.recorder.finalize(_sanitize_state_for_log(last_state), status)
    context.recorder.save(base_dir=context.base_dir)

    elapsed = _calc_elapsed(context.recorder)
    if status == "max_loop_reached":
        display_completion_summary(
            context.recorder.flow_name,
            elapsed,
            "max_loop",
            "max_loop_reached",
        )
    elif failed_state is not None:
        display_completion_summary(
            context.recorder.flow_name,
            elapsed,
            failed_state,
            "workflow aborted",
            error_name=abort_info.error_name if abort_info is not None else None,
            error_cause=abort_info.error_cause if abort_info is not None else None,
        )
    else:
        display_completion_summary(context.recorder.flow_name, elapsed)

    if context.on_terminal is not None:
        context.on_terminal(last_state, status, failed_state)

    logger.info(
        "flow_execution_completed",
        thread_id=context.thread_id,
        flow_name=context.recorder.flow_name,
        status=status,
        duration_seconds=elapsed,
        states_run=len(context.recorder.states),
    )
    return FlowResult(
        results=results,
        status=status,
        abort_state=failed_state,
    )


def finalize_failed_execution(
    context: TerminalContext,
    last_state: dict[str, Any],
    error: Exception,
) -> None:
    """Record a failed attempt without allowing cleanup to mask its error."""

    def attempt(step: str, action: Callable[[], Any]) -> None:
        try:
            action()
        except _LIFECYCLE_ERRORS as secondary_error:
            logger.error(
                "flow_failure_cleanup_failed",
                thread_id=context.thread_id,
                step=step,
                error=str(secondary_error),
            )

    attempt(
        "recorder_finalize",
        lambda: context.recorder.finalize(
            _sanitize_state_for_log(last_state),
            "error",
        ),
    )
    attempt(
        "recorder_save",
        lambda: context.recorder.save(base_dir=context.base_dir),
    )

    try:
        detected_status, _ = context.status_detector(context.recorder)
    except _LIFECYCLE_ERRORS as secondary_error:
        logger.error(
            "flow_failure_cleanup_failed",
            thread_id=context.thread_id,
            step="status_detection",
            error=str(secondary_error),
        )
        detected_status = "failed"
    end_status = (
        "aborted"
        if detected_status == "aborted" or isinstance(error, HookAbortError)
        else "failed"
    )
    attempt(
        "workflow_end_hooks",
        lambda: context.workflow_hook_executor(
            collect_workflow_hooks(
                "on_workflow_end",
                global_hooks=context.global_hooks,
                project_hooks=None,
                flow_hooks=context.flow.hooks,
            ),
            status=end_status,
            event="on_workflow_end",
            thread_id=context.thread_id,
            flow_name=context.recorder.flow_name,
        ),
    )

    from .results import _find_failed_state

    failed = _find_failed_state(context.recorder)
    failed_state = failed[0] if failed else "unknown"
    error_message = failed[1] if (failed and failed[1]) else str(error)
    try:
        elapsed = _calc_elapsed(context.recorder)
    except _LIFECYCLE_ERRORS as secondary_error:
        logger.error(
            "flow_failure_cleanup_failed",
            thread_id=context.thread_id,
            step="elapsed_calculation",
            error=str(secondary_error),
        )
        elapsed = 0.0
    attempt(
        "completion_display",
        lambda: display_completion_summary(
            context.recorder.flow_name,
            elapsed,
            failed_state,
            error_message,
        ),
    )

    if context.on_terminal is not None:
        on_terminal = context.on_terminal
        attempt(
            "terminal_callback",
            lambda: on_terminal(last_state, "error", failed_state),
        )

    logger.info(
        "flow_execution_completed",
        thread_id=context.thread_id,
        flow_name=context.recorder.flow_name,
        status=end_status,
        duration_seconds=elapsed,
        states_run=len(context.recorder.states),
    )

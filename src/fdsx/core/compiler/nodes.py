"""Node factory functions for the compiler package."""

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt

from fdsx.core.variables import (
    resolve_template,
    resolve_template_shell_safe,
    set_jsonpath,
    write_result_to_file,
)
from fdsx.display import terminal
from fdsx.display.terminal import _sanitize_output
from fdsx.models.flow import (
    ChoiceState,
    Flow,
    PassState,
    TaskState,
    WaitState,
)
from fdsx.providers.base import get_provider

from .helpers import (
    _check_max_iterations,
    _merge_provider_options,
    _set_next_state_meta,
)

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig


def _create_task_node(
    state_name: str,
    state: TaskState,
    flow: Flow,
    recorder: Any = None,
    config: "FdsxConfig | None" = None,
    log_dir: Path | None = None,
    quiet: bool = False,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Task state."""
    merged_options = _merge_provider_options(
        config, flow, state.provider, state.provider_options, state_name=state_name
    )

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        from fdsx.core.compiler.execution import ExecutionConfig, execute_with_retry
        from fdsx.logging.stream_logger import StreamLogger

        start_time = time.time()
        terminal.display_state_start(
            state_name=state_name,
            state_type="task",
            provider=state.provider,
            model=state.model,
        )

        if recorder is not None:
            recorder.record_state_start(state_name, "task")

        resolved_prompt = resolve_template(state.prompt_template or "", state_dict)
        resolved_command = resolve_template_shell_safe(state.command or "", state_dict)

        effective_options = dict(merged_options) if merged_options else None
        if effective_options:
            for key in ("system_prompt", "append_system_prompt"):
                if effective_options.get(key):
                    effective_options[key] = resolve_template(
                        effective_options[key], state_dict
                    )
        provider = get_provider(state.provider, effective_options)

        max_retries = state.retry if state.retry is not None else 3

        iters = dict(state_dict.get("_state_iterations", {}))
        iteration = iters.get(state_name, 0) + 1
        iters[state_name] = iteration
        _check_max_iterations(state_name, state, iteration)

        stream_logger = StreamLogger(
            state_name, log_dir, quiet=quiet, iteration=iteration
        )
        exec_config = ExecutionConfig(
            provider=provider,
            provider_name=state.provider,
            prompt=resolved_prompt,
            command=resolved_command,
            model=state.model,
            timeout_seconds=state.timeout_seconds,
            max_retries=max_retries,
            extract=state.extract,
            stream_logger=stream_logger,
            on_process_start=on_process_start,
            summary_callback=stream_logger.on_summary,
        )
        exec_result = execute_with_retry(exec_config)
        result = exec_result.result
        extracted = exec_result.extracted
        last_error = exec_result.last_error

        if result.exit_code != 0:
            terminal.display_state_error(state_name, last_error)
            if recorder is not None:
                recorder.record_state_error(state_name, last_error)
            raise RuntimeError(
                f"Provider {state.provider} failed after {max_retries + 1} attempts with exit code {result.exit_code}: {_sanitize_output(last_error)}"
            )

        partial: dict[str, Any] = {}

        if state.extract:
            if extracted is None:
                terminal.display_state_error(state_name, last_error)
                if recorder is not None:
                    recorder.record_state_error(state_name, last_error)
                raise RuntimeError(
                    f"Extraction failed after {max_retries + 1} attempts: all strategies returned None"
                )
            partial = set_jsonpath(state.extract.result_path, partial, extracted)
            partial = set_jsonpath(state.result_path, partial, result.stdout.strip())
            variables_set = [state.extract.result_path, state.result_path]
        else:
            partial = set_jsonpath(state.result_path, partial, result.stdout.strip())
            variables_set = [state.result_path]

        if state.result_file:
            run_dir = state_dict.get("_meta", {}).get("run_dir", "")
            if run_dir:
                varname = state.result_file[2:]  # strip "$."
                file_path = write_result_to_file(
                    varname, result.stdout.strip(), Path(run_dir)
                )
                partial = set_jsonpath(state.result_file, partial, file_path)
                variables_set = [*variables_set, state.result_file]

        duration = time.time() - start_time
        terminal.display_state_complete(state_name, duration)

        if recorder is not None:
            recorder.record_state_complete(
                state_name,
                "success",
                result.stdout,
                variables_set,
            )

        partial["_state_iterations"] = iters
        meta_holder = _set_next_state_meta({"_meta": state_dict.get("_meta") or {}}, state)
        partial["_meta"] = meta_holder["_meta"]
        return partial

    return node


def _create_choice_node(
    state_name: str, state: ChoiceState, flow: Flow, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Choice state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        iters = dict(state_dict.get("_state_iterations", {}))
        iteration = iters.get(state_name, 0) + 1
        iters[state_name] = iteration
        _check_max_iterations(state_name, state, iteration)
        if recorder is not None:
            recorder.record_state_start(state_name, "choice")
            recorder.record_state_complete(state_name, "success", "", [])
        return {"_state_iterations": iters}

    return node


def _create_pass_node(
    state_name: str, state: PassState, flow: Flow, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Pass state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        iters = dict(state_dict.get("_state_iterations", {}))
        iteration = iters.get(state_name, 0) + 1
        iters[state_name] = iteration
        _check_max_iterations(state_name, state, iteration)
        state_dict["_state_iterations"] = iters

        if recorder is not None:
            recorder.record_state_start(state_name, "pass")

        partial: dict[str, Any] = {}
        variables_set = []
        if state.parameters:
            for target, source in state.parameters.items():
                if isinstance(source, str):
                    value = resolve_template(source, state_dict)
                else:
                    value = source
                state_dict = set_jsonpath(target, state_dict, value)  # working copy for chaining
                partial = set_jsonpath(target, partial, value)
                variables_set.append(target)

        if state.aggregate:
            from fdsx.core.variables import resolve_jsonpath

            from .aggregation import _aggregate

            source_data = resolve_jsonpath(state.aggregate.source, state_dict)
            if isinstance(source_data, list):
                result = _aggregate(source_data, state.aggregate)
            else:
                result = state.aggregate.no_match
            state_dict = set_jsonpath(state.aggregate.result_path, state_dict, result)
            partial = set_jsonpath(state.aggregate.result_path, partial, result)
            variables_set.append(state.aggregate.result_path)

        if recorder is not None:
            recorder.record_state_complete(state_name, "success", "", variables_set)

        partial["_state_iterations"] = iters
        meta_holder = _set_next_state_meta({"_meta": state_dict.get("_meta") or {}}, state)
        partial["_meta"] = meta_holder["_meta"]
        return partial

    return node


def _create_wait_notify_node(
    state_name: str, state: WaitState, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the pre-interrupt notify node for a Wait state.

    Sends the webhook notification (if configured) and returns the state so the
    result is checkpointed before the interrupt.  This guarantees the notification
    fires exactly once: on the first entry the checkpoint advances past this node,
    so on resume LangGraph replays only the interrupt node — not this one.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        iters = dict(state_dict.get("_state_iterations", {}))
        iteration = iters.get(state_name, 0) + 1
        iters[state_name] = iteration
        _check_max_iterations(state_name, state, iteration)

        if recorder is not None:
            recorder.record_state_start(state_name, "wait")

        if state.notify is not None:
            from fdsx.notify.webhook import send_notification

            send_notification(state.notify, state_dict)
        return {"_state_iterations": iters}

    return node


def _create_wait_interrupt_node(
    state_name: str, state: WaitState, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the interrupt node for a Wait state.

    Uses LangGraph's interrupt() to pause execution and wait for user input.
    The engine handles the actual prompting and resume with Command(resume=value).
    Only this node is re-executed on resume; the notify node above is not.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        resolved_message = resolve_template(state.message, state_dict)

        user_selection = interrupt(
            {
                "message": resolved_message,
                "choices": state.choices,
                "state_name": state_name,
            }
        )

        partial: dict[str, Any] = set_jsonpath(state.result_path, {}, user_selection)

        if recorder is not None:
            recorder.record_state_complete(
                state_name,
                "success",
                user_selection,
                [state.result_path],
                state_type="wait",
            )

        meta_holder = _set_next_state_meta({"_meta": state_dict.get("_meta") or {}}, state)
        partial["_meta"] = meta_holder["_meta"]
        return partial

    return node

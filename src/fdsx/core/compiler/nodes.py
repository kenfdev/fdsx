"""Node factory functions for the compiler package."""

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from langgraph.types import interrupt

from fdsx.core.extraction_fallback import FallbackEvent, resolve_fallback
from fdsx.core.hooks import (
    INPUT_FILENAME,
    OUTPUT_FILENAME,
    execute_hooks,
    write_hook_data,
)
from fdsx.core.variables import (
    _strip_reserved_keys,
    inject_builtin_vars,
    resolve_jsonpath,
    resolve_template,
    resolve_template_shell_safe,
    set_jsonpath,
    write_result_to_file,
)
from fdsx.display import terminal
from fdsx.display.terminal import _sanitize_output
from fdsx.models.flow import (
    ChoiceState,
    FailState,
    Flow,
    HookEntry,
    PassState,
    StructuredOutput,
    TaskState,
    WaitState,
)
from fdsx.providers.base import get_provider

from .helpers import (
    _check_max_iterations,
    _merge_provider_options,
    build_escalation_target,
)

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig


def _make_escalation_callback(
    state_name: str, esc_target: Any, recorder: Any
) -> Callable[[], None]:
    def _cb() -> None:
        terminal.display_state_escalation(
            state_name, esc_target.provider_name, esc_target.model
        )
        if recorder is not None:
            recorder.record_state_escalation(
                state_name, esc_target.provider_name, esc_target.model
            )

    return _cb


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
    esc_target = build_escalation_target(config, flow, state.provider)
    node_flow_profiles: dict[str, Any] | None = getattr(flow, "profiles", None)
    node_resolved_fallback = None
    if state.extract is not None and config is not None:
        _flow_ef = getattr(flow, "extraction_fallback", None)
        if (
            state.extract.fallback is not None
            or config.extraction_fallback is not None
            or (_flow_ef is not None and _flow_ef is not False)
        ):
            node_resolved_fallback = resolve_fallback(state.extract, flow, config)
    node_config_profiles = (
        {k: v.model_dump() for k, v in config.profiles.items()}
        if config is not None and config.profiles
        else None
    )
    structured_output = (
        state.structured_output
        if isinstance(state.structured_output, StructuredOutput)
        else None
    )

    def _on_fallback(event: FallbackEvent) -> None:
        if recorder is not None:
            recorder.record_fallback_invocation(
                state_name=state_name,
                source=event.source,
                outcome=event.outcome,
                pattern=event.pattern,
                value_preview=event.value_preview,
                error_kind=event.error_kind,
            )
        terminal.display_fallback(
            state_name=state_name,
            source=event.source,
            outcome=event.outcome,
            value_preview=event.value_preview,
            error_kind=event.error_kind,
            provider=event.provider,
            model=event.model,
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

        iters = dict(state_dict.get("_state_iterations", {}))
        iteration = iters.get(state_name, 0) + 1
        iters[state_name] = iteration
        _check_max_iterations(state_name, state, iteration)

        vars_ctx = inject_builtin_vars(state_dict, state_iteration=iteration)
        resolved_prompt = resolve_template(state.prompt_template or "", vars_ctx)
        resolved_command = resolve_template_shell_safe(state.command or "", vars_ctx)

        effective_options = dict(merged_options) if merged_options else None
        if effective_options:
            for key in (
                "system_prompt",
                "append_system_prompt",
                "developer_instructions",
            ):
                if effective_options.get(key):
                    effective_options[key] = resolve_template(
                        effective_options[key], vars_ctx
                    )
        provider = get_provider(state.provider, effective_options)

        max_retries = state.retry if state.retry is not None else 3

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
            structured_output=structured_output,
            stream_logger=stream_logger,
            on_process_start=on_process_start,
            summary_callback=stream_logger.on_summary,
            resolved_fallback=node_resolved_fallback,
            flow_profiles=node_flow_profiles,
            config_profiles=node_config_profiles,
            on_fallback=_on_fallback,
            escalation=esc_target,
            on_escalation_activated=(
                _make_escalation_callback(state_name, esc_target, recorder)
                if esc_target is not None
                else None
            ),
        )
        exec_result = execute_with_retry(exec_config)
        result = exec_result.result
        extracted = exec_result.extracted
        structured_value = exec_result.structured_value
        last_error = exec_result.last_error

        if result.exit_code != 0:
            terminal.display_state_error(state_name, last_error)
            if recorder is not None:
                recorder.record_state_error(state_name, last_error)
            orig = state.provider
            last = exec_result.last_provider_name or orig
            annotation = f" (escalated from {orig})" if last != orig else ""
            raise RuntimeError(
                f"Provider {last} failed after {max_retries + 1} attempts{annotation} "
                f"with exit code {result.exit_code}: {_sanitize_output(last_error)}"
            )

        partial: dict[str, Any] = {}

        if structured_output is not None:
            if structured_value is None:
                terminal.display_state_error(state_name, last_error)
                if recorder is not None:
                    recorder.record_state_error(state_name, last_error)
                from fdsx.core.structured_output import StructuredOutputValidationError

                raise StructuredOutputValidationError(last_error)
            if structured_output.merge is not None:
                from fdsx.core.structured_output import upsert_structured_items

                current = resolve_jsonpath(structured_output.result_path, state_dict)
                structured_value = upsert_structured_items(
                    current,
                    structured_value,
                    structured_output.merge.key,
                )
            partial = set_jsonpath(
                structured_output.result_path, partial, structured_value
            )
            variables_set = [structured_output.result_path]
        elif state.extract:
            if extracted is None:
                terminal.display_state_error(state_name, last_error)
                if recorder is not None:
                    recorder.record_state_error(state_name, last_error)
                raise RuntimeError(
                    f"Extraction failed after {max_retries + 1} attempts: all strategies returned None"
                )
            partial = set_jsonpath(state.extract.result_path, partial, extracted)
            variables_set = [state.extract.result_path]
            if state.result_path is not None:
                partial = set_jsonpath(
                    state.result_path, partial, result.stdout.strip()
                )
                variables_set.append(state.result_path)
        else:
            variables_set = []
            if state.result_path is not None:
                partial = set_jsonpath(
                    state.result_path, partial, result.stdout.strip()
                )
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
        return _strip_reserved_keys(partial)

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
                    value = resolve_template(source, inject_builtin_vars(state_dict))
                else:
                    value = source
                state_dict = set_jsonpath(
                    target, state_dict, value
                )  # working copy for chaining
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
        return _strip_reserved_keys(partial)

    return node


def _create_fail_node(
    state_name: str,
    state: FailState,
    flow: Flow,
    recorder: Any = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Fail state."""
    from fdsx.core.engine.validate import FailStateTermination

    log = structlog.get_logger(__name__)

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        iters = dict(state_dict.get("_state_iterations", {}))
        iters[state_name] = iters.get(state_name, 0) + 1

        terminal.display_state_start(state_name, "fail")
        if recorder is not None:
            recorder.record_state_start(state_name, "fail")

        vars_ctx = inject_builtin_vars(state_dict)
        resolved_error = resolve_template(state.error, vars_ctx)
        resolved_cause = resolve_template(state.cause, vars_ctx)

        log.error(
            "fail_state_entered",
            state=state_name,
            error=resolved_error,
            cause=resolved_cause,
        )
        if recorder is not None:
            recorder.record_state_error(
                state_name,
                f"{resolved_error}: {resolved_cause}",
                state_type="fail",
                error_name=resolved_error,
                error_cause=resolved_cause,
            )
        terminal.display_state_error(state_name, f"{resolved_error}: {resolved_cause}")
        raise FailStateTermination(
            state_name=state_name,
            error=resolved_error,
            cause=resolved_cause,
        )

    return node


def _create_wait_notify_node(
    state_name: str,
    state: WaitState,
    recorder: Any = None,
    on_wait_start_hooks: list[HookEntry] | None = None,
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

        if on_wait_start_hooks:
            resolved_message = resolve_template(
                state.message, inject_builtin_vars(state_dict)
            )
            data_path = write_hook_data(
                state_dict,
                state_name=state_name,
                filename=INPUT_FILENAME,
                thread_id=recorder.thread_id if recorder is not None else "",
            )
            execute_hooks(
                on_wait_start_hooks,
                state_name=state_name,
                status="starting",
                data_path=data_path,
                thread_id=recorder.thread_id if recorder is not None else "",
                flow_name=recorder.flow_name if recorder is not None else "",
                event="on_wait_start",
                extra_env={
                    "FDSX_WAIT_MESSAGE": resolved_message,
                    "FDSX_WAIT_CHOICES": json.dumps(state.choices),
                },
            )

        return {"_state_iterations": iters}

    return node


def _create_wait_interrupt_node(
    state_name: str,
    state: WaitState,
    recorder: Any = None,
    on_wait_end_hooks: list[HookEntry] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the interrupt node for a Wait state.

    Uses LangGraph's interrupt() to pause execution and wait for user input.
    The engine handles the actual prompting and resume with Command(resume=value).
    Only this node is re-executed on resume; the notify node above is not.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        resolved_message = resolve_template(
            state.message, inject_builtin_vars(state_dict)
        )

        user_selection = interrupt(
            {
                "message": resolved_message,
                "choices": state.choices,
                "state_name": state_name,
            }
        )

        partial: dict[str, Any] = set_jsonpath(state.result_path, {}, user_selection)

        if on_wait_end_hooks:
            data_path = write_hook_data(
                {**state_dict, **partial},
                state_name=state_name,
                filename=OUTPUT_FILENAME,
                thread_id=recorder.thread_id if recorder is not None else "",
            )
            execute_hooks(
                on_wait_end_hooks,
                state_name=state_name,
                status="completed",
                data_path=data_path,
                thread_id=recorder.thread_id if recorder is not None else "",
                flow_name=recorder.flow_name if recorder is not None else "",
                event="on_wait_end",
                extra_env={"FDSX_WAIT_SELECTION": str(user_selection)},
            )

        if recorder is not None:
            recorder.record_state_complete(
                state_name,
                "success",
                user_selection,
                [state.result_path],
                state_type="wait",
            )

        return _strip_reserved_keys(partial)

    return node

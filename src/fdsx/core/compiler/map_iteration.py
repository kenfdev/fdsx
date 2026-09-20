"""Map state iteration node factory for the compiler package."""

import subprocess  # nosec B404 - process callback type annotations only.
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from fdsx.checkpoint.map_progress import MapProgress
from fdsx.core.extraction_fallback import FallbackEvent, resolve_fallback
from fdsx.core.variables import (
    _strip_reserved_keys,
    inject_builtin_vars,
    resolve_jsonpath,
    resolve_template,
    resolve_template_shell_safe,
    set_jsonpath,
)
from fdsx.display.terminal import (
    _sanitize_output,
    display_fallback,
    display_map_complete,
    display_map_iteration,
    display_map_iteration_complete,
    display_map_iteration_escalation,
    display_map_iteration_failed,
    display_map_start,
)
from fdsx.logging.attempts import observe_attempts
from fdsx.models.flow import (
    Flow,
    LocalWorkflow,
    MapState,
)
from fdsx.providers.base import get_provider

from .helpers import (
    EscalationTarget,
    _check_max_iterations,
    _merge_provider_options,
    build_escalation_target,
)

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig


def _top_key(path: str) -> str:
    """Extract the top-level channel key from a JSONPath expression.

    e.g. "$.steps.processed" -> "steps", "$.output" -> "output", "$.items[0].x" -> "items"
    """
    stripped = path[2:] if path.startswith("$.") else path
    return stripped.split(".")[0].split("[")[0]


def _create_map_node(
    state_name: str,
    state: MapState,
    flow: Flow,
    recorder: Any = None,
    config: "FdsxConfig | None" = None,
    log_dir: Path | None = None,
    quiet: bool = False,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
    resume_map_states: set[str] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Map state.

    Iterates over an array resolved via items_path, executing the sub-workflow
    for each item with ${item} scoping. Results are collected in order at result_path.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        from fdsx.core.compiler.execution import ExecutionConfig, execute_internal_task
        from fdsx.logging.stream_logger import StreamLogger

        start_time = time.time()

        items = resolve_jsonpath(state.items_path, state_dict)
        if items is None:
            raise RuntimeError(
                f"Map state '{state_name}': items_path '{state.items_path}' did not resolve to a value"
            )
        if not isinstance(items, list):
            raise RuntimeError(
                f"Map state '{state_name}': items_path resolved to {type(items).__name__}, expected list"
            )

        iters = dict(state_dict.get("_state_iterations", {}))
        iteration = iters.get(state_name, 0) + 1
        iters[state_name] = iteration
        _check_max_iterations(state_name, state, iteration)

        display_map_start(state_name, len(items))
        if recorder is not None:
            recorder.record_map_start(state_name, len(items))

        run_dir = state_dict.get("_meta", {}).get("run_dir", "") or ""
        resume = resume_map_states is not None and state_name in resume_map_states
        if resume_map_states is not None:
            resume_map_states.discard(state_name)
        progress = MapProgress(
            run_dir,
            state_name,
            iteration,
            len(items),
            resume=resume,
            local=isinstance(state.iterator, LocalWorkflow),
        )
        collected = progress.snapshot()
        execution_id = uuid4().hex
        if recorder is not None:
            recorder.record_map_progress(
                state_name, iteration, execution_id, collected, reused=len(collected)
            )

        if len(items) == 0:
            rp_key = _top_key(state.result_path)
            seed: dict[str, Any] = (
                {rp_key: state_dict.get(rp_key)}
                if state_dict.get(rp_key) is not None
                else {}
            )
            partial: dict[str, Any] = set_jsonpath(state.result_path, seed, [])
            partial["_state_iterations"] = iters
            duration = time.time() - start_time
            display_map_complete(state_name, 0, 0, duration)
            if recorder is not None:
                recorder.record_map_complete(
                    state_name,
                    "success",
                    0,
                    0,
                )
            return _strip_reserved_keys(partial)

        completion_lock = Lock()

        def complete_item(
            index: int, result: Any, failed: bool, error: str, started: float
        ) -> None:
            # Serialize publication and its record snapshot as one completion event.
            with completion_lock:
                if not (failed and state.fail_fast):
                    progress.collect(index, result, "failure" if failed else "success")
                    if recorder is not None:
                        recorder.record_map_progress(
                            state_name, iteration, execution_id, progress.snapshot()
                        )
                if recorder is not None:
                    recorder.record_map_iteration_complete(
                        state_name,
                        index,
                        "error" if failed else "success",
                        error if failed else str(result),
                    )
            logger = structlog.get_logger(__name__)
            if failed:
                logger.warning(
                    "map_item_failed",
                    state=state_name,
                    state_iteration=iteration,
                    item_index=index,
                    execution_id=execution_id,
                )
                display_map_iteration_failed(state_name, index, len(items), error)
            else:
                logger.info(
                    "map_item_completed",
                    state=state_name,
                    state_iteration=iteration,
                    item_index=index,
                    execution_id=execution_id,
                )
                display_map_iteration_complete(
                    state_name, index, len(items), duration=time.time() - started
                )

        for idx, item in enumerate(items):
            if idx in collected:
                continue
            display_map_iteration(state_name, idx, len(items))
            iter_start_time = time.time()
            iter_context = deepcopy({**state_dict, "item": item})
            item_log_dir = (
                log_dir / state_name / str(iteration) / execution_id / str(idx)
                if log_dir is not None
                else None
            )
            structlog.get_logger(__name__).info(
                "map_item_started",
                state=state_name,
                state_iteration=iteration,
                item_index=idx,
                execution_id=execution_id,
            )
            if recorder is not None:
                recorder.record_map_item_start(state_name, idx)

            def on_attempt(index: int = idx) -> None:
                if recorder is not None:
                    recorder.record_map_attempt(state_name, index)

            with (
                structlog.contextvars.bound_contextvars(
                    map_name=state_name,
                    state_iteration=iteration,
                    item_index=idx,
                    execution_id=execution_id,
                ),
                observe_attempts(on_attempt),
            ):
                if isinstance(state.iterator, LocalWorkflow):
                    from fdsx.core.engine.local import execute_local

                    outcome = execute_local(
                        state.iterator,
                        f"{state_name}.items.{idx}",
                        iter_context,
                        flow,
                        recorder,
                        config,
                        item_log_dir,
                        quiet,
                        on_process_start,
                    )
                    outcome["index"] = idx
                    failed = outcome["exit_code"] != 0
                    complete_item(
                        idx, outcome, failed, outcome["error"] or "", iter_start_time
                    )
                    if failed and state.fail_fast:
                        raise RuntimeError(
                            f"Map state '{state_name}': item {idx} failed: {outcome['error']}"
                        )
                    continue

                for iter_state in state.iterator.states:
                    merged_options = _merge_provider_options(
                        config,
                        flow,
                        iter_state.provider,
                        iter_state.provider_options,
                        state_name=f"{state_name}.{iter_state.name}",
                    )

                    iter_vars_ctx = inject_builtin_vars(iter_context)
                    resolved_prompt = resolve_template(
                        iter_state.prompt_template or "", iter_vars_ctx
                    )
                    resolved_command = resolve_template_shell_safe(
                        iter_state.command or "", iter_vars_ctx
                    )

                    effective_options = dict(merged_options) if merged_options else None
                    if effective_options:
                        for key in (
                            "system_prompt",
                            "append_system_prompt",
                            "developer_instructions",
                        ):
                            if effective_options.get(key):
                                effective_options[key] = resolve_template(
                                    effective_options[key], iter_vars_ctx
                                )
                    provider = get_provider(iter_state.provider, effective_options)

                    max_retries = (
                        iter_state.retry if iter_state.retry is not None else 3
                    )

                    stream_logger = StreamLogger(
                        f"{state_name}.items.{idx}.{iter_state.name}",
                        item_log_dir,
                        quiet=quiet,
                        iteration=iteration,
                    )
                    iter_resolved_fallback = None
                    if iter_state.extract is not None and config is not None:
                        _flow_ef = getattr(flow, "extraction_fallback", None)
                        if (
                            iter_state.extract.fallback is not None
                            or config.extraction_fallback is not None
                            or (_flow_ef is not None and _flow_ef is not False)
                        ):
                            iter_resolved_fallback = resolve_fallback(
                                iter_state.extract, flow, config
                            )
                    iter_config_profiles = (
                        {k: v.model_dump() for k, v in config.profiles.items()}
                        if config is not None and config.profiles
                        else None
                    )

                    def _on_fallback(event: FallbackEvent, _idx: int = idx) -> None:
                        if recorder is not None:
                            recorder.record_fallback_invocation(
                                state_name=state_name,
                                source=event.source,
                                outcome=event.outcome,
                                pattern=event.pattern,
                                value_preview=event.value_preview,
                                error_kind=event.error_kind,
                                iter_index=_idx,
                            )
                        display_fallback(
                            state_name=state_name,
                            source=event.source,
                            outcome=event.outcome,
                            value_preview=event.value_preview,
                            error_kind=event.error_kind,
                            provider=event.provider,
                            model=event.model,
                        )

                    iter_esc_target = build_escalation_target(
                        config, flow, iter_state.provider
                    )
                    on_iter_esc = None
                    if iter_esc_target is not None:
                        _target = iter_esc_target
                        _idx = idx
                        _total = len(items)

                        def on_iter_esc(
                            _t: EscalationTarget = _target,
                            _i: int = _idx,
                            _tot: int = _total,
                        ) -> None:
                            display_map_iteration_escalation(
                                state_name, _i, _tot, _t.provider_name, _t.model
                            )

                    exec_config = ExecutionConfig(
                        provider=provider,
                        provider_name=iter_state.provider,
                        prompt=resolved_prompt,
                        prompt_prefix=config.prompt_prefix
                        if config is not None
                        else "",
                        command=resolved_command,
                        model=iter_state.model,
                        timeout_seconds=iter_state.timeout_seconds,
                        max_retries=max_retries,
                        extract=iter_state.extract,
                        structured_output=iter_state.structured_output,
                        stream_logger=stream_logger,
                        on_process_start=on_process_start,
                        summary_callback=stream_logger.on_summary,
                        resolved_fallback=iter_resolved_fallback,
                        flow_profiles=getattr(flow, "profiles", None),
                        config_profiles=iter_config_profiles,
                        on_fallback=_on_fallback,
                        escalation=iter_esc_target,
                        on_escalation_activated=on_iter_esc,
                    )
                    exec_result = execute_internal_task(
                        exec_config,
                        state_dict,
                        f"{state_name}.{iter_state.name}",
                        iter_state.fork_from,
                    )
                    result = exec_result.result
                    extracted = exec_result.extracted
                    last_error = exec_result.last_error

                    if result.exit_code != 0 or (
                        iter_state.structured_output is not None
                        and exec_result.structured_value is None
                    ):
                        stream_logger.close()
                        orig = iter_state.provider
                        last = exec_result.last_provider_name or orig
                        annotation = f" (escalated from {orig})" if last != orig else ""
                        error = _sanitize_output(last_error) or ""
                        complete_item(idx, None, True, error, iter_start_time)
                        if state.fail_fast:
                            raise RuntimeError(
                                f"Map state '{state_name}': iteration {idx} failed: "
                                f"Provider {last}{annotation}: {error}"
                            )
                        break

                    if iter_state.extract:
                        if extracted is None:
                            stream_logger.close()
                            complete_item(
                                idx, None, True, "extraction failed", iter_start_time
                            )
                            if state.fail_fast:
                                raise RuntimeError(
                                    f"Map state '{state_name}': iteration {idx} extraction failed"
                                )
                            break
                        iter_result = extracted
                        iter_context = set_jsonpath(
                            iter_state.extract.result_path, iter_context, extracted
                        )
                        iter_context = set_jsonpath(
                            iter_state.result_path, iter_context, result.stdout.strip()
                        )
                    else:
                        iter_result = result.stdout.strip()
                        iter_context = set_jsonpath(
                            iter_state.result_path, iter_context, iter_result
                        )

                    if iter_state.structured_output is not None:
                        iter_context = set_jsonpath(
                            iter_state.structured_output.result_path,
                            iter_context,
                            exec_result.structured_value,
                        )

                else:
                    last_iter_state = state.iterator.states[-1]
                    if last_iter_state.structured_output is not None:
                        last_result = resolve_jsonpath(
                            last_iter_state.structured_output.result_path, iter_context
                        )
                    elif last_iter_state.extract:
                        last_result = resolve_jsonpath(
                            last_iter_state.extract.result_path, iter_context
                        )
                        if last_result is None:
                            last_result = resolve_jsonpath(
                                last_iter_state.result_path, iter_context
                            )
                    else:
                        last_result = resolve_jsonpath(
                            last_iter_state.result_path, iter_context
                        )
                    complete_item(idx, last_result, False, "", iter_start_time)

        saved = progress.snapshot()
        n_failed = sum(entry["status"] == "failure" for entry in saved.values())
        results = [entry["result"] for _, entry in sorted(saved.items())]
        rp_key = _top_key(state.result_path)
        seed = (
            {rp_key: state_dict.get(rp_key)}
            if state_dict.get(rp_key) is not None
            else {}
        )
        partial = set_jsonpath(state.result_path, seed, results)
        partial["_state_iterations"] = iters

        duration = time.time() - start_time
        display_map_complete(state_name, len(items), n_failed, duration)

        if recorder is not None:
            recorder.record_map_complete(
                state_name,
                "success"
                if n_failed == 0 or isinstance(state.iterator, LocalWorkflow)
                else "error",
                len(results),
                n_failed,
            )

        if n_failed > 0 and not isinstance(state.iterator, LocalWorkflow):
            raise RuntimeError(
                f"Map state '{state_name}': {n_failed} of {len(items)} iterations failed"
            )

        return _strip_reserved_keys(partial)

    return node

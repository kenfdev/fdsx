"""Parallel state node factories for the compiler package."""

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.types import Send

from fdsx.core.extraction_fallback import FallbackEvent, resolve_fallback
from fdsx.core.variables import (
    _strip_reserved_keys,
    inject_builtin_vars,
    resolve_template,
    resolve_template_shell_safe,
    set_jsonpath,
    write_result_to_file,
)
from fdsx.display import terminal
from fdsx.display.terminal import _sanitize_output
from fdsx.models.flow import Flow, ParallelState
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

    e.g. "$.review.results" -> "review", "$.output" -> "output", "$.items[0].x" -> "items"
    """
    stripped = path[2:] if path.startswith("$.") else path
    return stripped.split(".")[0].split("[")[0]


def _create_dispatch_node(
    state_name: str, state: ParallelState, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the dispatch node for a Parallel state.

    Displays the parallel state start line and triggers fan-out via Send.
    Returns updated _state_iterations counter. Fan-out is triggered by conditional edges.
    Only emits display_parallel_start (not display_state_start) to match CLI contract.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        terminal.display_parallel_start(state_name, len(state.branches))
        if recorder is not None:
            recorder.record_state_start(state_name, "parallel")
        iters = dict(state_dict.get("_state_iterations", {}))
        iteration = iters.get(state_name, 0) + 1
        iters[state_name] = iteration
        _check_max_iterations(state_name, state, iteration)
        return {"_state_iterations": iters}

    return node


def _create_branch_executor(
    state_name: str,
    state: ParallelState,
    flow: Flow,
    recorder: Any = None,
    config: "FdsxConfig | None" = None,
    log_dir: Path | None = None,
    quiet: bool = False,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a shared branch executor node invoked once per branch via Send.

    Reads `_branch_index` from the state dict to identify which branch to run.
    Returns `{f"_br_{state_name}": [result]}` — accumulated by _parallel_branch_reducer reducer.
    Never raises: all errors are captured in the result dict (exit_code != 0).
    """

    branch_esc_targets = [
        build_escalation_target(config, flow, branch.provider)
        for branch in state.branches
    ]

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        from fdsx.core.compiler.execution import ExecutionConfig, execute_with_retry
        from fdsx.logging.stream_logger import StreamLogger

        branch_index: int = state_dict.get("_branch_index", 0)
        branch = state.branches[branch_index]
        esc_target = branch_esc_targets[branch_index]

        start_time = time.time()
        terminal.display_branch_start(
            state_name=state_name,
            branch_index=branch_index,
            provider=branch.provider,
            model=branch.model,
        )

        vars_ctx = inject_builtin_vars(state_dict)
        resolved_prompt = resolve_template(branch.prompt_template or "", vars_ctx)
        resolved_command = resolve_template_shell_safe(branch.command or "", vars_ctx)

        merged_options = _merge_provider_options(
            config,
            flow,
            branch.provider,
            branch.provider_options,
            state_name=state_name,
        )
        effective_options = dict(merged_options) if merged_options else None
        if effective_options:
            for key in ("system_prompt", "append_system_prompt"):
                if effective_options.get(key):
                    effective_options[key] = resolve_template(
                        effective_options[key], vars_ctx
                    )
        provider = get_provider(branch.provider, effective_options)

        max_retries = branch.retry if branch.retry is not None else 3

        iters = state_dict.get("_state_iterations", {})
        iteration = iters.get(state_name, 1)
        branch_log_name = f"{state_name}_branch{branch_index + 1}"

        stream_logger = StreamLogger(
            branch_log_name, log_dir, quiet=quiet, iteration=iteration
        )
        branch_resolved_fallback = None
        if branch.extract is not None and config is not None:
            _flow_ef = getattr(flow, "extraction_fallback", None)
            if (
                branch.extract.fallback is not None
                or config.extraction_fallback is not None
                or (_flow_ef is not None and _flow_ef is not False)
            ):
                branch_resolved_fallback = resolve_fallback(
                    branch.extract, flow, config
                )
        branch_config_profiles = (
            {k: v.model_dump() for k, v in config.profiles.items()}
            if config is not None and config.profiles
            else None
        )

        def _on_fallback(event: FallbackEvent, _bi: int = branch_index) -> None:
            if recorder is not None:
                recorder.record_fallback_invocation(
                    state_name=state_name,
                    source=event.source,
                    outcome=event.outcome,
                    pattern=event.pattern,
                    value_preview=event.value_preview,
                    error_kind=event.error_kind,
                    branch_index=_bi,
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

        on_esc = None
        if esc_target is not None:
            _target = esc_target
            _bidx = branch_index

            def on_esc(_t: EscalationTarget = _target, _i: int = _bidx) -> None:
                terminal.display_branch_escalation(
                    state_name, _i, _t.provider_name, _t.model
                )

        exec_config = ExecutionConfig(
            provider=provider,
            provider_name=branch.provider,
            prompt=resolved_prompt,
            command=resolved_command,
            model=branch.model,
            timeout_seconds=branch.timeout_seconds,
            max_retries=max_retries,
            extract=branch.extract,
            stream_logger=stream_logger,
            on_process_start=on_process_start,
            summary_callback=stream_logger.on_summary,
            resolved_fallback=branch_resolved_fallback,
            flow_profiles=getattr(flow, "profiles", None),
            config_profiles=branch_config_profiles,
            on_fallback=_on_fallback,
            escalation=esc_target,
            on_escalation_activated=on_esc,
        )
        exec_result = execute_with_retry(exec_config)
        result = exec_result.result
        extracted = exec_result.extracted
        last_error = exec_result.last_error

        duration = time.time() - start_time

        if result.exit_code != 0:
            terminal.display_branch_failed(
                state_name=state_name,
                branch_index=branch_index,
                provider=branch.provider,
                model=branch.model,
            )
            orig = branch.provider
            last = exec_result.last_provider_name or orig
            annotation = f" (escalated from {orig})" if last != orig else ""
            branch_result: dict[str, Any] = {
                "index": branch_index,
                "output": result.stdout.strip(),
                "exit_code": result.exit_code,
                "error": (
                    f"Provider {last} failed after {max_retries + 1} attempts{annotation}: {_sanitize_output(last_error)}"
                    if annotation
                    else _sanitize_output(last_error)
                ),
                "_duration": duration,
            }
        elif branch.extract and extracted is None:
            terminal.display_branch_failed(
                state_name=state_name,
                branch_index=branch_index,
                provider=branch.provider,
                model=branch.model,
            )
            branch_result = {
                "index": branch_index,
                "output": result.stdout.strip(),
                "exit_code": 1,
                "error": f"Extraction failed after {max_retries + 1} attempts: all strategies returned None",
                "_duration": duration,
            }
        else:
            terminal.display_branch_complete(
                state_name=state_name,
                branch_index=branch_index,
                provider=branch.provider,
                model=branch.model,
                duration=duration,
            )
            branch_result = {
                "index": branch_index,
                "output": result.stdout.strip(),
                "exit_code": 0,
                "error": None,
                "_duration": duration,
            }

        if branch.extract and extracted is not None:
            branch_result = set_jsonpath(
                branch.extract.result_path, branch_result, extracted
            )

        return _strip_reserved_keys({f"_br_{state_name}": [branch_result]})

    return node


def _create_fan_out(
    state_name: str, state: ParallelState
) -> Callable[[dict[str, Any]], list[Send]]:
    """Create the fan-out function that returns one Send per branch.

    Each Send carries a fresh `_br_{state_name}: []` reset so that accumulation
    from a previous pass through this parallel state (in a loop) does not bleed
    into the current pass.
    """

    def fan_out(state_dict: dict[str, Any]) -> list[Send]:
        return [
            Send(
                f"_branch_{state_name}",
                {**state_dict, "_branch_index": i, f"_br_{state_name}": []},
            )
            for i in range(len(state.branches))
        ]

    return fan_out


def _create_collector_node(
    state_name: str, state: ParallelState, flow: Flow, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the fan-in collector node for a Parallel state.

    Reads branch results accumulated by the _parallel_branch_reducer reducer, sorts by index,
    enforces min_success (defaulting to all branches), and stores at result_path.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        start_time = time.time()

        raw_results: list[dict[str, Any]] = state_dict.get(f"_br_{state_name}", [])

        sorted_results = sorted(raw_results, key=lambda r: r.get("index", 0))

        branch_info_list: list[dict[str, Any]] = []
        for r in sorted_results:
            branch_info_list.append(
                {
                    "index": r.get("index", 0),
                    "provider": state.branches[r.get("index", 0)].provider
                    if r.get("index", 0) < len(state.branches)
                    else "unknown",
                    "status": "success" if r.get("exit_code") == 0 else "error",
                    "duration_seconds": int(r.get("_duration", 0)),
                }
            )

        clean_results = [
            {k: v for k, v in r.items() if k != "index" and k != "_duration"}
            for r in sorted_results
        ]

        min_required = (
            state.min_success if state.min_success is not None else len(state.branches)
        )
        successful = sum(1 for r in clean_results if r.get("exit_code") == 0)

        if successful < min_required:
            failed_branches = [
                f"branch {i}: {r.get('error', 'unknown error')}"
                for i, r in enumerate(clean_results)
                if r.get("exit_code") != 0
            ]
            if recorder is not None:
                recorder.record_state_complete(
                    state_name,
                    "error",
                    f"Only {successful}/{len(state.branches)} branches succeeded",
                    [state.result_path],
                    branch_info_list,
                )
            raise RuntimeError(
                f"Parallel state '{state_name}' failed: only {successful}/{len(state.branches)} "
                f"branches succeeded, required {min_required}. "
                f"Failed branches: {'; '.join(failed_branches)}"
            )

        rp_key = _top_key(state.result_path)
        partial: dict[str, Any] = (
            {rp_key: state_dict.get(rp_key)}
            if state_dict.get(rp_key) is not None
            else {}
        )
        partial = set_jsonpath(state.result_path, partial, clean_results)

        if state.result_file:
            run_dir = state_dict.get("_meta", {}).get("run_dir", "")
            if run_dir:
                varname = state.result_file[2:]  # strip "$."
                file_path = write_result_to_file(varname, clean_results, Path(run_dir))
                rf_key = _top_key(state.result_file)
                if rf_key not in partial and state_dict.get(rf_key) is not None:
                    partial[rf_key] = state_dict[rf_key]  # seed sibling-safe channel
                partial = set_jsonpath(state.result_file, partial, file_path)

        display_results = []
        for r in sorted_results:
            idx = r.get("index", 0)
            if idx < len(state.branches):
                branch = state.branches[idx]
                display_results.append(
                    {
                        **r,
                        "provider": branch.provider,
                        "model": branch.model,
                    }
                )
            else:
                display_results.append({**r, "provider": "unknown", "model": None})

        terminal.display_parallel_results(state_name, display_results)

        duration = time.time() - start_time
        terminal.display_state_complete(state_name, duration)

        recorded_paths = [state.result_path]
        if state.result_file:
            recorded_paths.append(state.result_file)

        if recorder is not None:
            recorder.record_state_complete(
                state_name,
                "success",
                "",
                recorded_paths,
                branch_info_list,
            )

        # Reset the branch accumulator so the next loop iteration starts clean.
        # The custom _parallel_branch_reducer treats [] as a reset signal.
        partial[f"_br_{state_name}"] = []

        return _strip_reserved_keys(partial)

    return node

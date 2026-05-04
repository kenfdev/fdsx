"""Map state iteration node factory for the compiler package."""

import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fdsx.core.extraction_fallback import resolve_fallback
from fdsx.core.variables import (
    resolve_jsonpath,
    resolve_template,
    resolve_template_shell_safe,
    set_jsonpath,
)
from fdsx.display.terminal import (
    _sanitize_output,
    display_map_complete,
    display_map_iteration,
    display_map_iteration_complete,
    display_map_iteration_failed,
    display_map_start,
)
from fdsx.models.flow import (
    Flow,
    MapState,
)
from fdsx.providers.base import get_provider

from .helpers import (
    _check_max_iterations,
    _merge_provider_options,
)

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig


def _top_key(path: str) -> str:
    """Extract the top-level channel key from a JSONPath expression.

    e.g. "$.steps.processed" -> "steps", "$.output" -> "output", "$.items[0].x" -> "items"
    """
    stripped = path[2:] if path.startswith("$.") else path
    return stripped.split(".")[0].split("[")[0]


_MAP_PROGRESS_FILENAME = "progress.json"


def _safe_progress_dir(run_dir: str, state_name: str) -> Path | None:
    """Return the progress directory, or None if the path escapes run_dir."""
    if not run_dir:
        return None
    base = Path(run_dir).resolve()
    candidate = (base / state_name).resolve()
    if not str(candidate).startswith(str(base) + "/") and candidate != base:
        return None
    return candidate


def _read_map_progress(run_dir: str, state_name: str) -> dict[str, Any] | None:
    """Read map progress from checkpoint file.

    Returns None if no progress file exists or reading fails.
    """
    progress_dir = _safe_progress_dir(run_dir, state_name)
    if progress_dir is None:
        return None
    progress_file = progress_dir / _MAP_PROGRESS_FILENAME
    try:
        with progress_file.open() as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return None
    except (OSError, json.JSONDecodeError):
        return None


def _write_map_progress(
    run_dir: str, state_name: str, completed_iterations: int, results: list[Any]
) -> None:
    """Write map progress to checkpoint file atomically."""
    progress_dir = _safe_progress_dir(run_dir, state_name)
    if progress_dir is None:
        return
    progress_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_file = progress_dir / f"{_MAP_PROGRESS_FILENAME}.tmp"
    progress_data = {
        "completed_iterations": completed_iterations,
        "results": results,
    }
    fd = os.open(str(temp_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(progress_data, f)
    temp_file.replace(progress_dir / _MAP_PROGRESS_FILENAME)


def _create_map_node(
    state_name: str,
    state: MapState,
    flow: Flow,
    recorder: Any = None,
    config: "FdsxConfig | None" = None,
    log_dir: Path | None = None,
    quiet: bool = False,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Map state.

    Iterates over an array resolved via items_path, executing the sub-workflow
    for each item with ${item} scoping. Results are collected in order at result_path.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        from fdsx.core.compiler.execution import ExecutionConfig, execute_with_retry
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
            return partial

        results: list[Any] = []
        n_failed = 0

        run_dir = state_dict.get("_meta", {}).get("run_dir", "") or ""
        progress = _read_map_progress(run_dir, state_name)
        if progress and len(progress.get("results", [])) <= len(items):
            start_idx = progress["completed_iterations"]
            results = list(progress["results"])
        else:
            start_idx = 0
            results = []

        for idx, item in enumerate(items):
            if idx < start_idx:
                continue
            display_map_iteration(state_name, idx, len(items))
            iter_start_time = time.time()
            iter_context = {**state_dict, "item": item}
            iter_steps: dict[str, Any] = {}

            for iter_state in state.iterator.states:
                merged_options = _merge_provider_options(
                    config,
                    flow,
                    iter_state.provider,
                    iter_state.provider_options,
                    state_name=f"{state_name}.{iter_state.name}",
                )

                resolved_prompt = resolve_template(
                    iter_state.prompt_template or "", iter_context
                )
                resolved_command = resolve_template_shell_safe(
                    iter_state.command or "", iter_context
                )

                effective_options = dict(merged_options) if merged_options else None
                if effective_options:
                    for key in ("system_prompt", "append_system_prompt"):
                        if effective_options.get(key):
                            effective_options[key] = resolve_template(
                                effective_options[key], iter_context
                            )
                provider = get_provider(iter_state.provider, effective_options)

                max_retries = iter_state.retry if iter_state.retry is not None else 3

                stream_logger = StreamLogger(
                    f"{state_name}.{iter_state.name}",
                    log_dir,
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
                exec_config = ExecutionConfig(
                    provider=provider,
                    provider_name=iter_state.provider,
                    prompt=resolved_prompt,
                    command=resolved_command,
                    model=iter_state.model,
                    timeout_seconds=iter_state.timeout_seconds,
                    max_retries=max_retries,
                    extract=iter_state.extract,
                    stream_logger=stream_logger,
                    on_process_start=on_process_start,
                    summary_callback=stream_logger.on_summary,
                    resolved_fallback=iter_resolved_fallback,
                    flow_profiles=getattr(flow, "profiles", None),
                    config_profiles=iter_config_profiles,
                )
                exec_result = execute_with_retry(exec_config)
                result = exec_result.result
                extracted = exec_result.extracted
                last_error = exec_result.last_error

                if result.exit_code != 0:
                    stream_logger.close()
                    if state.fail_fast:
                        display_map_iteration_failed(
                            state_name,
                            idx,
                            len(items),
                            f"iteration {idx} failed: {_sanitize_output(last_error)}",
                        )
                        if recorder is not None:
                            recorder.record_map_iteration_complete(
                                state_name,
                                idx,
                                "error",
                                _sanitize_output(last_error) or "",
                            )
                            recorder.record_state_error(
                                state_name,
                                f"iteration {idx} failed: {_sanitize_output(last_error)}",
                            )
                        raise RuntimeError(
                            f"Map state '{state_name}': iteration {idx} failed: {_sanitize_output(last_error)}"
                        )
                    else:
                        display_map_iteration_failed(
                            state_name,
                            idx,
                            len(items),
                            f"iteration {idx} failed: {_sanitize_output(last_error)}",
                        )
                        if recorder is not None:
                            recorder.record_map_iteration_complete(
                                state_name,
                                idx,
                                "error",
                                _sanitize_output(last_error) or "",
                            )
                        results.append(None)
                        n_failed += 1
                        _write_map_progress(run_dir, state_name, idx + 1, results)
                        break

                if iter_state.extract:
                    if extracted is None:
                        stream_logger.close()
                        if state.fail_fast:
                            display_map_iteration_failed(
                                state_name,
                                idx,
                                len(items),
                                f"iteration {idx} extraction failed",
                            )
                            if recorder is not None:
                                recorder.record_map_iteration_complete(
                                    state_name,
                                    idx,
                                    "error",
                                    "extraction failed",
                                )
                                recorder.record_state_error(
                                    state_name,
                                    f"iteration {idx} extraction failed",
                                )
                            raise RuntimeError(
                                f"Map state '{state_name}': iteration {idx} extraction failed"
                            )
                        else:
                            display_map_iteration_failed(
                                state_name,
                                idx,
                                len(items),
                                f"iteration {idx} extraction failed",
                            )
                            if recorder is not None:
                                recorder.record_map_iteration_complete(
                                    state_name,
                                    idx,
                                    "error",
                                    "extraction failed",
                                )
                            results.append(None)
                            n_failed += 1
                            _write_map_progress(run_dir, state_name, idx + 1, results)
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

                iter_steps[iter_state.name] = {"results": iter_result}

            else:
                last_iter_state = state.iterator.states[-1]
                if last_iter_state.extract:
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
                results.append(last_result)
                _write_map_progress(run_dir, state_name, idx + 1, results)
                display_map_iteration_complete(
                    state_name, idx, len(items), duration=time.time() - iter_start_time
                )
                if recorder is not None:
                    recorder.record_map_iteration_complete(
                        state_name,
                        idx,
                        "success",
                        str(last_result) if last_result is not None else "",
                    )

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
                "success" if n_failed == 0 else "error",
                len(results),
                n_failed,
            )

        if n_failed > 0:
            raise RuntimeError(
                f"Map state '{state_name}': {n_failed} of {len(items)} iterations failed"
            )

        return partial

    return node

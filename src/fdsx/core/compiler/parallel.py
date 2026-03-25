"""Parallel state node factories for the compiler package."""
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from langgraph.types import Send

from fdsx.core.variables import (
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
    _check_max_iterations,
    _merge_provider_options,
    _set_next_state_meta,
)

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig


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
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a shared branch executor node invoked once per branch via Send.

    Reads `_branch_index` from the state dict to identify which branch to run.
    Returns `{f"_br_{state_name}": [result]}` — accumulated by _parallel_branch_reducer reducer.
    Never raises: all errors are captured in the result dict (exit_code != 0).
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        from fdsx.core.compiler.execution import ExecutionConfig, execute_with_retry
        from fdsx.logging.stream_logger import StreamLogger

        branch_index: int = state_dict.get("_branch_index", 0)
        branch = state.branches[branch_index]

        start_time = time.time()
        terminal.display_branch_start(
            state_name=state_name,
            branch_index=branch_index,
            provider=branch.provider,
            model=branch.model,
        )

        resolved_prompt = resolve_template(branch.prompt_template or "", state_dict)
        resolved_command = resolve_template_shell_safe(branch.command or "", state_dict)

        merged_options = _merge_provider_options(
            config, flow, branch.provider, branch.provider_options
        )
        provider = get_provider(branch.provider, merged_options)

        max_retries = branch.retry if branch.retry is not None else 3

        iters = state_dict.get("_state_iterations", {})
        iteration = iters.get(state_name, 1)
        branch_log_name = f"{state_name}_branch{branch_index + 1}"

        stream_logger = StreamLogger(branch_log_name, log_dir, quiet=quiet, iteration=iteration)
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
            branch_result: dict[str, Any] = {
                "index": branch_index,
                "output": result.stdout.strip(),
                "exit_code": result.exit_code,
                "error": _sanitize_output(last_error),
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

        return {f"_br_{state_name}": [branch_result]}

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

        new_state = set_jsonpath(state.result_path, state_dict, clean_results)

        if state.result_file:
            run_dir = state_dict.get("_meta", {}).get("run_dir", "")
            if run_dir:
                varname = state.result_file[2:]  # strip "$."
                file_path = write_result_to_file(varname, clean_results, Path(run_dir))
                new_state = set_jsonpath(state.result_file, new_state, file_path)

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
        new_state[f"_br_{state_name}"] = []

        new_state = _set_next_state_meta(new_state, state)
        return new_state

    return node

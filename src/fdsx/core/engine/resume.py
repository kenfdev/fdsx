"""resume_flow implementation for the engine package."""

import sys
from pathlib import Path
from typing import Any, Literal, cast

from langgraph.types import Command

from fdsx.checkpoint.manager import CheckpointManager, _extract_meta_from_checkpoint
from fdsx.core.compiler import compile_flow
from fdsx.core.config import load_config
from fdsx.core.loader import load_flow
from fdsx.display.terminal import (
    _sanitize_output,
    display_completion_summary,
    display_wait_prompt,
)
from fdsx.logging import RunRecorder
from fdsx.logging.recorder import LOGS_DIR_NAME
from fdsx.models.task import load_task_file, save_task_file

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


def resume_flow(
    thread_id: str,
    base_dir: Path | None = None,
    flow_path: Path | None = None,
) -> FlowResult:
    """Resume a flow from a checkpoint.

    Args:
        thread_id: The thread ID to resume
        base_dir: Base directory for checkpoints (.fdsx/). Defaults to '.fdsx/'.
        flow_path: Optional path to the flow YAML file. Required if not stored in checkpoint.

    Returns:
        Final state variables as result dict.

    Raises:
        RuntimeError: If checkpoint is corrupt or execution fails
    """
    if base_dir is None:
        base_dir = CheckpointManager.DEFAULT_BASE_DIR

    checkpoint_manager = CheckpointManager(base_dir=base_dir)

    if not checkpoint_manager.verify_checkpoint(thread_id):
        raise RuntimeError(f"No checkpoint found for thread ID {thread_id}")

    if not checkpoint_manager.acquire_lock(thread_id):
        locked, pid = checkpoint_manager.is_locked(thread_id)
        if locked:
            raise RuntimeError(f"Thread {thread_id} is locked by PID {pid}")

    print(f"Resuming from thread: {_sanitize_output(thread_id)}", file=sys.stderr)

    recorder: RunRecorder | None = None
    last_state: dict[str, Any] = {}

    try:
        checkpointer = checkpoint_manager.get_checkpointer()

        if flow_path is None or not flow_path.exists():
            config_for_lookup: Any = {"configurable": {"thread_id": thread_id}}
            checkpoint = checkpointer.get(config_for_lookup)

            if checkpoint:
                stored_meta = _extract_meta_from_checkpoint(checkpoint)
                if isinstance(stored_meta, dict):
                    flow_path_str = stored_meta.get("flow_path")
                    if flow_path_str:
                        flow_path = Path(flow_path_str)

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

        flow, errors = load_flow(flow_path, config_profiles=config_profiles)
        if flow is None:
            raise RuntimeError(f"Failed to load flow for resume: {', '.join(errors)}")

        from fdsx.logging.recorder import RUN_FILENAME, RUNS_DIR_NAME
        from fdsx.models.flow import ParallelState, WaitState

        runs_dir = base_dir / RUNS_DIR_NAME
        existing_log_path = runs_dir / thread_id / RUN_FILENAME

        if existing_log_path.exists():
            import json

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
        )

        resume_run_dir = base_dir / RUNS_DIR_NAME / thread_id
        resume_log_dir = resume_run_dir / LOGS_DIR_NAME

        handler = SignalHandler(checkpoint_manager, thread_id)

        compiled = compile_flow(
            flow,
            checkpointer=checkpointer,
            recorder=recorder,
            config=config,
            log_dir=resume_log_dir,
            on_process_start=handler.register_process,
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

        state_info = compiled.graph.get_state(resume_config)
        existing_meta = state_info.values.get("_meta", {}) if state_info.values else {}
        if "run_dir" not in existing_meta:
            updated_meta = {**existing_meta, "run_dir": str(resume_run_dir)}
            compiled.graph.update_state(resume_config, {"_meta": updated_meta})

        state_info = compiled.graph.get_state(resume_config)
        with handler:
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

                    for chunk in compiled.graph.stream(
                        Command(resume=user_selection),
                        config=resume_config,
                        stream_mode="values",
                        version="v2",
                    ):
                        last_state = chunk["data"]
                else:
                    # Error/pending task (no interrupt) — re-execute from checkpoint
                    for chunk in compiled.graph.stream(
                        None, config=resume_config, stream_mode="values", version="v2"
                    ):
                        last_state = chunk["data"]
            else:
                for chunk in compiled.graph.stream(
                    None, config=resume_config, stream_mode="values", version="v2"
                ):
                    last_state = chunk["data"]

            # Continue handling any further interrupts (e.g. multi-Wait flows)
            last_state = handle_interrupts(compiled.graph, resume_config, last_state)

        # Read authoritative state from checkpointer after resume completes
        final_state_info = compiled.graph.get_state(resume_config)
        if final_state_info.values:
            last_state = final_state_info.values

        results = _extract_results(last_state, compiled.result_paths)
        status: str = "completed"
        failed_state: str | None = None
        if recorder is not None:
            status, failed_state, _ = _detect_abort_status(recorder)
            recorder.finalize(_sanitize_state_for_log(last_state), status)
            recorder.save(base_dir=base_dir)
            if failed_state is not None:
                display_completion_summary(
                    recorder.flow_name,
                    _calc_elapsed(recorder),
                    failed_state,
                    "workflow aborted",
                )
            else:
                display_completion_summary(recorder.flow_name, _calc_elapsed(recorder))

        # Best-effort: update task YAML entry if stored in _meta
        _meta = last_state.get("_meta", {})
        _task_file_path_str = _meta.get("task_file_path")
        _task_entry_index = _meta.get("task_entry_index")
        if _task_file_path_str is not None and _task_entry_index is not None:
            try:
                _task_file_path = Path(_task_file_path_str)
                _task_file = load_task_file(_task_file_path)
                _entry = _task_file.entries[_task_entry_index]
                _new_status = "failed" if status == "aborted" else "completed"
                _entry.status = cast(
                    Literal["pending", "running", "completed", "failed"], _new_status
                )
                _entry.thread_id = thread_id
                _entry.error = (
                    f"workflow aborted at state '{failed_state}'"
                    if status == "aborted"
                    else None
                )
                save_task_file(_task_file_path, _task_file)
            except (FileNotFoundError, IndexError, ValueError):
                pass  # best-effort: do not raise if file is missing or index is invalid

        return FlowResult(results=results, status=status, abort_state=failed_state)
    except Exception as e:
        if recorder is not None:
            recorder.finalize(_sanitize_state_for_log(last_state), "error")
            recorder.save(base_dir=base_dir)
            failed = _find_failed_state(recorder)
            failed_state_name = failed[0] if failed else "unknown"
            error_message = failed[1] if (failed and failed[1]) else str(e)
            display_completion_summary(
                recorder.flow_name,
                _calc_elapsed(recorder),
                failed_state_name,
                error_message,
            )
        raise RuntimeError(f"Flow resume failed: {e}") from e
    finally:
        checkpoint_manager.release_lock(thread_id)

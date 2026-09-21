"""Execute a local graph without publishing its private state or checkpoints."""

import subprocess
from collections.abc import Callable
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from fdsx.core.compiler.execution import TaskExecutionError
from fdsx.core.compiler.helpers import MaxIterationsReachedError
from fdsx.core.engine.validate import FailStateTermination
from fdsx.core.evaluation import EvaluationError
from fdsx.core.hooks import HookAbortError
from fdsx.core.structured_output import StructuredOutputValidationError
from fdsx.core.variables import jsonpath_exists, resolve_jsonpath
from fdsx.models.flow import Flow, LocalWorkflow
from fdsx.providers.base import ProviderSessionError

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig

log = structlog.get_logger(__name__)


def execute_local(
    definition: LocalWorkflow,
    scope: str,
    parent: dict[str, Any],
    flow: Flow,
    recorder: Any = None,
    config: "FdsxConfig | None" = None,
    log_dir: Path | None = None,
    quiet: bool = False,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> dict[str, Any]:
    from fdsx.core.compiler.local import compile_local

    limit = definition.max_loop if definition.max_loop is not None else flow.max_loop

    context = deepcopy(
        {
            key: value
            for key, value in parent.items()
            if not key.startswith("_")
            and key not in {"remaining_steps", "state", "run_path"}
        }
    )
    context["_meta"] = deepcopy(
        {
            key: value
            for key, value in parent.get("_meta", {}).items()
            if key not in {"terminal_status", "terminal_failure"}
        }
    )
    context["_state_iterations"] = {}
    # Keep run_dir (run_path and hook context) unchanged. Only managed result
    # files use a stable per-subject directory, including when replayed.
    run_dir = context["_meta"].get("run_dir")
    if run_dir:
        scope_key = sha256(scope.encode("utf-8")).hexdigest()
        context["_meta"]["result_file_dir"] = str(
            Path(run_dir) / "local-results" / scope_key
        )
    from fdsx.logging import RunRecorder

    child_recorder = (
        RunRecorder(recorder.thread_id, recorder.flow_name)
        if recorder is not None
        else None
    )
    compiled = compile_local(
        definition,
        scope,
        flow,
        input_keys=set(context),
        recorder=child_recorder,
        config=config,
        log_dir=log_dir,
        quiet=quiet,
        on_process_start=on_process_start,
    )
    error: str | None = None
    output: Any = None
    try:
        result = compiled.graph.invoke(
            context,
            config={
                "recursion_limit": max(1, limit) * (len(definition.states) + 1) + 2,
            },
        )
        error = result.get("_meta", {}).get("terminal_status")
        if error is None:
            if jsonpath_exists(definition.output_path, result):
                output = resolve_jsonpath(definition.output_path, result)
            else:
                error = "missing_local_output"
    except FailStateTermination as failure:
        error = failure.error
    except TaskExecutionError:
        # Preserve the existing public envelope for exhausted task failures.
        error = "RuntimeError"
    except (
        MaxIterationsReachedError,
        EvaluationError,
        HookAbortError,
        StructuredOutputValidationError,
        ProviderSessionError,
    ) as failure:
        error = type(failure).__name__
    finally:
        if recorder is not None and child_recorder is not None:
            recorder.record_local_workflow(scope, child_recorder)
    if error is not None:
        log.warning("local_workflow_failed", scope=scope, error_kind=error)
    return {
        "exit_code": 1 if error is not None else 0,
        "error": error,
        "output": output,
    }

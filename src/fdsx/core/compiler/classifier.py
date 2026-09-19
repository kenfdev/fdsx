"""Graph adapters for atomic classification, including parallel branch results."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from fdsx.core.classifier import ClassifierError, classify
from fdsx.core.evaluation import EvaluationError
from fdsx.logging.classifier import ClassifierAudit, ClassifierRecordingError
from fdsx.models.classifier import ClassifierDefinition

log = structlog.get_logger(__name__)


def execute_classifier(
    name: str,
    definition: ClassifierDefinition,
    state: dict[str, Any],
    recorder: Any = None,
    quiet: bool = False,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> dict[str, Any]:
    run_dir = state.get("_meta", {}).get("run_dir")
    audit = ClassifierAudit(name, Path(run_dir) if run_dir else None, recorder, quiet)
    try:
        return classify(
            definition,
            state,
            location=name,
            emit=audit.emit,
            save_full=audit.save_full,
            on_process_start=on_process_start,
        )
    except (ClassifierError, EvaluationError, ClassifierRecordingError):
        audit.emit("failed", {"reason": "classifier failed"})
        log.error("classifier_state_failed", state=name)
        raise


def create_classifier_node(
    name: str,
    definition: ClassifierDefinition,
    recorder: Any = None,
    quiet: bool = False,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def node(state: dict[str, Any]) -> dict[str, Any]:
        if recorder is not None:
            recorder.record_state_start(name, "classifier")
        try:
            result = execute_classifier(
                name, definition, state, recorder, quiet, on_process_start
            )
        except (ClassifierError, EvaluationError, ClassifierRecordingError) as error:
            if recorder is not None:
                recorder.record_state_error(name, str(error))
            raise
        if recorder is not None:
            recorder.record_state_complete(
                name, "success", "", [definition.result_path]
            )
        iterations = dict(state.get("_state_iterations", {}))
        iterations[name] = iterations.get(name, 0) + 1
        return {definition.result_path[2:]: result, "_state_iterations": iterations}

    return node

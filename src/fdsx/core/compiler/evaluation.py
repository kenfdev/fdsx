"""Graph adapter for the shared evaluator."""

from collections.abc import Callable
from time import monotonic
from typing import Any

import structlog

from fdsx.core.evaluation import EvaluationError, evaluate
from fdsx.core.variables import jsonpath_exists, resolve_jsonpath
from fdsx.models.flow import EvaluateState

log = structlog.get_logger(__name__)


def create_evaluate_node(
    name: str, definition: EvaluateState, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def node(state: dict[str, Any]) -> dict[str, Any]:
        started = monotonic()
        iterations = dict(state.get("_state_iterations", {}))
        iterations[name] = iterations.get(name, 0) + 1
        if recorder is not None:
            recorder.record_state_start(name, "evaluate")
        try:
            materials: dict[str, Any] = {}
            for key, material in definition.input.items():
                if material.ref is not None:
                    if not jsonpath_exists(material.ref, state):
                        raise EvaluationError(
                            f"Evaluation {name}.input.{key}: missing reference"
                        )
                    materials[key] = resolve_jsonpath(material.ref, state)
                else:
                    materials[key] = material.literal
            result = evaluate(
                materials, definition.questions, model=definition.model, location=name
            )
        except EvaluationError as error:
            log.error(
                "evaluation_state_failed",
                state=name,
                duration_seconds=monotonic() - started,
            )
            if recorder is not None:
                recorder.record_state_error(name, str(error))
            raise
        if recorder is not None:
            recorder.record_state_complete(
                name, "success", "", [definition.result_path]
            )
        log.info(
            "evaluation_state_completed",
            state=name,
            duration_seconds=monotonic() - started,
        )
        return {
            definition.result_path[2:]: result.to_dict(),
            "_state_iterations": iterations,
        }

    return node

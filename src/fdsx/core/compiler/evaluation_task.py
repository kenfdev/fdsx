"""Task adapter for evaluation, without LLM retries or raw-input recording."""

from collections.abc import Callable
from typing import Any

import structlog

from fdsx.core.evaluation import EvaluationError
from fdsx.core.structured_output import (
    StructuredOutputValidationError,
    create_structured_output_validator,
    parse_structured_output,
)
from fdsx.core.variables import (
    extract_template_references,
    inject_builtin_vars,
    resolve_jsonpath,
    resolve_template,
    set_jsonpath,
)
from fdsx.models.flow import TaskState
from fdsx.providers.jev import JevProvider

from .helpers import _check_max_iterations

log = structlog.get_logger(__name__)


def create_evaluation_task_node(
    name: str, definition: TaskState, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    contract = definition.structured_output
    if contract is None:
        log.error("evaluation_task_invalid", state=name)
        raise EvaluationError(f"Evaluation {name}: structured_output required")
    provider = JevProvider(location=name)
    validator = create_structured_output_validator(
        contract.schema_document, allow_extra_fields=contract.allow_extra_fields
    )

    def node(state: dict[str, Any]) -> dict[str, Any]:
        iterations = dict(state.get("_state_iterations", {}))
        iterations[name] = iterations.get(name, 0) + 1
        _check_max_iterations(name, definition, iterations[name])
        if recorder is not None:
            recorder.record_state_start(name, "task")
        try:
            variables = inject_builtin_vars(state, state_iteration=iterations[name])
            template = definition.prompt_template or ""
            if any(
                resolve_jsonpath(ref, variables) is None
                for ref in extract_template_references(template)
            ):
                raise EvaluationError(f"Evaluation {name}: missing input reference")
            prompt = resolve_template(template, variables)
            response = provider.execute(
                prompt, model=definition.model, output_schema=contract.schema_document
            )
            result = response.evaluation
            if result is None:
                raise EvaluationError(f"Evaluation {name}: missing validated response")
            try:
                value = parse_structured_output(response.stdout, validator)
            except StructuredOutputValidationError:
                raise EvaluationError(
                    f"Evaluation {name}: output contract mismatch"
                ) from None
        except EvaluationError as error:
            log.error("evaluation_task_failed", state=name)
            if recorder is not None:
                recorder.record_state_error(name, str(error))
            raise
        if recorder is not None:
            recorder.record_evaluation_diagnostics(name, result)
            recorder.record_state_complete(name, "success", "", [contract.result_path])
        return set_jsonpath(
            contract.result_path, {"_state_iterations": iterations}, value
        )

    return node

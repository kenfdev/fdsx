"""Pure conversion of explicit output contracts to shared evaluation questions.

This module does not invoke providers or accept arbitrary JSON Schema. Runtime
integration must validate the projected value against the original schema.
"""

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import structlog
from pydantic import ValidationError

from fdsx.core.evaluation import EvaluationResult
from fdsx.models.evaluation import NAME, EvaluationQuestion

log = structlog.get_logger(__name__)
ANNOTATION = "x-fdsx-evaluation"


def prepare_evaluation_provider_schema(schema: Any) -> Any:
    """Express explicit evaluation annotations as ordinary schema descriptions.

    Only evaluation contracts opt in. Unannotated JSON Schema is left alone.
    The original document remains authoritative for output validation.
    """
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        return schema
    if not any(
        isinstance(prop, dict) and ANNOTATION in prop
        for prop in schema["properties"].values()
    ):
        return schema
    output = compile_evaluation_output(schema)
    prepared = deepcopy(schema)
    for name, prop in prepared["properties"].items():
        prop.pop(ANNOTATION, None)
        question, field = output.projections[name]
        definition = output.questions[question]
        guidance = ""
        if field == "noul":
            guidance = "Return the probability of Yes, from 0 to 1, not a boolean."
        elif field == "score":
            criteria = definition.criteria
            if isinstance(criteria, list):
                guidance = (
                    "Return the fractional expected score on these ordered levels: "
                    + "; ".join(
                        f"{index}: {criterion}"
                        for index, criterion in enumerate(criteria)
                    )
                )
        elif field in ("confidence", "probabilities"):
            guidance = (
                f"Return {field} for question {question}. These are self-reported "
                "estimates, not calibrated accuracy or Jev service metrics."
            )
        if guidance:
            prop["description"] = (
                prop.get("description", "") + "\n" + guidance
            ).strip()
    return prepared


class EvaluationSchemaError(ValueError):
    """Safe, location-bearing error in an evaluation output definition."""


def _reject(location: str, reason: str) -> EvaluationSchemaError:
    log.warning("evaluation_schema_invalid", location=location, reason=reason)
    return EvaluationSchemaError(f"{location}: {reason}")


def _keys(value: Any, allowed: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed:
        raise _reject(location, "unsupported schema keys or non-object definition")
    return value


@dataclass(frozen=True)
class EvaluationOutput:
    questions: dict[str, EvaluationQuestion]
    projections: dict[str, tuple[str, str]]

    def project(self, result: EvaluationResult) -> dict[str, Any]:
        """Project only a result already validated by the shared SDK boundary."""
        try:
            return {
                field: dict(result.answers[question])[attribute]
                for field, (question, attribute) in self.projections.items()
            }
        except KeyError:
            raise _reject(
                "structured_output", "validated answer is incomplete"
            ) from None


def compile_evaluation_output(
    schema: Any, *, location: str = "structured_output.schema"
) -> EvaluationOutput:
    """Compile required named scalar questions and explicitly requested metadata.

    Only descriptive root keywords, required properties, closed objects, Choice
    oneOf/const, and exact Noul/Score bounds are supported. Other constraints are
    rejected instead of being silently dropped.
    """
    root = _keys(
        schema,
        {
            "$schema",
            "$comment",
            "title",
            "description",
            "type",
            "properties",
            "required",
            "additionalProperties",
        },
        location,
    )
    properties = root.get("properties")
    required = root.get("required")
    if (
        root.get("type") != "object"
        or not isinstance(properties, dict)
        or not properties
        or any(not isinstance(k, str) or not re.fullmatch(NAME, k) for k in properties)
        or not isinstance(required, list)
        or any(not isinstance(k, str) for k in required)
        or len(required) != len(properties)
        or set(required) != set(properties)
        or root.get("additionalProperties") is not False
    ):
        raise _reject(
            location, "requires a closed object with all named properties required"
        )
    questions: dict[str, EvaluationQuestion] = {}
    projections: dict[str, tuple[str, str]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for name, raw in properties.items():
        place = f"{location}.properties.{name}"
        prop = _keys(
            raw,
            {
                "type",
                "description",
                "oneOf",
                "minimum",
                "maximum",
                ANNOTATION,
                "properties",
                "required",
                "additionalProperties",
            },
            place,
        )
        annotation = prop.get(ANNOTATION, {})
        if not isinstance(annotation, dict):
            raise _reject(place, "evaluation annotation must be an object")
        kind = annotation.get("kind", "choice")
        if not isinstance(kind, str):
            raise _reject(place, "evaluation kind must be a string")
        if kind == "metadata":
            _keys(annotation, {"kind", "question", "field"}, place)
            metadata[name] = prop
            continue
        instructions = prop.get("description")
        criteria: Any = None
        if kind == "choice":
            _keys(prop, {"type", "description", "oneOf"}, place)
            choices = prop.get("oneOf")
            if prop.get("type") != "string" or not isinstance(choices, list):
                raise _reject(
                    place, "choice requires described string const alternatives"
                )
            criteria = {}
            for option in choices:
                item = _keys(option, {"const", "description"}, place)
                key = item.get("const")
                if not isinstance(key, str) or key in criteria:
                    raise _reject(place, "choice candidates must be unique strings")
                criteria[key] = item.get("description")
        elif kind in {"noul", "score"}:
            _keys(
                prop, {"type", "description", "minimum", "maximum", ANNOTATION}, place
            )
            _keys(
                annotation, {"kind", "criteria"} if kind == "score" else {"kind"}, place
            )
            criteria = annotation.get("criteria")
            maximum = (
                len(criteria) - 1
                if kind == "score" and isinstance(criteria, list)
                else 1
            )
            if (
                prop.get("type") != "number"
                or type(prop.get("minimum")) not in (int, float)
                or prop["minimum"] != 0
                or type(prop.get("maximum")) not in (int, float)
                or prop["maximum"] != maximum
            ):
                raise _reject(place, "numeric bounds must match the evaluation range")
        else:
            raise _reject(place, "unsupported evaluation kind")
        try:
            questions[name] = EvaluationQuestion.model_validate(
                {"type": kind, "instructions": instructions, "criteria": criteria}
            )
        except ValidationError:
            raise _reject(
                place, "invalid question or missing candidate descriptions"
            ) from None
        projections[name] = (name, kind)
    if not questions:
        raise _reject(location, "at least one evaluation question is required")
    for name, prop in metadata.items():
        place = f"{location}.properties.{name}"
        annotation = prop[ANNOTATION]
        source, field = annotation.get("question"), annotation.get("field")
        if (
            not isinstance(source, str)
            or source not in questions
            or field not in ("confidence", "probabilities")
            or questions[source].type == "noul"
        ):
            raise _reject(place, "metadata must reference a Choice or Score metric")
        if field == "confidence":
            expected: dict[str, Any] = {"type": "number", "minimum": 0, "maximum": 1}
        else:
            rubric = questions[source].criteria
            names = (
                list(rubric)
                if isinstance(rubric, dict)
                else [str(i) for i in range(len(rubric or []))]
            )
            expected = {
                "type": "object",
                "properties": {
                    key: {"type": "number", "minimum": 0, "maximum": 1} for key in names
                },
                "required": names,
                "additionalProperties": False,
            }
        actual = {k: v for k, v in prop.items() if k not in (ANNOTATION, "description")}
        if isinstance(actual.get("required"), list):
            if "required" not in expected or any(
                not isinstance(key, str) for key in actual["required"]
            ):
                raise _reject(place, "invalid metadata required fields")
            actual = dict(actual, required=sorted(actual["required"]))
            expected = dict(expected, required=sorted(expected["required"]))
        if actual != expected:
            raise _reject(place, "metadata schema must match its metric exactly")
        projections[name] = (source, field)
    return EvaluationOutput(questions, projections)

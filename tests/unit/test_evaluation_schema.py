"""Explicit question conversion and projection, without SDK or provider calls."""

from copy import deepcopy

import pytest

from fdsx.core.evaluation import EvaluationResult
from fdsx.core.evaluation_schema import (
    EvaluationSchemaError,
    compile_evaluation_output,
)


def contract():
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Choose the next action from the review.",
                "oneOf": [
                    {"const": "fix", "description": "A confirmed defect needs repair."},
                    {"const": "proceed", "description": "No blocking issue was found."},
                ],
            },
            "risk": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "Is there a blocking risk? Return the Yes probability.",
                "x-fdsx-evaluation": {"kind": "noul"},
            },
            "quality": {
                "type": "number",
                "minimum": 0,
                "maximum": 2,
                "description": "How complete is the implementation?",
                "x-fdsx-evaluation": {
                    "kind": "score",
                    "criteria": ["Missing", "Partial", "Complete"],
                },
            },
        },
        "required": ["action", "risk", "quality"],
        "additionalProperties": False,
    }


def result():
    return EvaluationResult(
        "requested",
        "reported",
        {
            "action": {
                "type": "choice",
                "choice": "fix",
                "probabilities": {"fix": 0.7, "proceed": 0.3},
                "confidence": 0.01,
            },
            "risk": {"type": "noul", "noul": 0.25},
            "quality": {
                "type": "score",
                "score": 1.4,
                "legend": {"0": "Missing", "1": "Partial", "2": "Complete"},
                "probabilities": {"0": 0.1, "1": 0.4, "2": 0.5},
                "confidence": 0.2,
            },
        },
    )


def test_independent_questions_keep_explicit_meanings_and_project_only_values():
    schema = contract()
    original = deepcopy(schema)
    compiled = compile_evaluation_output(schema)
    assert compiled.questions["action"].criteria == {
        "fix": "A confirmed defect needs repair.",
        "proceed": "No blocking issue was found.",
    }
    assert compiled.questions["quality"].criteria == ["Missing", "Partial", "Complete"]
    assert compiled.project(result()) == {"action": "fix", "risk": 0.25, "quality": 1.4}
    assert schema == original


@pytest.mark.parametrize("question", ["action", "quality"])
@pytest.mark.parametrize("metric", ["confidence", "probabilities"])
def test_requested_metrics_are_projected_from_the_named_question(question, metric):
    schema = contract()
    metric_schema = {"type": "number", "minimum": 0, "maximum": 1}
    if metric == "probabilities":
        keys = ["fix", "proceed"] if question == "action" else ["0", "1", "2"]
        metric_schema = {
            "type": "object",
            "properties": {
                key: {"type": "number", "minimum": 0, "maximum": 1} for key in keys
            },
            "required": list(reversed(keys)),
            "additionalProperties": False,
        }
    metric_schema["x-fdsx-evaluation"] = {
        "kind": "metadata",
        "question": question,
        "field": metric,
    }
    schema["properties"]["detail"] = metric_schema
    schema["required"].append("detail")
    projected = compile_evaluation_output(schema).project(result())
    assert projected["detail"] == result().answers[question][metric]
    assert set(projected) == {"action", "risk", "quality", "detail"}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.update(allOf=[]),
        lambda s: s.update(additionalProperties=True),
        lambda s: s.update(required=["action"]),
        lambda s: s["properties"]["action"].update(pattern="secret-pattern"),
        lambda s: s["properties"]["action"]["oneOf"][1].update(const="fix"),
        lambda s: s["properties"]["action"]["oneOf"][0].pop("description"),
        lambda s: s["properties"]["risk"].update(maximum=True),
        lambda s: s["properties"]["risk"].update(maximum=2),
        lambda s: s["properties"]["risk"]["x-fdsx-evaluation"].update(kind=[]),
        lambda s: s["properties"]["quality"]["x-fdsx-evaluation"].update(
            criteria=["One"]
        ),
        lambda s: s["properties"]["quality"]["x-fdsx-evaluation"].update(
            unknown="secret-value"
        ),
    ],
)
def test_unsupported_or_ambiguous_contract_fails_with_safe_location(mutation):
    schema = contract()
    mutation(schema)
    with pytest.raises(EvaluationSchemaError, match=r"assess\.schema") as error:
        compile_evaluation_output(schema, location="assess.schema")
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "source,field",
    [
        ("missing", "confidence"),
        ("risk", "confidence"),
        ("action", "raw"),
        ([], "confidence"),
    ],
)
def test_invalid_metadata_reference_is_rejected(source, field):
    schema = contract()
    schema["properties"]["detail"] = {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "x-fdsx-evaluation": {"kind": "metadata", "question": source, "field": field},
    }
    schema["required"].append("detail")
    with pytest.raises(EvaluationSchemaError, match="metadata"):
        compile_evaluation_output(schema)


def test_projection_rejects_incomplete_result_without_exposing_answer():
    invalid = result()
    del invalid.answers["risk"]
    with pytest.raises(EvaluationSchemaError, match="incomplete"):
        compile_evaluation_output(contract()).project(invalid)

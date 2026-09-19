"""Nontrivial input and response validation at the shared evaluation boundary."""

import json
from unittest.mock import patch

import pytest
from typesafe_sdk import ChoiceAnswer, NoulAnswer, ScoreAnswer, SystemOneResponse, Usage

from fdsx.core.evaluation import EvaluationError, encode_materials, evaluate
from fdsx.models.evaluation import EvaluationQuestion, Material


@pytest.mark.parametrize(
    "path",
    [
        "$.document",
        "$.items[0].text",
        '$.items[2]["a.b"]',
        "$.object['hyphen-key']",
    ],
)
def test_concrete_references_roundtrip(path):
    material = Material(ref=path)
    assert Material.model_validate(material.model_dump()).ref == path


@pytest.mark.parametrize(
    "value",
    [
        b"bytes",
        object(),
        {1, 2},
        float("nan"),
        {"x": float("-inf")},
        {"x": {1: "value"}},
    ],
)
def test_non_json_materials_fail_without_values(value):
    with pytest.raises(EvaluationError, match=r"assess.input.document") as error:
        encode_materials({"document": value}, "assess")
    assert repr(value) not in str(error.value)


def test_cyclic_material_fails_at_named_location():
    value = []
    value.append(value)
    with pytest.raises(EvaluationError, match=r"assess.input.document: cyclic"):
        encode_materials({"document": value}, "assess")


def test_nested_null_and_literal_templates_are_not_interpreted():
    materials = {
        "doc": {"nested": [None, "", False, 0], "ref": "$.other", "text": "{other}"}
    }
    assert json.loads(encode_materials(materials, "assess")) == materials


@pytest.mark.parametrize("number", [True, float("nan"), float("inf"), -0.01, 1.01])
def test_typed_sdk_noul_rejects_invalid_numbers(number):
    question = EvaluationQuestion(type="noul", instructions="Question")
    response = SystemOneResponse(
        model="reported", usage=Usage(), answers={"q": NoulAnswer(noul=number)}
    )
    with patch("typesafe_sdk.TypeSafeClient") as client:
        client.return_value.__enter__.return_value.system_one.return_value = response
        with pytest.raises(EvaluationError, match="invalid numeric answer"):
            evaluate({"doc": "text"}, {"q": question}, location="assess")


@pytest.mark.parametrize("delta", [0.0000005, 0.000002, -0.1, 0.3])
def test_distribution_preserves_values_without_requiring_sum_of_one(delta):
    question = EvaluationQuestion(
        type="choice", instructions="Question", criteria={"a": "A", "b": "B"}
    )
    probabilities = {"a": 0.4, "b": 0.6 + delta}
    response = SystemOneResponse(
        model="reported",
        usage=Usage(),
        answers={
            "q": ChoiceAnswer(choice="b", probabilities=probabilities, confidence=0.01),
        },
    )
    with patch("typesafe_sdk.TypeSafeClient") as client:
        client.return_value.__enter__.return_value.system_one.return_value = response
        result = evaluate({"doc": "text"}, {"q": question}, location="assess")
        assert result.to_dict()["answers"]["q"]["probabilities"] == probabilities


@pytest.mark.parametrize("delta,valid", [(0.0000015, True), (0.000003, False)])
def test_score_tolerance_scales_with_number_of_levels(delta, valid):
    question = EvaluationQuestion(
        type="score", instructions="Question", criteria=["A", "B", "C"]
    )
    response = SystemOneResponse(
        model="reported",
        usage=Usage(),
        answers={
            "q": ScoreAnswer(
                score=1.6 + delta,
                legend={0: "A", 1: "B", 2: "C"},
                probabilities={0: 0.1, 1: 0.2, 2: 0.7},
                confidence=0.1,
            ),
        },
    )
    with patch("typesafe_sdk.TypeSafeClient") as client:
        client.return_value.__enter__.return_value.system_one.return_value = response
        if valid:
            result = evaluate({"doc": "text"}, {"q": question}, location="assess")
            assert result.to_dict()["answers"]["q"]["score"] == 1.6 + delta
        else:
            with pytest.raises(EvaluationError, match="distribution"):
                evaluate({"doc": "text"}, {"q": question}, location="assess")


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
def test_json_string_can_be_transported_as_utf8(surrogate):
    # Escape lone surrogates, but keep valid Unicode in both keys and values.
    materials = {"資料" + surrogate: {"doc": "日本語 😀" + surrogate}}
    encoded = encode_materials(materials, "assess")
    assert json.loads(encoded.encode("utf-8")) == materials
    assert "資料" in encoded
    assert "日本語 😀" in encoded

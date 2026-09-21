"""Per-material empty-value policy through the engine and offline SDK transport."""

import json
from unittest.mock import Mock

import httpx2
import pytest
import yaml

from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.engine.errors import FlowExecutionError
from fdsx.core.engine.validate import FlowValidationError
from fdsx.models.flow import Flow
from fdsx.providers.base import ProviderResult


@pytest.fixture
def wire(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test-key")
    requests = []
    response = {
        "model": "offline",
        "usage": {},
        "answers": {
            "answer": {
                "type": "choice",
                "choice": "proceed",
                "probabilities": {"proceed": 0.9, "repair": 0.1},
                "confidence": 0.9,
            }
        },
    }

    def request(client, method, url, **kwargs):
        requests.append(json.loads(kwargs["content"]))
        return httpx2.Response(200, json=response, request=httpx2.Request(method, url))

    monkeypatch.setattr(httpx2.Client, "request", request)
    fallback = Mock(
        return_value=ProviderResult(
            0, '{"answer":"repair","reason":"Check evidence"}', ""
        )
    )
    monkeypatch.setattr("fdsx.providers.claude._run_subprocess", fallback)
    return requests, response, fallback


def definition(kind, material):
    question = {
        "type": "choice",
        "instructions": "Choose from the supplied findings.",
        "criteria": {"proceed": "No repair needed", "repair": "Repair needed"},
    }
    state = {
        "type": kind,
        "input": {"findings": material},
        "result_path": "$.judgment",
        "end": True,
    }
    state["questions" if kind == "evaluate" else "question"] = (
        {"answer": question} if kind == "evaluate" else question
    )
    if kind == "evaluate":
        state["evaluator"] = "jev"
    return {
        "name": "empty-material-policy",
        "description": "Offline material validation",
        "start_at": "assess",
        "states": {"assess": state},
    }


def run(tmp_path, data, inputs=None, **kwargs):
    path = tmp_path / "flow.yaml"
    path.write_text(yaml.safe_dump(data))
    return run_flow(path, inputs=inputs, base_dir=tmp_path / ".fdsx", **kwargs)


@pytest.mark.parametrize("kind", ["evaluate", "classifier"])
def test_default_empty_findings_reach_service_unchanged(tmp_path, wire, kind):
    result = run(
        tmp_path,
        definition(kind, {"ref": "$.findings"}),
        {"findings": []},
    )
    assert result.status == "completed"
    assert json.loads(wire[0][0]["state"]) == {"findings": []}


@pytest.mark.parametrize("kind", ["evaluate", "classifier"])
@pytest.mark.parametrize("source", ["literal", "ref"])
@pytest.mark.parametrize("value", [[], {}, "", "  "])
@pytest.mark.parametrize("policy", [{}, {"allow_empty": True}])
def test_empty_policy_survives_workflow_serialization(
    tmp_path, wire, kind, source, value, policy
):
    material = {source: "$.findings" if source == "ref" else value, **policy}
    flow = Flow.model_validate(definition(kind, material))
    result = run(
        tmp_path, flow.model_dump(mode="json", exclude_none=True), {"findings": value}
    )
    assert result.status == "completed"
    assert json.loads(wire[0][0]["state"]) == {"findings": value}


@pytest.mark.parametrize("kind", ["evaluate", "classifier"])
@pytest.mark.parametrize("source", ["literal", "ref"])
@pytest.mark.parametrize("value", [[], {}, "", "  "])
def test_explicit_strict_policy_survives_serialization(
    tmp_path, wire, kind, source, value
):
    material = {
        source: "$.findings" if source == "ref" else value,
        "allow_empty": False,
    }
    flow = Flow.model_validate(definition(kind, material))
    with pytest.raises(FlowExecutionError, match="required material is empty"):
        run(
            tmp_path,
            flow.model_dump(mode="json", exclude_none=True),
            {"findings": value},
        )
    assert wire[0] == []
    wire[2].assert_not_called()


@pytest.mark.parametrize("kind", ["evaluate", "classifier"])
@pytest.mark.parametrize("inputs", [{}, {"findings": None}])
@pytest.mark.parametrize("policy", [{}, {"allow_empty": True}, {"allow_empty": False}])
def test_missing_or_null_references_fail_with_any_policy(
    tmp_path, wire, kind, inputs, policy
):
    data = definition(kind, {"ref": "$.findings", **policy})
    # A parent object exists, but the required child may be absent at runtime.
    data["states"]["assess"]["input"]["findings"]["ref"] = "$.report.findings"
    with pytest.raises(FlowExecutionError):
        run(tmp_path, data, {"report": inputs})
    assert wire[0] == []
    wire[2].assert_not_called()


@pytest.mark.parametrize("kind", ["evaluate", "classifier"])
@pytest.mark.parametrize("policy", [{}, {"allow_empty": True}, {"allow_empty": False}])
def test_literal_null_fails_with_any_policy(tmp_path, wire, kind, policy):
    with pytest.raises(FlowExecutionError, match="required material is empty"):
        run(tmp_path, definition(kind, {"literal": None, **policy}))
    assert wire[0] == []


@pytest.mark.parametrize("kind", ["evaluate", "classifier"])
def test_strict_override_is_local_to_one_material(tmp_path, wire, kind):
    data = definition(kind, {"literal": []})
    data["states"]["assess"]["input"]["requirements"] = {
        "literal": [],
        "allow_empty": False,
    }
    with pytest.raises(FlowExecutionError, match=r"input\.requirements"):
        run(tmp_path, data)
    assert wire[0] == []


@pytest.mark.parametrize("kind", ["evaluate", "classifier"])
@pytest.mark.parametrize(
    "value", [[float("nan")], {"nested": float("inf")}, {1: "invalid key"}]
)
def test_allow_empty_keeps_json_validation(tmp_path, wire, kind, value):
    with pytest.raises(FlowExecutionError, match=r"input\.findings"):
        run(tmp_path, definition(kind, {"literal": value, "allow_empty": True}))
    assert wire[0] == []


@pytest.mark.parametrize("kind", ["evaluate", "classifier"])
@pytest.mark.parametrize("value", [0, False, {"items": [], "unknown": None}])
def test_allow_empty_preserves_valid_falsy_and_nested_values(
    tmp_path, wire, kind, value
):
    run(tmp_path, definition(kind, {"literal": value, "allow_empty": True}))
    assert json.loads(wire[0][0]["state"]) == {"findings": value}


@pytest.mark.parametrize("kind", ["evaluate", "classifier"])
@pytest.mark.parametrize("policy", ["true", 1, None])
def test_empty_policy_requires_boolean_before_execution(tmp_path, wire, kind, policy):
    with pytest.raises(FlowValidationError, match="allow_empty"):
        run(tmp_path, definition(kind, {"literal": [], "allow_empty": policy}))
    assert wire[0] == []


def test_fallback_receives_identical_typed_materials_without_policy(tmp_path, wire):
    data = definition("classifier", {"literal": []})
    data["states"]["assess"].update(
        acceptance={"confidence": 1.0},
        fallback={"provider": "claude", "model": "test-model"},
        record_full_input=True,
    )
    result = run(tmp_path, data)
    assert result.results["judgment"]["source"] == "llm"
    snapshot = json.loads(
        next((tmp_path / ".fdsx/runs").glob("*/classifier-inputs/*.json")).read_text()
    )
    fallback_request = json.loads(snapshot["llm_prompt"].split("\n", 1)[1])
    assert (
        fallback_request["input"] == json.loads(wire[0][0]["state"]) == {"findings": []}
    )
    assert "allow_empty" not in snapshot["llm_prompt"]
    wire[2].assert_called_once()


def test_parallel_classifier_accepts_empty_material_by_default(tmp_path, wire):
    data = definition("classifier", {"literal": []})
    branch = data["states"]["assess"]
    branch.pop("end")
    data["states"]["assess"] = {
        "type": "parallel",
        "branches": [branch],
        "result_path": "$.reviews",
        "end": True,
    }
    result = run(tmp_path, data)
    assert result.results["reviews"][0]["judgment"]["answer"] == "proceed"
    assert json.loads(wire[0][0]["state"]) == {"findings": []}


def test_pending_classifier_resume_preserves_empty_policy(tmp_path, wire):
    data = definition("classifier", {"ref": "$.findings"})
    data["states"]["seed"] = {
        "type": "pass",
        "parameters": {"$.findings": []},
        "next": "assess",
    }
    data["start_at"] = "seed"
    data["states"]["assess"].update(
        acceptance={"confidence": 1.0},
        fallback={"provider": "claude", "model": "test-model"},
    )
    wire[2].return_value = ProviderResult(1, "", "offline failure")
    with pytest.raises(FlowExecutionError):
        run(tmp_path, data, thread_id="resume-empty")
    wire[2].return_value = ProviderResult(
        0, '{"answer":"repair","reason":"Check evidence"}', ""
    )
    result = resume_flow("resume-empty", base_dir=tmp_path / ".fdsx")
    assert result.results["judgment"]["source"] == "llm"
    assert [json.loads(request["state"]) for request in wire[0]] == [
        {"findings": []},
        {"findings": []},
    ]
    record = json.loads((tmp_path / ".fdsx/runs/resume-empty/run.json").read_text())
    assert [state["name"] for state in record["states"]].count("seed") == 1

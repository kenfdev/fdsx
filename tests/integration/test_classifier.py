"""Classifier behavior through the engine and real SDK with offline transport."""

import json
from contextlib import suppress
from unittest.mock import Mock

import httpx2
import pytest
import yaml

from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.engine.errors import FlowExecutionError
from fdsx.core.engine.validate import FlowValidationError
from fdsx.providers.base import ProviderResult


def classifier(**changes):
    return {
        "type": "classifier",
        "input": {"review": {"literal": "PRIVATE_MATERIAL"}},
        "question": {
            "type": "choice",
            "instructions": "PRIVATE_RULE: choose the next action",
            "criteria": {"commit": "ready", "fix": "needs work"},
        },
        "result_path": "$.decision",
        **changes,
    }


def definition(**changes):
    return {
        "name": "classifier-test",
        "description": "Classify review offline",
        "start_at": "classify",
        "states": {
            "classify": classifier(next="route", **changes),
            "route": {
                "type": "choice",
                "choices": [
                    {
                        "variable": "$.decision.answer",
                        "operator": "equals",
                        "value": "commit",
                        "next": "commit",
                    }
                ],
                "default": "fix",
            },
            "commit": {
                "type": "pass",
                "parameters": {"$.route": "commit"},
                "end": True,
            },
            "fix": {"type": "pass", "parameters": {"$.route": "fix"}, "end": True},
        },
    }


FALLBACK = {"provider": "claude", "model": "test-model"}


@pytest.fixture
def wire(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    requests = []
    payload = {
        "model": "reported",
        "usage": {},
        "answers": {
            "answer": {
                "type": "choice",
                "choice": "commit",
                "probabilities": {"commit": 0.75, "fix": 0.25},
                "confidence": 0.8,
            }
        },
    }

    def request(client, method, url, **kwargs):
        requests.append(json.loads(kwargs["content"]))
        return httpx2.Response(200, json=payload, request=httpx2.Request(method, url))

    monkeypatch.setattr(httpx2.Client, "request", request)
    llm = Mock(
        return_value=ProviderResult(0, '{"answer":"fix","reason":"Needs tests"}', "")
    )
    monkeypatch.setattr("fdsx.providers.claude._run_subprocess", llm)
    return requests, payload, llm


def run(tmp_path, data):
    path = tmp_path / "flow.yaml"
    path.write_text(yaml.safe_dump(data))
    return run_flow(path, base_dir=tmp_path / ".fdsx")


@pytest.mark.parametrize(
    "acceptance,source",
    [
        ({}, "jev"),
        ({"probability": 0.75}, "jev"),
        ({"probability": 0.75001}, "llm"),
        ({"probability": 0.74999}, "jev"),
        ({"confidence": 0.8}, "jev"),
        ({"confidence": 0.80001}, "llm"),
        ({"confidence": 0.79999}, "jev"),
        ({"probability": 0.8, "confidence": 0.7}, "llm"),
        ({"probability": 0.8, "confidence": 0.7, "mode": "any"}, "jev"),
        ({"probability": 0.8, "confidence": 0.9, "mode": "any"}, "llm"),
        ({"probability": 0.6, "probability_by_choice": {"commit": 0.8}}, "llm"),
        ({"probability": 0.8, "probability_by_choice": {"commit": 0.7}}, "jev"),
        ({"probability_by_choice": {"fix": 0.1}}, "jev"),
        ({"probability_by_choice": {"fix": 0.1}, "mode": "any"}, "jev"),
        ({"probability_by_choice": {"fix": 0.1}, "confidence": 0.9}, "llm"),
        (
            {"probability_by_choice": {"fix": 0.1}, "confidence": 0.9, "mode": "any"},
            "llm",
        ),
        ({"probability": 0}, "jev"),
        ({"probability": 1}, "llm"),
    ],
)
def test_acceptance_routes_common_result(tmp_path, wire, acceptance, source):
    requests, _, llm = wire
    result = run(tmp_path, definition(acceptance=acceptance, fallback=FALLBACK))
    decision = result.results["decision"]
    assert decision["source"] == source
    assert result.results["route"] == decision["answer"]
    assert decision["reason"] == (None if source == "jev" else "Needs tests")
    assert decision["jev"]["answer"] == "commit"
    assert decision["jev"]["probabilities"]["commit"] == 0.75
    assert len(requests) == 1
    assert llm.call_count == (source == "llm")


@pytest.mark.parametrize(
    "changes",
    [
        {"acceptance": {"confidence": 0.5}},
        {"acceptance": {"probability": -0.1}, "fallback": FALLBACK},
        {"acceptance": {"confidence": float("inf")}, "fallback": FALLBACK},
        {"acceptance": {"confidence": float("nan")}, "fallback": FALLBACK},
        {"acceptance": {"confidence": True}, "fallback": FALLBACK},
        {
            "acceptance": {"probability_by_choice": {"unknown": 0.5}},
            "fallback": FALLBACK,
        },
        {"acceptance": {"mode": "either"}},
        {"question": {"type": "noul", "instructions": "Choose"}},
        {"fallback": {"provider": "system", "model": "test"}},
    ],
)
def test_invalid_definition_fails_before_execution(tmp_path, wire, changes):
    requests, _, llm = wire
    data = definition(**changes)
    data["start_at"] = "before"
    data["states"]["before"] = {
        "type": "task",
        "provider": "system",
        "command": "echo before > marker",
        "next": "classify",
    }
    with pytest.raises(FlowValidationError):
        run(tmp_path, data)
    assert not requests and not llm.called
    assert not (tmp_path / "marker").exists()


@pytest.mark.parametrize("include", [False, True])
def test_generated_prompt_and_profile(tmp_path, wire, include):
    requests, _, llm = wire
    data = definition(
        acceptance={"probability": 0.9},
        fallback={"profile": "judge", "include_jev_result": include},
    )
    data["profiles"] = {"judge": FALLBACK}
    run(tmp_path, data)
    call = str(llm.call_args)
    assert "test-model" in call
    assert "PRIVATE_MATERIAL" in call and "PRIVATE_RULE" in call
    assert ("jev_result" in call) == include
    assert json.loads(requests[0]["state"])["review"] == "PRIVATE_MATERIAL"


@pytest.mark.parametrize(
    "body",
    [
        "{}",
        '{"answer":"unknown","reason":"x"}',
        '{"answer":"fix","reason":" "}',
        '{"answer":"fix","reason":1}',
        "not json",
        json.dumps({"answer": "fix", "reason": "x" * 501}),
    ],
)
def test_llm_invalid_answer_is_not_retried(tmp_path, wire, body):
    requests, _, llm = wire
    llm.return_value = ProviderResult(0, body, "")
    with pytest.raises(FlowExecutionError):
        run(tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    assert len(requests) == 1 and llm.call_count == 1


def test_invalid_jev_preserves_only_unvalidated_numbers(tmp_path, wire, caplog):
    requests, payload, llm = wire
    payload["answers"]["answer"]["probabilities"]["commit"] = 1.5
    payload["answers"]["answer"]["secret"] = "PRIVATE_RESPONSE"
    with pytest.raises(FlowExecutionError):
        run(tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    assert len(requests) == 1 and not llm.called
    record = json.loads(next((tmp_path / ".fdsx/runs").glob("*/run.json")).read_text())
    event = next(e for e in record["classifier_events"] if e["event"] == "invalid_jev")
    assert event["diagnostic"]["validated"] is False
    assert event["diagnostic"]["questions"]["answer"]["probabilities"]["commit"] == {
        "value": 1.5,
        "valid_range": False,
    }
    assert "PRIVATE_RESPONSE" not in json.dumps(record) + caplog.text


def test_parallel_mixed_branches_and_gate(tmp_path, wire):
    data = definition()
    data["states"] = {
        "classify": {
            "type": "parallel",
            "branches": [
                classifier(name="required"),
                classifier(
                    name="advice", acceptance={"probability": 0.9}, fallback=FALLBACK
                ),
                {"name": "task", "provider": "system", "command": "echo ok"},
            ],
            "result_path": "$.reviews",
            "gate": {
                "required": ["required"],
                "field": "$.decision.answer",
                "expected": "commit",
                "result_path": "$.approved",
            },
            "end": True,
        }
    }
    result = run(tmp_path, data)
    assert result.results["approved"] is True
    assert [r["exit_code"] for r in result.results["reviews"]] == [0, 0, 0]
    assert [r["decision"]["source"] for r in result.results["reviews"][:2]] == [
        "jev",
        "llm",
    ]


def test_resume_restarts_jev_after_llm_failure(tmp_path, wire):
    requests, _, llm = wire
    llm.return_value = ProviderResult(1, "PRIVATE_RESPONSE", "PRIVATE_ERROR")
    with pytest.raises(FlowExecutionError):
        run(tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    thread = next((tmp_path / ".fdsx/runs").iterdir()).name
    llm.return_value = ProviderResult(0, '{"answer":"fix","reason":"Tests"}', "")
    result = resume_flow(thread, base_dir=tmp_path / ".fdsx")
    assert result.results["decision"]["source"] == "llm"
    assert len(requests) == 2 and llm.call_count == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_full_input_record_is_opt_in_and_private(tmp_path, wire, enabled):
    run(
        tmp_path,
        definition(
            acceptance={"probability": 0.9},
            fallback=FALLBACK,
            record_full_input=enabled,
        ),
    )
    files = list((tmp_path / ".fdsx/runs").glob("*/classifier-inputs/*.json"))
    assert len(files) == int(enabled)
    if enabled:
        data = json.loads(files[0].read_text())
        assert "PRIVATE_MATERIAL" in data["llm_prompt"]
        assert files[0].stat().st_mode & 0o777 == 0o600
        assert files[0].parent.stat().st_mode & 0o777 == 0o700


def test_tied_maximum_keeps_jev_choice_without_fallback(tmp_path, wire):
    _, payload, llm = wire
    payload["answers"]["answer"]["probabilities"] = {"commit": 0.5, "fix": 0.5}
    result = run(tmp_path, definition())
    assert result.results["decision"]["answer"] == "commit"
    assert not llm.called


@pytest.mark.parametrize("quiet", [False, True])
def test_cli_display_and_normal_logs_respect_quiet(tmp_path, wire, quiet):
    from typer.testing import CliRunner

    from fdsx.cli.main import app

    path = tmp_path / "flow.yaml"
    path.write_text(
        yaml.safe_dump(definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    )
    (tmp_path / ".fdsx").mkdir()
    result = CliRunner().invoke(
        app, ["run", str(path), *(["--quiet"] if quiet else [])]
    )
    assert result.exit_code == 0, result.output
    assert ("Needs tests" in result.stderr) is (not quiet)
    assert ('"event": "fallback"' in result.stderr) is (not quiet)
    assert "Needs tests" not in result.stdout
    assert (
        "PRIVATE_MATERIAL" not in result.output and "PRIVATE_RULE" not in result.output
    )
    logs = "".join(p.read_text() for p in (tmp_path / ".fdsx/runs").glob("*/logs/*"))
    assert "Needs tests" in logs and '"kind": "fallback"' in logs
    assert "PRIVATE_MATERIAL" not in logs and "PRIVATE_RULE" not in logs


@pytest.mark.parametrize(
    "required,min_success,completed",
    [(False, 1, True), (False, 2, False), (True, None, False), (False, None, True)],
)
def test_parallel_failure_uses_existing_aggregation(
    tmp_path, wire, required, min_success, completed
):
    _, _, llm = wire
    llm.return_value = ProviderResult(1, "PRIVATE_RESPONSE", "PRIVATE_ERROR")
    parallel = {
        "type": "parallel",
        "branches": [
            classifier(name="good"),
            classifier(name="bad", acceptance={"probability": 0.9}, fallback=FALLBACK),
        ],
        "result_path": "$.reviews",
        "end": True,
    }
    if min_success is not None:
        parallel["min_success"] = min_success
    else:
        parallel["gate"] = {
            "required": ["bad" if required else "good"],
            "field": "$.decision.answer",
            "expected": "commit",
            "result_path": "$.approved",
        }
    data = definition()
    data["states"] = {"classify": parallel}
    if completed:
        result = run(tmp_path, data)
        assert result.results["reviews"][1]["exit_code"] == 1
        assert "decision" not in result.results["reviews"][1]
    else:
        with pytest.raises(FlowExecutionError):
            run(tmp_path, data)
    assert llm.call_count == 1


@pytest.mark.parametrize("status,expected_requests", [(401, 1), (429, 3), (500, 3)])
def test_sdk_owns_transport_retries_and_never_calls_llm(
    tmp_path, wire, monkeypatch, status, expected_requests
):
    _, _, llm = wire
    requests = []

    def request(client, method, url, **kwargs):
        requests.append(url)
        return httpx2.Response(
            status,
            json={"detail": "PRIVATE_RESPONSE"},
            request=httpx2.Request(method, url),
        )

    monkeypatch.setattr(httpx2.Client, "request", request)
    monkeypatch.setattr("time.sleep", lambda _: None)
    with pytest.raises(FlowExecutionError):
        run(tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    assert len(requests) == expected_requests and not llm.called


@pytest.mark.parametrize("kind", ["exit", "timeout", "transport"])
def test_llm_failure_never_retries_or_leaks_response(tmp_path, wire, kind):
    import subprocess

    requests, _, llm = wire
    llm.return_value = ProviderResult(1, "PRIVATE_RESPONSE", "PRIVATE_ERROR")
    if kind == "timeout":
        llm.side_effect = subprocess.TimeoutExpired("PRIVATE_COMMAND", 1)
    if kind == "transport":
        llm.side_effect = OSError("PRIVATE_ERROR")
    with pytest.raises(FlowExecutionError) as error:
        run(tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    assert "PRIVATE" not in str(error.value)
    assert len(requests) == 1 and llm.call_count == 1
    logs = "".join(p.read_text() for p in (tmp_path / ".fdsx/runs").glob("*/logs/*"))
    assert "PRIVATE" not in logs


@pytest.mark.parametrize(
    "reason,expected",
    [
        (" x ", "x"),
        ("x" * 500, "x" * 500),
        ("\u001b[31mReason\u001b[0m\nnext", "Reason next"),
    ],
)
def test_reason_normalization_and_boundaries(tmp_path, wire, reason, expected):
    _, _, llm = wire
    llm.return_value = ProviderResult(
        0, json.dumps({"answer": "fix", "reason": reason}), ""
    )
    result = run(
        tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK)
    )
    assert result.results["decision"]["reason"] == expected


def test_input_reference_and_actual_snapshot(tmp_path, wire):
    requests, _, _ = wire
    data = definition(input={"review": {"ref": "$.review"}}, record_full_input=True)
    data["start_at"] = "before"
    data["states"]["before"] = {
        "type": "pass",
        "parameters": {"$.review": "resolved"},
        "next": "classify",
    }
    run(tmp_path, data)
    assert json.loads(requests[0]["state"]) == {"review": "resolved"}
    record = json.loads(
        next((tmp_path / ".fdsx/runs").glob("*/classifier-inputs/*.json")).read_text()
    )
    assert record["jev_request"]["input"] == {"review": "resolved"}


def test_private_snapshot_limit_fails_before_jev(tmp_path, wire):
    requests, _, llm = wire
    with pytest.raises(FlowExecutionError, match="1MiB"):
        run(
            tmp_path,
            definition(
                input={"review": {"literal": "x" * (1024 * 1024)}},
                record_full_input=True,
            ),
        )
    assert not requests and not llm.called


def test_completed_classifier_checkpoint_is_reused(tmp_path, wire):
    requests, _, _ = wire
    data = definition()
    data["states"]["commit"] = {
        "type": "task",
        "provider": "system",
        "command": "exit 1",
        "retry": 0,
        "end": True,
    }
    with pytest.raises(FlowExecutionError):
        run(tmp_path, data)
    thread = next((tmp_path / ".fdsx/runs").iterdir()).name
    data["states"]["commit"]["command"] = "echo recovered"
    (tmp_path / "flow.yaml").write_text(yaml.safe_dump(data))
    resume_flow(thread, base_dir=tmp_path / ".fdsx")
    assert len(requests) == 1


def test_explicit_resume_from_classifier_reexecutes(tmp_path, wire):
    requests, _, _ = wire
    data = definition()
    data["states"]["commit"] = {"type": "fail", "error": "stop", "cause": "stop"}
    run(tmp_path, data)
    thread = next((tmp_path / ".fdsx/runs").iterdir()).name
    resume_flow(thread, base_dir=tmp_path / ".fdsx", from_state="classify")
    assert len(requests) == 2


def test_no_automatic_fallback_for_nonmaximum_jev_answer(tmp_path, wire):
    requests, payload, llm = wire
    payload["answers"]["answer"]["choice"] = "fix"
    with pytest.raises(FlowExecutionError):
        run(tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    assert len(requests) == 1 and not llm.called


def test_invalid_branch_declaration_cannot_become_legacy_task(tmp_path, wire):
    requests, _, llm = wire
    data = definition()
    data["states"] = {
        "classify": {
            "type": "parallel",
            "branches": [classifier(result_path="$.exit_code")],
            "result_path": "$.reviews",
            "end": True,
        }
    }
    with pytest.raises(FlowValidationError):
        run(tmp_path, data)
    assert not requests and not llm.called


def test_fallback_options_are_validated_before_jev(tmp_path, wire):
    requests, _, llm = wire
    with pytest.raises(FlowValidationError):
        run(
            tmp_path,
            definition(
                fallback={**FALLBACK, "provider_options": {"unsupported": True}}
            ),
        )
    assert not requests and not llm.called


@pytest.mark.parametrize(
    "provider", ["claude", "codex", "gemini", "opencode", "cursor", "grok", "pi"]
)
def test_all_llm_adapters_receive_schema_and_execute_once(
    tmp_path, wire, monkeypatch, provider
):
    calls = []
    body = '{"answer":"fix","reason":"Adapter result"}'

    def execute(**kwargs):
        calls.append(kwargs)
        if provider == "grok":
            kwargs["output_callback"](json.dumps({"type": "text", "data": body}))
            kwargs["output_callback"](json.dumps({"type": "end", "stopReason": "stop"}))
        return ProviderResult(0, body, "")

    monkeypatch.setattr(f"fdsx.providers.{provider}._run_subprocess", execute)
    if provider == "pi":
        monkeypatch.setattr("fdsx.providers.pi.shutil.which", lambda _: "/fake/pi")
    result = run(
        tmp_path,
        definition(
            acceptance={"probability": 0.9},
            fallback={
                "provider": provider,
                "model": "test-model",
                "timeout_seconds": 19,
                "inactivity_timeout": 7,
            },
        ),
    )
    assert result.results["decision"]["reason"] == "Adapter result"
    assert len(calls) == 1
    assert calls[0]["timeout"] == 19 and calls[0]["inactivity_timeout"] == 7
    arguments = str(calls[0]["args"]) + str(calls[0].get("stdin_data"))
    assert "test-model" in arguments
    if provider in {"claude", "grok"}:
        assert "--json-schema" in arguments
    elif provider == "codex":
        assert "--output-schema" in arguments
    else:
        assert "properties" in arguments and "reason" in arguments


def test_stream_parser_diagnostics_do_not_expose_private_response(
    tmp_path, wire, monkeypatch, caplog
):
    def execute(**kwargs):
        callback = kwargs["output_callback"]
        callback("PRIVATE_BROKEN_STREAM")
        callback(
            json.dumps(
                {
                    "type": "result",
                    "structured_output": {"answer": "fix", "reason": "safe"},
                }
            )
        )
        return ProviderResult(0, "PRIVATE_BROKEN_STREAM", "PRIVATE_STDERR")

    monkeypatch.setattr("fdsx.providers.claude._run_subprocess", execute)
    with pytest.raises(FlowExecutionError):
        run(tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    assert "PRIVATE_BROKEN_STREAM" not in caplog.text
    logs = "".join(p.read_text() for p in (tmp_path / ".fdsx/runs").glob("*/logs/*"))
    assert "PRIVATE" not in logs


def test_parallel_explicit_resume_reexecutes_classifier(tmp_path, wire):
    requests, _, llm = wire
    llm.return_value = ProviderResult(1, "", "failed")
    data = definition()
    data["states"] = {
        "classify": {
            "type": "parallel",
            "branches": [
                classifier(acceptance={"probability": 0.9}, fallback=FALLBACK)
            ],
            "result_path": "$.reviews",
            "end": True,
        }
    }
    with pytest.raises(FlowExecutionError):
        run(tmp_path, data)
    thread = next((tmp_path / ".fdsx/runs").iterdir()).name
    llm.return_value = ProviderResult(0, '{"answer":"fix","reason":"Recovered"}', "")
    result = resume_flow(thread, base_dir=tmp_path / ".fdsx", from_state="classify")
    assert result.results["reviews"][0]["decision"]["reason"] == "Recovered"
    assert len(requests) == 2 and llm.call_count == 2


def test_interruption_after_jev_restarts_whole_classifier(tmp_path, wire):
    requests, _, llm = wire
    llm.side_effect = KeyboardInterrupt()
    # The engine handles user interruption and retains the pending classifier.
    with suppress(KeyboardInterrupt):
        run(tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    thread = next((tmp_path / ".fdsx/runs").iterdir()).name
    llm.side_effect = None
    result = resume_flow(
        thread, base_dir=tmp_path / ".fdsx", flow_path=tmp_path / "flow.yaml"
    )
    assert result.results["decision"]["source"] == "llm"
    assert len(requests) == 2


@pytest.mark.parametrize(
    "damaged", ["unknown_key", "bool", "string", "nonfinite", "missing"]
)
def test_numeric_diagnostics_allowlist(tmp_path, wire, monkeypatch, damaged):
    _, payload, llm = wire
    payload["answers"]["answer"]["choice"] = "fix"  # Force invalid maximum.
    probabilities = payload["answers"]["answer"]["probabilities"]
    if damaged == "unknown_key":
        probabilities["PRIVATE_UNKNOWN"] = 0.2
    elif damaged == "bool":
        probabilities["commit"] = True
    elif damaged == "string":
        probabilities["commit"] = "PRIVATE_NUMBER"
    elif damaged == "nonfinite":
        probabilities["commit"] = float("nan")
    else:
        del payload["answers"]["answer"]["confidence"]

    def request(client, method, url, **kwargs):
        return httpx2.Response(
            200, content=json.dumps(payload), request=httpx2.Request(method, url)
        )

    monkeypatch.setattr(httpx2.Client, "request", request)
    with pytest.raises(FlowExecutionError):
        run(tmp_path, definition(acceptance={"probability": 0.9}, fallback=FALLBACK))
    assert not llm.called
    record = json.loads(next((tmp_path / ".fdsx/runs").glob("*/run.json")).read_text())
    diagnostic = next(
        e["diagnostic"]
        for e in record["classifier_events"]
        if e["event"] == "invalid_jev"
    )
    assert diagnostic["validated"] is False
    assert "PRIVATE" not in json.dumps(diagnostic)
    number = diagnostic["questions"]["answer"]["probabilities"]["commit"]
    if damaged in {"bool", "string"}:
        assert number == {"availability": "unavailable"}
    if damaged == "nonfinite":
        assert number == {"value": "nonfinite", "valid_range": False}


@pytest.mark.parametrize(
    "choice,probability,confidence,source",
    [
        ("commit", 0.75, 0.8, "llm"),
        ("fix", 0.65, 0.8, "jev"),
        ("commit", 0.85, 0.5, "llm"),
        ("commit", 0.85, 0.8, "jev"),
    ],
)
def test_agreed_threshold_examples(
    tmp_path, wire, choice, probability, confidence, source
):
    _, payload, _ = wire
    answer = payload["answers"]["answer"]
    answer.update(
        choice=choice,
        confidence=confidence,
        probabilities={
            choice: probability,
            ("fix" if choice == "commit" else "commit"): 1 - probability,
        },
    )
    result = run(
        tmp_path,
        definition(
            acceptance={
                "probability": 0.6,
                "probability_by_choice": {"commit": 0.8, "fix": 0.6},
                "confidence": 0.7,
            },
            fallback=FALLBACK,
        ),
    )
    assert result.results["decision"]["source"] == source


@pytest.mark.parametrize("parallel", [False, True])
def test_missing_key_precedes_tasks_and_hooks(tmp_path, wire, monkeypatch, parallel):
    requests, _, llm = wire
    monkeypatch.delenv("TYPESAFE_API_KEY")
    data = definition()
    if parallel:
        data["states"] = {
            "classify": {
                "type": "parallel",
                "branches": [classifier()],
                "result_path": "$.reviews",
                "end": True,
            }
        }
    data["start_at"] = "before"
    data["states"]["before"] = {
        "type": "task",
        "provider": "system",
        "command": "echo unsafe > marker",
        "next": "classify",
    }
    data["hooks"] = {"on_workflow_start": [{"command": "echo unsafe > hook-marker"}]}
    with pytest.raises(FlowValidationError):
        run(tmp_path, data)
    assert not requests and not llm.called
    assert (
        not (tmp_path / "marker").exists() and not (tmp_path / "hook-marker").exists()
    )


def test_config_profile_resolves_for_fallback(tmp_path, wire):
    (tmp_path / ".fdsx").mkdir()
    (tmp_path / ".fdsx/config.yaml").write_text(
        yaml.safe_dump({"profiles": {"judge": FALLBACK}})
    )
    result = run(
        tmp_path,
        definition(acceptance={"probability": 0.9}, fallback={"profile": "judge"}),
    )
    assert result.results["decision"]["source"] == "llm"


def test_classification_loop_evaluates_again(tmp_path, wire):
    requests, _, _ = wire
    data = definition()
    data["max_loop"] = 2
    data["states"]["commit"] = {"type": "pass", "next": "classify"}
    result = run(tmp_path, data)
    assert result.status == "max_loop_reached"
    assert len(requests) > 1


def test_full_input_write_failure_is_safe(tmp_path, wire, monkeypatch):
    from pathlib import Path

    original = Path.replace

    def replace(path, target):
        if path.parent.name == "classifier-inputs":
            raise OSError("PRIVATE_FILESYSTEM_ERROR")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    requests, _, _ = wire
    with pytest.raises(FlowExecutionError) as error:
        run(tmp_path, definition(record_full_input=True))
    assert "PRIVATE" not in str(error.value) and not requests
    assert not list((tmp_path / ".fdsx/runs").glob("*/classifier-inputs/*.tmp"))


def test_no_task_retry_escalation_or_extraction_recovery(tmp_path, wire, monkeypatch):
    other = Mock(side_effect=AssertionError("unexpected recovery provider"))
    monkeypatch.setattr("fdsx.providers.codex._run_subprocess", other)
    _, _, llm = wire
    llm.return_value = ProviderResult(0, "invalid JSON", "")
    data = definition(acceptance={"probability": 0.9}, fallback=FALLBACK)
    data["retry_escalation"] = {"provider": "codex", "model": "other"}
    data["extraction_fallback"] = {"provider": "codex", "model": "other"}
    with pytest.raises(FlowExecutionError):
        run(tmp_path, data)
    assert llm.call_count == 1 and not other.called


@pytest.mark.parametrize("name", ["review", "parallel"])
def test_documented_examples_execute_offline(tmp_path, wire, name):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    data = yaml.safe_load(
        (root / f"src/fdsx/examples/workflows/classifier/{name}.yaml").read_text()
    )
    if name == "parallel":
        answer = wire[1]["answers"]["answer"]
        answer.update(choice="ready", probabilities={"ready": 0.75, "fix": 0.25})
    result = run(tmp_path, data)
    assert result.status == "completed"
    assert (root / "docs/classifier.md").read_text() == (
        root / "src/fdsx/data/skills/fdsx/references/classifier.md"
    ).read_text()


def test_empty_final_message_does_not_recover_from_earlier_stdout(tmp_path, wire):
    _, _, llm = wire
    llm.return_value = ProviderResult(
        0, '{"answer":"fix","reason":"Earlier message"}', "", final_message=""
    )
    with pytest.raises(FlowExecutionError):
        run(
            tmp_path,
            definition(acceptance={"probability": 0.9}, fallback=FALLBACK),
        )
    assert llm.call_count == 1


def test_completed_parallel_classifier_can_resume_collector_without_key(
    tmp_path, wire, monkeypatch
):
    import importlib

    compiler = importlib.import_module("fdsx.core.compiler.compile")
    original = compiler._create_collector_node

    def interrupted_collector(*args, **kwargs):
        def node(state):
            raise RuntimeError("interrupted collector")

        return node

    monkeypatch.setattr(compiler, "_create_collector_node", interrupted_collector)
    data = definition()
    data["states"] = {
        "classify": {
            "type": "parallel",
            "branches": [classifier()],
            "result_path": "$.reviews",
            "end": True,
        }
    }
    with pytest.raises(FlowExecutionError):
        run(tmp_path, data)
    monkeypatch.setattr(compiler, "_create_collector_node", original)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    thread = next((tmp_path / ".fdsx/runs").iterdir()).name
    result = resume_flow(thread, base_dir=tmp_path / ".fdsx")
    assert result.results["reviews"][0]["decision"]["source"] == "jev"
    assert len(wire[0]) == 1


def test_missing_resolved_material_fails_without_request(tmp_path, wire):
    data = definition(input={"review": {"ref": "$.materials.review"}})
    data["start_at"] = "prepare"
    data["states"]["prepare"] = {
        "type": "pass",
        "parameters": {"$.materials": {"other": "PRIVATE"}},
        "next": "classify",
    }
    with pytest.raises(FlowExecutionError, match="material reference is missing"):
        run(tmp_path, data)
    assert not wire[0] and not wire[2].called


@pytest.mark.parametrize(
    "field,completed", [("$.decision.answer", True), ("$.decision.absent", False)]
)
def test_parallel_gate_mismatch_and_missing_field(tmp_path, wire, field, completed):
    data = definition()
    data["states"] = {
        "classify": {
            "type": "parallel",
            "branches": [classifier(name="required")],
            "result_path": "$.reviews",
            "gate": {
                "required": ["required"],
                "field": field,
                "expected": "fix",
                "result_path": "$.approved",
            },
            "end": True,
        }
    }
    if completed:
        assert run(tmp_path, data).results["approved"] is False
    else:
        with pytest.raises(FlowExecutionError, match="omitted gate field"):
            run(tmp_path, data)


def test_parallel_private_records_and_diagnostics_are_independent(tmp_path, wire):
    data = definition()
    data["states"] = {
        "classify": {
            "type": "parallel",
            "branches": [
                classifier(record_full_input=True),
                classifier(
                    record_full_input=True,
                    acceptance={"probability": 0.9},
                    fallback=FALLBACK,
                ),
            ],
            "result_path": "$.reviews",
            "end": True,
        }
    }
    run(tmp_path, data)
    records = [
        json.loads(path.read_text())
        for path in (tmp_path / ".fdsx/runs").glob("*/classifier-inputs/*.json")
    ]
    assert len(records) == 2
    assert len({record["attempt"] for record in records}) == 2
    assert {record["state"] for record in records} == {
        "classify.branches.0",
        "classify.branches.1",
    }
    run_record = json.loads(
        next((tmp_path / ".fdsx/runs").glob("*/run.json")).read_text()
    )
    events = run_record["classifier_events"]
    assert {event["attempt"] for event in events} == {
        record["attempt"] for record in records
    }
    assert {event["name"] for event in events if event["event"] == "reason"} == {
        "classify.branches.1"
    }


def test_malformed_fallback_profile_is_a_configuration_error(tmp_path, wire):
    with pytest.raises(FlowValidationError):
        run(tmp_path, definition(fallback={"profile": ["judge"]}))
    assert not wire[0] and not wire[2].called


@pytest.mark.parametrize("mode", ["all", "any"])
@pytest.mark.parametrize("confidence", [None, 0.7, 0.9])
def test_candidate_only_threshold_applies_to_first_candidate(
    tmp_path, wire, mode, confidence
):
    acceptance = {"probability_by_choice": {"commit": 0.8}, "mode": mode}
    if confidence is not None:
        acceptance["confidence"] = confidence
    expected = "jev" if mode == "any" and confidence == 0.7 else "llm"
    result = run(tmp_path, definition(acceptance=acceptance, fallback=FALLBACK))
    assert result.results["decision"]["source"] == expected
    assert wire[2].call_count == (expected == "llm")


def test_passing_runner_up_is_never_substituted(tmp_path, wire):
    wire[2].return_value = ProviderResult(
        0, '{"answer":"commit","reason":"Reassessed"}', ""
    )
    result = run(
        tmp_path,
        definition(
            acceptance={"probability_by_choice": {"commit": 0.8, "fix": 0.2}},
            fallback=FALLBACK,
        ),
    )
    assert result.results["decision"]["source"] == "llm"
    assert result.results["decision"]["answer"] == "commit"
    assert wire[2].call_count == 1


def test_codex_empty_stream_final_invalidates_previous_answer(
    tmp_path, wire, monkeypatch
):
    def execute(**kwargs):
        for text in ['{"answer":"commit","reason":"Earlier"}', ""]:
            kwargs["output_callback"](
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": text},
                    }
                )
            )
        return ProviderResult(0, "", "")

    mock = Mock(side_effect=execute)
    monkeypatch.setattr("fdsx.providers.codex._run_subprocess", mock)
    with pytest.raises(FlowExecutionError):
        run(
            tmp_path,
            definition(
                acceptance={"probability": 0.9},
                fallback={"provider": "codex", "model": "test-model"},
            ),
        )
    assert mock.call_count == 1


@pytest.mark.parametrize(
    "provider,malformed",
    [
        (provider, value)
        for provider in ("codex", "claude")
        for value in (None, [], "nested")
    ]
    + [
        (
            "codex",
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": value},
            },
        )
        for value in (None, 0, False, [], {})
    ],
)
@pytest.mark.parametrize("earlier", [False, True])
@pytest.mark.parametrize("threaded", [False, True])
def test_malformed_stream_invalidates_classifier(
    tmp_path, wire, monkeypatch, capsys, caplog, provider, malformed, earlier, threaded
):
    import shlex

    from fdsx.providers.base import _run_subprocess

    answer = '{"answer":"commit","reason":"Earlier"}'
    valid = (
        {"type": "item.completed", "item": {"type": "agent_message", "text": answer}}
        if provider == "codex"
        else {"type": "result", "result": answer}
    )
    bad = malformed
    if malformed == "nested":
        bad = (
            {"type": "item.completed", "item": None}
            if provider == "codex"
            else {"type": "content_block_start", "content_block": None}
        )
    lines = [json.dumps(event) for event in ([valid] if earlier else []) + [bad]]

    def execute(**kwargs):
        if threaded:
            # Exercise the actual subprocess reader thread using only local shell output.
            command = "printf '%s\n' " + " ".join(shlex.quote(line) for line in lines)
            return _run_subprocess(
                args=["sh", "-c", command],
                output_callback=kwargs["output_callback"],
                timeout=5,
            )
        for line in lines:
            kwargs["output_callback"](line)
        return ProviderResult(0, answer, "")

    mock = Mock(side_effect=execute)
    monkeypatch.setattr(f"fdsx.providers.{provider}._run_subprocess", mock)
    with pytest.raises(FlowExecutionError) as error:
        run(
            tmp_path,
            definition(
                acceptance={"probability": 0.9},
                fallback={"provider": provider, "model": "test-model"},
            ),
        )
    assert "Earlier" not in str(error.value)
    captured = capsys.readouterr()
    assert "Earlier" not in captured.out + captured.err + caplog.text
    assert "Traceback" not in captured.err
    records = list((tmp_path / ".fdsx" / "runs").glob("*/run.json"))
    assert records
    for path in records:
        record = json.loads(path.read_text())
        assert not any(
            state["name"] in {"route", "commit", "fix"} for state in record["states"]
        )
        assert "decision" not in (record.get("final_variables") or {})
    assert mock.call_count == 1


@pytest.mark.parametrize(
    "provider,bad_event",
    [("codex", None), ("claude", None)]
    + [
        (
            "codex",
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": value},
            },
        )
        for value in (None, 0, False, [], {})
    ],
)
@pytest.mark.parametrize("earlier", [False, True])
@pytest.mark.parametrize(
    "required,min_success,completed",
    [
        (False, 1, True),
        (False, 2, False),
        (True, None, False),
        (False, None, True),
    ],
)
def test_malformed_stream_parallel_aggregation(
    tmp_path,
    wire,
    monkeypatch,
    capsys,
    caplog,
    provider,
    bad_event,
    earlier,
    required,
    min_success,
    completed,
):
    def execute(**kwargs):
        if earlier:
            answer = '{"answer":"commit","reason":"PRIVATE_STALE"}'
            event = (
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": answer},
                }
                if provider == "codex"
                else {"type": "result", "result": answer}
            )
            kwargs["output_callback"](json.dumps(event))
        kwargs["output_callback"](json.dumps(bad_event))
        return ProviderResult(0, '{"answer":"commit","reason":"PRIVATE_STALE"}', "")

    mock = Mock(side_effect=execute)
    monkeypatch.setattr(f"fdsx.providers.{provider}._run_subprocess", mock)
    parallel = {
        "type": "parallel",
        "branches": [
            classifier(name="good"),
            classifier(
                name="bad",
                acceptance={"probability": 0.9},
                fallback={"provider": provider, "model": "test"},
            ),
        ],
        "result_path": "$.reviews",
        "end": True,
    }
    if min_success is not None:
        parallel["min_success"] = min_success
    else:
        parallel["gate"] = {
            "required": ["bad" if required else "good"],
            "field": "$.decision.answer",
            "expected": "commit",
            "result_path": "$.approved",
        }
    data = definition()
    data["states"] = {"classify": parallel}
    if completed:
        result = run(tmp_path, data)
        assert result.results["reviews"][1]["exit_code"] == 1
        assert "decision" not in result.results["reviews"][1]
    else:
        with pytest.raises(FlowExecutionError):
            run(tmp_path, data)
    captured = capsys.readouterr()
    assert "PRIVATE_STALE" not in captured.err + captured.out + caplog.text
    assert "Traceback" not in captured.err
    assert mock.call_count == 1

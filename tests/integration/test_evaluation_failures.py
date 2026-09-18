"""Safe Jev failure diagnostics through the SDK and workflow boundary, offline."""

import hashlib
import json
import logging
import traceback
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest
import yaml
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core.engine import run_flow
from fdsx.core.engine.errors import FlowExecutionError

PRIVATE = "PRIVATE_EVALUATION_PAYLOAD"
KEY = "PRIVATE_API_CREDENTIAL"
REQUEST_ID = "PRIVATE_REQUEST_IDENTIFIER"


def workflow(tmp_path: Path, kind: str) -> Path:
    question = {
        "type": "choice",
        "instructions": "PRIVATE_QUESTION",
        "criteria": {"go": "PRIVATE_CRITERION", "stop": "PRIVATE_CRITERION_OTHER"},
    }
    if kind == "evaluate":
        state = {
            "type": "evaluate",
            "evaluator": "jev",
            "input": {"document": {"literal": PRIVATE}},
            "questions": {"action": question},
            "result_path": "$.assessment",
            "end": True,
        }
    else:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "description": question["instructions"],
                    "oneOf": [
                        {"const": "go", "description": "PRIVATE_CRITERION"},
                        {"const": "stop", "description": "PRIVATE_CRITERION_OTHER"},
                    ],
                },
            },
        }
        (tmp_path / "answer.json").write_text(json.dumps(schema))
        state = {
            "type": "task",
            "provider": "jev",
            "model": "jev-1.13.0",
            "prompt_template": PRIVATE,
            "structured_output": {
                "schema": "answer.json",
                "result_path": "$.assessment",
            },
            "end": True,
        }
    path = tmp_path / "flow.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "failure-diagnostics",
                "description": "Offline fixture",
                "start_at": "assess",
                "states": {"assess": state},
            }
        )
    )
    return path


CASES = [
    ("400", "http", "TypeSafeBadRequestError", 400, 1),
    ("401", "http", "TypeSafeAuthenticationError", 401, 1),
    ("403", "http", "TypeSafePermissionDeniedError", 403, 1),
    ("404", "http", "TypeSafeNotFoundError", 404, 1),
    ("408", "http", "TypeSafeAPIError", 408, 3),
    ("422", "http", "TypeSafeUnprocessableEntityError", 422, 1),
    ("429", "http", "TypeSafeRateLimitError", 429, 3),
    ("503", "http", "TypeSafeInternalServerError", 503, 3),
    ("connection", "connection", "TypeSafeAPIConnectionError", None, 3),
    ("timeout", "timeout", "TypeSafeAPITimeoutError", None, 3),
    (
        "invalid_json",
        "response_validation",
        "TypeSafeAPIResponseValidationError",
        200,
        1,
    ),
    (
        "invalid_answer",
        "response_validation",
        "TypeSafeAPIResponseValidationError",
        200,
        1,
    ),
]


def wire(monkeypatch, case, request_id=REQUEST_ID, body=None):
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    calls = []

    def request(client, method, url, **kwargs):
        calls.append(1)
        if case == "connection":
            raise httpx2.ConnectError(PRIVATE)
        if case == "timeout":
            raise httpx2.ReadTimeout(PRIVATE)
        headers = {"x-typesafe-request-id": request_id, "Authorization": KEY}
        req = httpx2.Request(method, url)
        if case == "invalid_json":
            return httpx2.Response(
                200, content=PRIVATE.encode(), headers=headers, request=req
            )
        if case == "invalid_answer":
            return httpx2.Response(
                200,
                json={
                    "model": "jev-1.13.0",
                    "usage": {},
                    "answers": {"action": {}},
                    "private": PRIVATE,
                },
                headers=headers,
                request=req,
            )
        return httpx2.Response(
            int(case), json=body or {"message": PRIVATE}, headers=headers, request=req
        )

    monkeypatch.setattr(httpx2.Client, "request", request)
    return calls, sleeps


@pytest.mark.parametrize("kind", ["task", "evaluate"])
@pytest.mark.parametrize("case,category,error_type,status,attempts", CASES)
def test_failures_preserve_safe_diagnostics_without_private_data(
    tmp_path,
    monkeypatch,
    caplog,
    capsys,
    kind,
    case,
    category,
    error_type,
    status,
    attempts,
):
    path = workflow(tmp_path, kind)
    calls, sleeps = wire(monkeypatch, case)
    with (
        caplog.at_level(logging.DEBUG, logger="typesafe_sdk"),
        pytest.raises(FlowExecutionError) as failure,
    ):
        run_flow(path, thread_id="failure", base_dir=tmp_path / ".fdsx")
    message = str(failure.value)
    assert f"category={category}" in message
    assert f"exception_type={error_type}" in message
    if status is not None:
        assert f"http_status={status}" in message
        assert (
            f"request_id_sha256={hashlib.sha256(REQUEST_ID.encode()).hexdigest()}"
            in message
        )
    else:
        assert "http_status=" not in message
        assert "request_id_sha256=" not in message
    assert len(calls) == attempts
    assert sleeps == ([1, 2] if attempts == 3 else [])
    saved = json.loads((tmp_path / ".fdsx/runs/failure/run.json").read_text())
    assert f"category={category}" in json.dumps(saved)
    output = capsys.readouterr()
    rendered = (
        output.out
        + output.err
        + caplog.text
        + "".join(traceback.format_exception(failure.value))
    )
    for file in (tmp_path / ".fdsx/runs/failure").rglob("*"):
        if file.is_file():
            rendered += file.read_text()
    for secret in (
        PRIVATE,
        KEY,
        REQUEST_ID,
        "PRIVATE_QUESTION",
        "PRIVATE_CRITERION",
        "Authorization",
    ):
        assert secret not in rendered


@pytest.mark.parametrize(
    "request_id",
    [
        "",
        "bad\nheader",
        "bad\rheader",
        "bad\x1b[31m",
        "x" * 129,
        "非ASCII",
        "contains space",
    ],
)
def test_untrusted_request_id_is_omitted(tmp_path, monkeypatch, request_id):
    # Direct SDK error injection permits testing headers httpx2 itself may reject.
    from typesafe_sdk import TypeSafeAPIError

    class HeaderFixture:
        def get(self, key):
            return request_id

    error = TypeSafeAPIError(422, PRIVATE, HeaderFixture(), message=PRIVATE)
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    with (
        patch("typesafe_sdk.TypeSafeClient.system_one", side_effect=error),
        pytest.raises(FlowExecutionError) as failure,
    ):
        run_flow(workflow(tmp_path, "task"), base_dir=tmp_path / ".fdsx")
    assert "category=http" in str(failure.value)
    assert "request_id_sha256=" not in str(failure.value)
    assert PRIVATE not in str(failure.value)


@pytest.mark.parametrize("kind", ["task", "evaluate"])
@pytest.mark.parametrize(
    "case,category,error_type",
    [
        ("sdk", "sdk", "TypeSafeError"),
        ("encoding", "encoding", "UnicodeEncodeError"),
        ("decoding", "encoding", "UnicodeDecodeError"),
    ],
)
def test_exception_text_repr_and_class_names_are_not_copied(
    tmp_path, monkeypatch, caplog, capsys, kind, case, category, error_type
):
    from typesafe_sdk import TypeSafeError

    class PrivateError(TypeSafeError):
        def __str__(self):
            raise AssertionError("SDK exception must never be formatted")

        def __repr__(self):
            raise AssertionError("SDK exception must never be represented")

    PrivateError.__name__ = PRIVATE
    errors = {
        "sdk": PrivateError(PRIVATE),
        "encoding": UnicodeEncodeError("ascii", PRIVATE, 0, 1, PRIVATE),
        "decoding": UnicodeDecodeError("ascii", PRIVATE.encode(), 0, 1, PRIVATE),
    }
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    with (
        caplog.at_level(logging.DEBUG, logger="typesafe_sdk"),
        patch("typesafe_sdk.TypeSafeClient.system_one", side_effect=errors[case]),
        pytest.raises(FlowExecutionError) as failure,
    ):
        run_flow(
            workflow(tmp_path, kind), thread_id="failure", base_dir=tmp_path / ".fdsx"
        )
    assert f"category={category}" in str(failure.value)
    assert f"exception_type={error_type}" in str(failure.value)
    assert "http_status=" not in str(failure.value)
    output = capsys.readouterr()
    rendered = (
        output.out
        + output.err
        + caplog.text
        + "".join(traceback.format_exception(failure.value))
    )
    rendered += (tmp_path / ".fdsx/runs/failure/run.json").read_text()
    for secret in (PRIVATE, KEY, "PRIVATE_QUESTION", "PRIVATE_CRITERION"):
        assert secret not in rendered


@pytest.mark.parametrize("kind", ["task", "evaluate"])
@pytest.mark.parametrize("error_type", ["max_tokens_exceeded", "new_service_reason"])
def test_service_reason_reaches_cli_and_record(tmp_path, monkeypatch, kind, error_type):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".fdsx").mkdir()
    path = workflow(tmp_path, kind)
    calls, sleeps = wire(
        monkeypatch,
        "400",
        body={
            "detail": {"error_type": error_type, "message": PRIVATE},
            "Authorization": KEY,
            "request": PRIVATE,
        },
    )
    result = CliRunner().invoke(app, ["run", str(path)])
    assert result.exit_code == 1
    saved = next((tmp_path / ".fdsx/runs").glob("*/run.json")).read_text()
    for text in (result.output, saved):
        assert f"error_type={error_type}" in text
        assert "http_status=400" in text
        assert "request_id_sha256=" in text
        if error_type == "max_tokens_exceeded":
            assert "retrying the same input will not resolve" in text
        for secret in (PRIVATE, KEY, "Authorization"):
            assert secret not in text
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "error_type", [PRIVATE, KEY, {"message": PRIVATE}, "bad\ncode"]
)
def test_error_type_does_not_expose_echoed_secrets(
    tmp_path, monkeypatch, capsys, caplog, error_type
):
    monkeypatch.chdir(tmp_path)
    path = workflow(tmp_path, "task")
    wire(monkeypatch, "400", body={"detail": {"error_type": error_type}})
    with pytest.raises(FlowExecutionError) as failure:
        run_flow(path, thread_id="redacted", base_dir=tmp_path / ".fdsx")
    output = capsys.readouterr()
    text = str(failure.value) + output.out + output.err + caplog.text
    text += (tmp_path / ".fdsx/runs/redacted/run.json").read_text()
    assert "error_type=" not in text
    for secret in (PRIVATE, KEY, "bad\\ncode"):
        assert secret not in text


@pytest.mark.parametrize("kind", ["task", "evaluate"])
@pytest.mark.parametrize(
    "usage", [{"input_tokens": 123, "output_tokens": 7}, {}, {"input_tokens": 0}]
)
def test_unicode_and_usage_through_shared_boundary(tmp_path, monkeypatch, kind, usage):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    path = workflow(tmp_path, kind)
    material = "日本語の材料 😀"
    path.write_text(path.read_text().replace(PRIVATE, material))
    sent = []

    def request(client, method, url, **kwargs):
        payload = json.loads(kwargs["content"])
        sent.append(payload["state"])
        return httpx2.Response(
            200,
            request=httpx2.Request(method, url),
            json={
                "model": "jev-1.13.0",
                "usage": usage,
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "go",
                        "probabilities": {"go": 1.0, "stop": 0.0},
                        "confidence": 1.0,
                    }
                },
            },
        )

    monkeypatch.setattr(httpx2.Client, "request", request)
    run_flow(path, thread_id="usage", base_dir=tmp_path / ".fdsx")
    assert len(sent) == 1
    assert material in sent[0]
    assert json.loads(sent[0]) == {"prompt" if kind == "task" else "document": material}
    saved = json.loads((tmp_path / ".fdsx/runs/usage/run.json").read_text())
    assert saved["states"][0]["evaluation"]["usage"] == {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }


def test_cli_preserves_safe_failure_without_traceback(tmp_path, monkeypatch, caplog):
    path = workflow(tmp_path, "task")
    wire(monkeypatch, "422")
    (tmp_path / ".fdsx").mkdir()
    with caplog.at_level(logging.DEBUG, logger="typesafe_sdk"):
        result = CliRunner().invoke(app, ["run", str(path)])
    assert result.exit_code == 1
    assert "category=http" in result.output
    assert "http_status=422" in result.output
    assert "Traceback" not in result.output
    for secret in (
        PRIVATE,
        KEY,
        REQUEST_ID,
        "PRIVATE_QUESTION",
        "PRIVATE_CRITERION",
        "Authorization",
    ):
        assert secret not in result.output + caplog.text

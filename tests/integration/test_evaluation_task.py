"""Jev tasks use the public engine and the real SDK with offline HTTP."""

import json
from copy import deepcopy
from unittest.mock import patch

import httpx2
import pytest
import yaml

from fdsx.core.engine import run_flow
from fdsx.core.engine.errors import FlowExecutionError
from fdsx.core.engine.validate import FlowValidationError
from fdsx.providers.base import ProviderResult
from tests.integration import test_evaluation as legacy
from tests.unit.test_evaluation_schema import contract


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-key")
    calls = []
    answers = deepcopy(legacy.ANSWERS)
    answers["action"]["choice"] = "proceed"
    answers["action"]["probabilities"] = {"fix": 0.5, "proceed": 0.5}
    answers["risk"] = answers.pop("ambiguity")
    answers["quality"]["legend"] = {"0": "Missing", "1": "Partial", "2": "Complete"}
    payload = {"model": "reported-model", "usage": {}, "answers": answers}

    def request(client, method, url, **kwargs):
        calls.append(json.loads(kwargs["content"]))
        return httpx2.Response(200, json=payload, request=httpx2.Request(method, url))

    monkeypatch.setattr(httpx2.Client, "request", request)
    return calls, payload


def write_flow(tmp_path, provider="jev", modify=None):
    data = {
        "name": "task-evaluation",
        "description": "Explicit judgment",
        "start_at": "assess",
        "states": {
            "assess": {
                "type": "task",
                "provider": provider,
                "model": "jev-1.13.0" if provider == "jev" else "test-model",
                "prompt_template": "Review: {review}. Requirements: complete implementation. Criteria: no blocking defects.",
                "structured_output": {
                    "schema": "output.json",
                    "result_path": "$.assessment",
                    "allow_extra_fields": False,
                },
                "next": "route",
            },
            "route": {
                "type": "choice",
                "choices": [
                    {
                        "variable": "$.assessment.action",
                        "operator": "equals",
                        "value": "proceed",
                        "next": "done",
                    }
                ],
                "default": "wrong",
            },
            "done": {
                "type": "task",
                "provider": "system",
                "command": "echo {assessment.quality}",
                "result_path": "$.observed",
                "end": True,
            },
            "wrong": {
                "type": "fail",
                "error": "wrong",
                "cause": "wrong",
            },
        },
    }
    if modify:
        modify(data)
    (tmp_path / "output.json").write_text(json.dumps(contract()))
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize("provider", ["jev", "claude"])
def test_common_task_contract_routes_and_feeds_following_task(tmp_path, wire, provider):
    value = {"action": "proceed", "risk": 0.2, "quality": 1.6}
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, json.dumps(value), ""),
    ) as llm:
        result = run_flow(
            write_flow(tmp_path, provider),
            {"review": "No blocking defect observed", "private": "DO NOT SEND"},
            base_dir=tmp_path / ".fdsx",
        )
    assert result.status == "completed"
    assert result.results["assessment"] == value
    assert result.results["observed"].strip() == "1.6"
    assert llm.call_count == (provider == "claude")
    if provider == "jev":
        assert len(wire[0]) == 1
        assert "DO NOT SEND" not in json.dumps(wire[0])
        assert set(json.loads(wire[0][0]["state"])) == {"prompt"}
        assert (
            wire[0][0]["questions"]["action"]["criteria"]["fix"]
            == "A confirmed defect needs repair."
        )
    else:
        assert not wire[0]
        arguments = str(llm.call_args)
        assert "x-fdsx-evaluation" not in arguments
        assert "Missing" in arguments and "fractional" in arguments


@pytest.mark.parametrize(
    "option,value",
    [
        ("retry", 2),
        ("timeout_seconds", 3),
        ("fork_from", "before"),
        ("provider_options", {"effort": "high"}),
    ],
)
def test_unsupported_options_fail_before_execution(tmp_path, wire, option, value):
    path = write_flow(
        tmp_path, modify=lambda d: d["states"]["assess"].update({option: value})
    )
    with (
        patch("fdsx.core.engine.run.execute_workflow_hooks") as hooks,
        pytest.raises(FlowValidationError),
    ):
        run_flow(path, {"review": "review"}, base_dir=tmp_path / ".fdsx")
    hooks.assert_not_called()
    assert not wire[0]


def test_missing_key_precedes_hooks(tmp_path, monkeypatch, wire):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with (
        patch("fdsx.core.engine.run.execute_workflow_hooks") as hooks,
        pytest.raises(FlowValidationError, match="TYPESAFE_API_KEY"),
    ):
        run_flow(write_flow(tmp_path), {"review": "text"}, base_dir=tmp_path / ".fdsx")
    hooks.assert_not_called()
    assert not wire[0]


def test_missing_input_never_reaches_http(tmp_path, wire):
    with pytest.raises(FlowExecutionError, match="missing input reference"):
        run_flow(write_flow(tmp_path), base_dir=tmp_path / ".fdsx")
    assert not wire[0]


@pytest.mark.parametrize(
    "damage", ["missing", "extra", "unknown", "distribution", "score"]
)
def test_invalid_answer_stops_without_downstream_execution(tmp_path, wire, damage):
    answers = wire[1]["answers"]
    if damage == "missing":
        del answers["risk"]
    elif damage == "extra":
        answers["extra"] = deepcopy(answers["risk"])
    elif damage == "unknown":
        answers["action"]["choice"] = "unknown"
    elif damage == "distribution":
        answers["action"]["probabilities"]["fix"] = 0.9
    else:
        answers["quality"]["score"] = 0.0
    with (
        patch("fdsx.providers.system._run_subprocess") as downstream,
        pytest.raises(FlowExecutionError),
    ):
        run_flow(write_flow(tmp_path), {"review": "text"}, base_dir=tmp_path / ".fdsx")
    downstream.assert_not_called()
    assert len(wire[0]) == 1


def test_profiles_resolve_to_jev(tmp_path, wire):
    def modify(data):
        data["profiles"] = {"judge": {"provider": "jev", "model": "jev-1.13.0"}}
        task = data["states"]["assess"]
        task.pop("provider")
        task.pop("model")
        task["profile"] = "judge"

    result = run_flow(
        write_flow(tmp_path, modify=modify),
        {"review": "text"},
        base_dir=tmp_path / ".fdsx",
    )
    assert result.results["assessment"]["action"] == "proceed"
    assert len(wire[0]) == 1


@pytest.mark.parametrize("nested", ["parallel", "map"])
def test_nested_jev_is_rejected_before_start(tmp_path, wire, nested):
    def modify(data):
        task = data["states"].pop("assess")
        task.pop("next")
        task.pop("type")
        if nested == "parallel":
            data["states"]["assess"] = {
                "type": "parallel",
                "branches": [task],
                "result_path": "$.assessment",
                "end": True,
            }
        else:
            task.update(name="judge", result_path="$.raw")
            data["states"]["assess"] = {
                "type": "map",
                "items_path": "$.items",
                "iterator": {"states": [task]},
                "result_path": "$.assessment",
                "end": True,
            }

    with pytest.raises(FlowValidationError, match="top-level only"):
        run_flow(
            write_flow(tmp_path, modify=modify),
            {"review": "text", "items": "[]"},
            base_dir=tmp_path / ".fdsx",
        )
    assert not wire[0]


@pytest.mark.parametrize("mode", ["read", "choice", "default", "from"])
def test_resume_checks_reachable_tasks_and_reuses_saved_result(
    tmp_path, monkeypatch, wire, mode
):
    from fdsx.core.engine import resume_flow

    def modify(data):
        data["states"]["assess"]["next"] = "pause"
        data["states"]["pause"] = {
            "type": "wait",
            "message": "Continue?",
            "choices": ["yes"],
            "result_path": "$.confirmation",
            "next": "route",
        }

    path = write_flow(tmp_path, modify=modify)
    base = tmp_path / ".fdsx"
    with (
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=RuntimeError("interrupted"),
        ),
        pytest.raises(FlowExecutionError),
    ):
        run_flow(path, {"review": "text"}, "saved", base)
    data = yaml.safe_load(path.read_text())
    if mode == "choice":
        data["states"]["route"]["choices"][0]["next"] = "assess"
    elif mode == "default":
        data["states"]["route"]["default"] = "assess"
    path.write_text(yaml.safe_dump(data))
    monkeypatch.delenv("TYPESAFE_API_KEY")
    if mode == "read":
        with patch("fdsx.core.engine.resume.display_wait_prompt", return_value="yes"):
            result = resume_flow("saved", base)
        assert result.results["assessment"]["action"] == "proceed"
    else:
        with pytest.raises(FlowValidationError, match="TYPESAFE_API_KEY"):
            resume_flow("saved", base, from_state="assess" if mode == "from" else None)
    assert len(wire[0]) == 1


@pytest.mark.parametrize("recover", [True, False])
def test_sdk_owns_retries_without_escalation_or_repeating_generation(
    tmp_path, monkeypatch, wire, recover
):
    calls = []
    waits = []
    original = httpx2.Client.request

    def request(client, method, url, **kwargs):
        calls.append(url)
        if not recover or len(calls) < 3:
            raise httpx2.ConnectError("PRIVATE_EXCEPTION")
        return original(client, method, url, **kwargs)

    monkeypatch.setattr(httpx2.Client, "request", request)
    monkeypatch.setattr("tenacity.nap.time.sleep", waits.append)

    def modify(data):
        data["start_at"] = "generate"
        data["retry_escalation"] = {"provider": "claude", "model": "alternate"}
        data["states"]["generate"] = {
            "type": "task",
            "provider": "claude",
            "model": "test",
            "prompt_template": "Review",
            "result_path": "$.review",
            "next": "assess",
        }

    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "review", ""),
    ) as llm:
        if recover:
            result = run_flow(
                write_flow(tmp_path, modify=modify), base_dir=tmp_path / ".fdsx"
            )
            assert result.status == "completed"
        else:
            with pytest.raises(FlowExecutionError, match="Jev request failed"):
                run_flow(
                    write_flow(tmp_path, modify=modify), base_dir=tmp_path / ".fdsx"
                )
    assert llm.call_count == 1
    assert len(calls) == 3
    assert waits == [1, 2]


def test_task_hooks_and_diagnostics_do_not_copy_materials(tmp_path, wire, caplog):
    import logging

    def modify(data):
        data["states"]["assess"]["hooks"] = {
            "on_state_start": [{"command": "true"}],
            "on_state_end": [{"command": "true"}],
        }

    with caplog.at_level(logging.DEBUG, logger="typesafe_sdk"):
        run_flow(
            write_flow(tmp_path, modify=modify),
            {"review": "PRIVATE_MATERIAL"},
            "private",
            tmp_path / ".fdsx",
        )
    assert "PRIVATE_MATERIAL" not in caplog.text
    assert "fake-key" not in caplog.text
    files = list((tmp_path / ".fdsx/runs/private/hooks/assess").glob("*.json"))
    assert len(files) == 2
    for file in files:
        assert set(json.loads(file.read_text())) == {"state", "status"}
    records = list((tmp_path / ".fdsx/runs/private").glob("*.json"))
    assert records
    recorded = "\n".join(file.read_text() for file in records)
    assert '"source": "service"' in recorded
    assert '"reported_model": "reported-model"' in recorded


def test_explicit_metadata_and_prompt_file_are_shared_with_llm(
    tmp_path, wire, monkeypatch
):
    path = write_flow(tmp_path)
    schema = contract()
    schema["properties"]["certainty"] = {
        "type": "number",
        "minimum": 0,
        "maximum": 1,
        "x-fdsx-evaluation": {
            "kind": "metadata",
            "question": "action",
            "field": "confidence",
        },
    }
    schema["required"].append("certainty")
    (tmp_path / "output.json").write_text(json.dumps(schema))
    data = yaml.safe_load(path.read_text())
    task = data["states"]["assess"]
    (tmp_path / "input.txt").write_text(task.pop("prompt_template"))
    task["prompt_file"] = "input.txt"
    path.write_text(yaml.safe_dump(data))
    result = run_flow(path, {"review": "text"}, base_dir=tmp_path / ".fdsx")
    assert result.results["assessment"] == {
        "action": "proceed",
        "risk": 0.2,
        "quality": 1.6,
        "certainty": 0.0,
    }
    monkeypatch.delenv("TYPESAFE_API_KEY")
    task.update(provider="claude", model="mock")
    path.write_text(yaml.safe_dump(data))
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, json.dumps(result.results["assessment"]), ""),
    ):
        result2 = run_flow(path, {"review": "text"}, base_dir=tmp_path / ".fdsx")
    assert result2.results["assessment"] == result.results["assessment"]
    assert len(wire[0]) == 1


@pytest.mark.parametrize("invalid", ["schema", "key"])
def test_cli_preflight_precedes_start_hooks_and_generation(
    tmp_path, monkeypatch, wire, invalid
):
    from typer.testing import CliRunner

    from fdsx.cli.main import app

    def modify(data):
        data["start_at"] = "generate"
        data["states"]["generate"] = {
            "type": "task",
            "provider": "system",
            "command": "echo before",
            "result_path": "$.review",
            "next": "assess",
        }

    path = write_flow(tmp_path, modify=modify)
    (tmp_path / ".fdsx").mkdir()
    if invalid == "schema":
        schema = contract()
        schema["properties"]["action"]["minLength"] = 2
        (tmp_path / "output.json").write_text(json.dumps(schema))
    else:
        monkeypatch.delenv("TYPESAFE_API_KEY")
    with (
        patch("fdsx.cli.main.execute_run_hooks") as hooks,
        patch("fdsx.providers.system._run_subprocess") as generate,
    ):
        result = CliRunner().invoke(app, ["run", str(path)])
    assert result.exit_code != 0
    assert (
        "TYPESAFE_API_KEY" in result.stderr
        if invalid == "key"
        else "unsupported schema" in result.stderr
    )
    hooks.assert_not_called()
    generate.assert_not_called()
    assert not wire[0]


@pytest.mark.parametrize("status", [401, 429])
def test_authentication_stops_immediately_and_retry_after_is_respected(
    tmp_path, monkeypatch, wire, status
):
    calls = []
    waits = []
    original = httpx2.Client.request

    def request(client, method, url, **kwargs):
        calls.append(url)
        if status == 401 or len(calls) == 1:
            return httpx2.Response(
                status,
                json={"message": "PRIVATE_FAILURE"},
                headers={"Retry-After": "5"},
                request=httpx2.Request(method, url),
            )
        return original(client, method, url, **kwargs)

    monkeypatch.setattr(httpx2.Client, "request", request)
    monkeypatch.setattr("tenacity.nap.time.sleep", waits.append)
    if status == 401:
        with pytest.raises(FlowExecutionError, match="Jev request failed") as error:
            run_flow(
                write_flow(tmp_path), {"review": "text"}, base_dir=tmp_path / ".fdsx"
            )
        assert "PRIVATE_FAILURE" not in str(error.value)
        assert len(calls) == 1
        assert not waits
    else:
        result = run_flow(
            write_flow(tmp_path), {"review": "text"}, base_dir=tmp_path / ".fdsx"
        )
        assert result.status == "completed"
        assert len(calls) == 2
        assert waits == [5]


def test_empty_resolved_prompt_is_not_sent(tmp_path, wire):
    path = write_flow(
        tmp_path,
        modify=lambda d: d["states"]["assess"].update(prompt_template="{review}"),
    )
    with pytest.raises(FlowExecutionError):
        run_flow(path, {"review": "  "}, base_dir=tmp_path / ".fdsx")
    assert not wire[0]


def test_resume_from_assessment_reexecutes_only_the_reached_judgment(tmp_path, wire):
    from fdsx.core.engine import resume_flow

    base = tmp_path / ".fdsx"

    def modify(data):
        data["states"]["assess"]["next"] = "pause"
        data["states"]["pause"] = {
            "type": "wait",
            "message": "Continue?",
            "choices": ["yes"],
            "result_path": "$.confirmation",
            "next": "route",
        }

    with (
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=RuntimeError("interrupted"),
        ),
        pytest.raises(FlowExecutionError),
    ):
        run_flow(write_flow(tmp_path, modify=modify), {"review": "text"}, "saved", base)
    with patch("fdsx.core.engine.resume.display_wait_prompt", return_value="yes"):
        result = resume_flow("saved", base, from_state="assess")
    assert result.status == "completed"
    assert len(wire[0]) == 2


@pytest.mark.parametrize("invalid", ["key", "schema"])
def test_multi_task_preflight_rejects_later_jev_before_any_task(
    tmp_path, monkeypatch, wire, invalid
):
    from typer.testing import CliRunner

    from fdsx.cli.main import app

    workflows = tmp_path / ".fdsx/workflows"
    workflows.mkdir(parents=True)
    path = write_flow(
        workflows,
        modify=lambda d: d["states"]["assess"].update(prompt_template="{task}"),
    )
    plain = workflows / "plain.yaml"
    plain.write_text(
        yaml.safe_dump(
            {
                "name": "plain",
                "description": "plain",
                "start_at": "done",
                "states": {
                    "done": {
                        "type": "task",
                        "provider": "system",
                        "command": "echo plain",
                        "end": True,
                    }
                },
            }
        )
    )
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "001.yaml").write_text("description: first\nworkflow: plain.yaml\n")
    (tasks / "002.yaml").write_text("description: second\nworkflow: task.yaml\n")
    if invalid == "key":
        monkeypatch.delenv("TYPESAFE_API_KEY")
    else:
        schema = contract()
        del schema["properties"]["action"]["description"]
        (workflows / "output.json").write_text(json.dumps(schema))
    with (
        patch("fdsx.cli.main.execute_run_hooks") as hooks,
        patch("fdsx.providers.system._run_subprocess") as provider,
    ):
        result = CliRunner().invoke(
            app, ["run", "--tasks-dir", str(tasks), "--auto-workflow"]
        )
    assert result.exit_code != 0, result.output
    assert path.name == "task.yaml"
    hooks.assert_not_called()
    provider.assert_not_called()
    assert not wire[0]


@pytest.mark.parametrize("provider", ["jev", "llm"])
def test_documented_examples_execute_offline(tmp_path, wire, provider):
    import shutil
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "src/fdsx/examples/workflows/evaluation-task"
    )
    for file in source.iterdir():
        shutil.copy(file, tmp_path / file.name)
    wire[1]["answers"] = {
        "action": {
            "type": "choice",
            "choice": "proceed",
            "probabilities": {"fix": 0.1, "investigate": 0.2, "proceed": 0.7},
            "confidence": 0.1,
        }
    }
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, '{"action":"proceed"}', ""),
    ):
        result = run_flow(
            tmp_path / f"review-{provider}.yaml",
            {"review": "No blocker observed", "requirements": "All work complete"},
            base_dir=tmp_path / ".fdsx",
        )
    assert result.results["assessment"] == {"action": "proceed"}


def test_mixed_documented_schema_projects_all_requested_metrics(tmp_path, wire):
    from pathlib import Path

    path = write_flow(tmp_path)
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "src/fdsx/examples/workflows/evaluation-task/assessment.schema.json"
    )
    schema = json.loads(schema_path.read_text())
    (tmp_path / "output.json").write_text(json.dumps(schema))
    answers = wire[1]["answers"]
    answers["ambiguity"] = answers.pop("risk")
    answers["action"]["probabilities"] = {
        "fix": 0.1,
        "investigate": 0.2,
        "proceed": 0.7,
    }
    answers["quality"]["legend"] = dict(
        enumerate(schema["properties"]["quality"]["x-fdsx-evaluation"]["criteria"])
    )
    result = run_flow(path, {"review": "text"}, base_dir=tmp_path / ".fdsx")
    assert set(result.results["assessment"]) == set(schema["required"])
    assert (
        result.results["assessment"]["action_probabilities"]
        == answers["action"]["probabilities"]
    )
    assert result.results["assessment"]["action_confidence"] == 0.0


def test_loop_reaching_judgment_evaluates_again(tmp_path, monkeypatch, wire):
    original = httpx2.Client.request

    def request(client, method, url, **kwargs):
        first = not wire[0]
        wire[1]["answers"]["action"].update(
            choice="fix" if first else "proceed",
            probabilities={
                "fix": 1.0 if first else 0.0,
                "proceed": 0.0 if first else 1.0,
            },
        )
        return original(client, method, url, **kwargs)

    monkeypatch.setattr(httpx2.Client, "request", request)

    def modify(data):
        data["states"]["assess"]["max_iterations"] = 2
        data["states"]["route"]["choices"].append(
            {
                "variable": "$.assessment.action",
                "operator": "equals",
                "value": "fix",
                "next": "assess",
            }
        )

    result = run_flow(
        write_flow(tmp_path, modify=modify),
        {"review": "text"},
        base_dir=tmp_path / ".fdsx",
    )
    assert result.results["assessment"]["action"] == "proceed"
    assert len(wire[0]) == 2


def test_unexecuted_judgment_still_requires_key_on_fresh_run(
    tmp_path, monkeypatch, wire
):
    monkeypatch.delenv("TYPESAFE_API_KEY")

    def modify(data):
        data["start_at"] = "skip"
        data["states"]["skip"] = {"type": "pass", "end": True}

    with pytest.raises(FlowValidationError, match="TYPESAFE_API_KEY"):
        run_flow(
            write_flow(tmp_path, modify=modify),
            {"review": "text"},
            base_dir=tmp_path / ".fdsx",
        )
    assert not wire[0]

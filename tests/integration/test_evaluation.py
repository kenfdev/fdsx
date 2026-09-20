"""Evaluation through public engine/CLI and the real SDK's offline HTTP boundary."""

import json
import logging
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

import httpx2
import pytest
import yaml
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.engine.errors import FlowExecutionError
from fdsx.core.engine.validate import FlowValidationError

QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": "Choose",
        "criteria": {"fix": "Fix", "go": "Continue"},
    },
    "ambiguity": {
        "type": "noul",
        "instructions": "Ambiguous?",
        "criteria": {"true": "Yes", "false": "No"},
    },
    "quality": {
        "type": "score",
        "instructions": "Score",
        "criteria": ["Bad", "Good", "Best"],
    },
}
ANSWERS = {
    "action": {
        "type": "choice",
        "choice": "go",
        "probabilities": {"fix": 0.5, "go": 0.5},
        "confidence": 0.0,
    },
    "ambiguity": {"type": "noul", "noul": 0.2},
    "quality": {
        "type": "score",
        "score": 1.6,
        "legend": {"0": "Bad", "1": "Good", "2": "Best"},
        "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
        "confidence": 0.12,
    },
}


def definition():
    return {
        "name": "evaluate-review",
        "description": "Explicit assessment",
        "start_at": "assess",
        "states": {
            "assess": {
                "type": "evaluate",
                "evaluator": "jev",
                "input": {
                    "review": {"ref": "$.review"},
                    "rules": {
                        "literal": {
                            "text": "{private}",
                            "ref": "$.private",
                            "nested": None,
                        }
                    },
                },
                "questions": deepcopy(QUESTIONS),
                "result_path": "$.assessment",
                "next": "route",
            },
            "route": {
                "type": "choice",
                "choices": [
                    {
                        "variable": "$.assessment.answers.action.choice",
                        "operator": "equals",
                        "value": "go",
                        "next": "done",
                    }
                ],
                "default": "wrong",
            },
            "done": {"type": "pass", "parameters": {"$.routed": "yes"}, "end": True},
            "wrong": {
                "type": "fail",
                "error": "wrong branch",
                "cause": "unexpected route",
            },
        },
    }


def write_flow(tmp_path, data=None):
    path = tmp_path / "flow.yaml"
    path.write_text(yaml.safe_dump(data or definition(), sort_keys=False))
    return path


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-evaluation-key")
    requests = []
    payload = {"model": "reported-model", "usage": {}, "answers": deepcopy(ANSWERS)}

    def request(client, method, url, **kwargs):
        requests.append((url, kwargs))
        return httpx2.Response(200, json=payload, request=httpx2.Request(method, url))

    monkeypatch.setattr(httpx2.Client, "request", request)
    return requests, payload


def test_mixed_evaluation_sends_only_explicit_materials_and_routes(
    tmp_path, monkeypatch, wire
):
    requests, _ = wire
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://invalid.example")
    monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "unwanted-model")
    result = run_flow(
        write_flow(tmp_path),
        {"review": "Review text", "private": "DO NOT SEND"},
        base_dir=tmp_path / ".fdsx",
    )
    assert result.status == "completed"
    assert result.results["routed"] == "yes"
    assert result.results["assessment"] == {
        "model": {"requested": "jev-1.13.0", "reported": "reported-model"},
        "answers": ANSWERS,
    }
    assert len(requests) == 1
    url, options = requests[0]
    assert url.startswith("https://api.typesafe.ai/")
    body = json.loads(options["content"])
    assert json.loads(body["state"]) == {
        "review": "Review text",
        "rules": {"text": "{private}", "ref": "$.private", "nested": None},
    }
    assert "DO NOT SEND" not in options["content"].decode()
    assert body["model"] == "jev-1.13.0"
    assert set(body["questions"]) == set(QUESTIONS)
    timeout = httpx2.Timeout(options["timeout"])
    assert timeout.connect == timeout.read == 30
    assert timeout.write == timeout.pool == 30


@pytest.mark.parametrize("value", [None, "", "  ", [], {}, float("inf"), {1: "bad"}])
def test_invalid_material_stops_before_http(tmp_path, wire, value):
    data = definition()
    data["states"]["assess"]["input"] = {"document": {"literal": value}}
    with pytest.raises(FlowExecutionError, match=r"assess.input.document"):
        run_flow(write_flow(tmp_path, data), base_dir=tmp_path / ".fdsx")
    assert wire[0] == []


@pytest.mark.parametrize("value", [0, False, {"null": None}, [None, ""]])
def test_valid_falsy_and_nested_empty_materials(tmp_path, wire, value):
    data = definition()
    data["states"]["assess"]["input"] = {"document": {"literal": value}}
    result = run_flow(write_flow(tmp_path, data), base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    assert json.loads(json.loads(wire[0][0][1]["content"])["state"]) == {
        "document": value
    }


def test_missing_reference_stops_before_http(tmp_path, wire):
    with pytest.raises(
        FlowExecutionError, match=r"assess.input.review: missing reference"
    ):
        run_flow(write_flow(tmp_path), base_dir=tmp_path / ".fdsx")
    assert wire[0] == []


@pytest.mark.parametrize(
    "change",
    [
        {"result_path": "$.nested.value"},
        {"result_path": "$._meta"},
        {"result_path": "$.remaining_steps"},
        {"input": {"x": {"ref": "$"}}},
        {"input": {"x": {"ref": "$.x[*]"}}},
        {"input": {"x": {"ref": "$.x["}}},
        {"input": {"x": {"ref": "$.x..y"}}},
        {"input": {"x": {"literal": "secret", "ref": "$.x"}}},
        {"input": {}},
        {"input": {"bad-name": {"literal": "secret"}}},
        {"questions": {}},
        {"questions": {"q": {"type": "score", "instructions": "x", "criteria": ["x"]}}},
        {
            "questions": {
                "q": {"type": "noul", "instructions": "x", "criteria": {True: "yes"}}
            }
        },
        {
            "questions": {
                "q": {
                    "type": "choice",
                    "instructions": " ",
                    "criteria": {"a": "a", "b": "b"},
                }
            }
        },
        {"end": True},
        {"end": False, "next": None},
        {"evaluator": "other"},
        {"model": " "},
    ],
)
def test_definition_errors_are_safe_and_preexecution(tmp_path, wire, change):
    data = definition()
    data["states"]["assess"].update(change)
    with pytest.raises(FlowValidationError) as error:
        run_flow(write_flow(tmp_path, data), base_dir=tmp_path / ".fdsx")
    assert "assess" in str(error.value)
    assert "input_value" not in str(error.value)
    assert wire[0] == []


@pytest.mark.parametrize("kind", ["parallel", "map"])
def test_legacy_nested_evaluation_requires_local_form_with_location(
    tmp_path, wire, kind
):
    data = definition()
    assessment = data["states"]["assess"]
    data["states"]["assess"] = (
        {"type": "parallel", "branches": [assessment], "end": True}
        if kind == "parallel"
        else {
            "type": "map",
            "items_path": "$.items",
            "iterator": {"states": [assessment]},
            "end": True,
        }
    )
    with pytest.raises(FlowValidationError, match=r"assess.*local workflow form"):
        run_flow(write_flow(tmp_path, data), base_dir=tmp_path / ".fdsx")
    assert not wire[0]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a.pop("quality"),
        lambda a: a.update(extra={"type": "noul", "noul": 0.5}),
        lambda a: a.update(extra={"type": "future", "value": 0.5}),
        lambda a: a.update(action={"type": "future", "value": 0.5}),
        lambda a: a.update(action={"type": "noul", "noul": 0.5}),
        lambda a: a["action"].update(choice="unknown"),
        lambda a: a["action"].update(
            choice="fix", probabilities={"fix": 0.1, "go": 0.9}
        ),
        lambda a: a["action"].update(probabilities={"fix": 0.1, "go": 1.1}),
        lambda a: a["action"].update(probabilities={"unknown": 0.5, "go": 0.5}),
        lambda a: a["action"].update(confidence=True),
        lambda a: a["action"].update(confidence=1.1),
        lambda a: a["ambiguity"].update(noul=-0.1),
        lambda a: a["quality"].update(score=0.2),
        lambda a: a["quality"].update(
            legend={"0": "different", "1": "Good", "2": "Best"}
        ),
    ],
)
def test_invalid_responses_are_atomic_and_not_retried(tmp_path, wire, mutation):
    mutation(wire[1]["answers"])
    with pytest.raises(FlowExecutionError, match="Evaluation assess"):
        run_flow(write_flow(tmp_path), {"review": "text"}, "bad", tmp_path / ".fdsx")
    assert len(wire[0]) == 1
    record = json.loads((tmp_path / ".fdsx/runs/bad/run.json").read_text())
    assert not any(state["name"] == "route" for state in record["states"])
    assert "assessment" not in record.get("final_state", {})


@pytest.mark.parametrize("status", [408, 429, 500, 529, 599, 401, 403, 422])
def test_sdk_retry_count_and_delays(tmp_path, monkeypatch, wire, status):
    calls, sleeps = [], []

    def request(client, method, url, **kwargs):
        calls.append(url)
        return httpx2.Response(
            status, json={"error": "SECRET"}, request=httpx2.Request(method, url)
        )

    monkeypatch.setattr(httpx2.Client, "request", request)
    monkeypatch.setattr("tenacity.nap.time.sleep", sleeps.append)
    with pytest.raises(FlowExecutionError, match="Jev request failed"):
        run_flow(write_flow(tmp_path), {"review": "text"}, base_dir=tmp_path / ".fdsx")
    retryable = status in (408, 429) or status >= 500
    assert len(calls) == (3 if retryable else 1)
    assert sleeps == ([1, 2] if retryable else [])


@pytest.mark.parametrize("delay,expected_calls", [("7", 3), ("60", 1)])
def test_retry_after_has_priority_and_respects_budget(
    tmp_path, monkeypatch, wire, delay, expected_calls
):
    calls, sleeps = [], []

    def request(client, method, url, **kwargs):
        calls.append(url)
        return httpx2.Response(
            429,
            headers={"Retry-After": delay},
            json={},
            request=httpx2.Request(method, url),
        )

    monkeypatch.setattr(httpx2.Client, "request", request)
    monkeypatch.setattr("tenacity.nap.time.sleep", sleeps.append)
    with pytest.raises(FlowExecutionError):
        run_flow(write_flow(tmp_path), {"review": "text"}, base_dir=tmp_path / ".fdsx")
    assert len(calls) == expected_calls
    assert sleeps == ([7, 7] if expected_calls == 3 else [])


@pytest.mark.parametrize("error_type", [httpx2.ConnectError, httpx2.ReadTimeout])
def test_transport_recovery_does_not_repeat_prior_task(
    tmp_path, monkeypatch, wire, error_type
):
    from fdsx.providers.base import ProviderResult

    data = definition()
    data["start_at"] = "generate"
    data["states"]["generate"] = {
        "type": "task",
        "provider": "claude",
        "model": "fake",
        "prompt_template": "Review",
        "result_path": "$.review",
        "next": "assess",
    }
    original = httpx2.Client.request
    calls = []

    def request(client, method, url, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            raise error_type("private error")
        return original(client, method, url, **kwargs)

    monkeypatch.setattr(httpx2.Client, "request", request)
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _: None)
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "Review text", ""),
    ) as provider:
        result = run_flow(write_flow(tmp_path, data), base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    assert len(calls) == 3
    assert provider.call_count == 1


def test_missing_key_precedes_cli_and_engine_hooks(tmp_path, monkeypatch, wire):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    data = definition()
    data["start_at"] = "before"
    data["states"]["before"] = {
        "type": "task",
        "provider": "system",
        "command": "echo forbidden",
        "next": "assess",
    }
    path = write_flow(tmp_path, data)
    (tmp_path / ".fdsx").mkdir()
    with (
        patch("fdsx.cli.main.execute_run_hooks") as run_hooks,
        patch("fdsx.core.engine.run.execute_workflow_hooks") as flow_hooks,
        patch("fdsx.providers.system._run_subprocess") as provider,
    ):
        cli = CliRunner().invoke(app, ["run", str(path), "--input", "review=text"])
        assert cli.exit_code != 0, cli.output
        assert "TYPESAFE_API_KEY" in cli.stderr
        run_hooks.assert_not_called()
        flow_hooks.assert_not_called()
        provider.assert_not_called()
        with pytest.raises(FlowValidationError):
            run_flow(path, {"review": "text"}, base_dir=tmp_path / ".fdsx")
        flow_hooks.assert_not_called()
    assert not wire[0]


def test_checkpoint_reuses_answers_without_key_or_http(tmp_path, monkeypatch, wire):
    data = definition()
    data["states"]["assess"]["next"] = "pause"
    data["states"]["pause"] = {
        "type": "wait",
        "message": "Continue?",
        "choices": ["yes"],
        "result_path": "$.confirmation",
        "next": "route",
    }
    path = write_flow(tmp_path, data)
    base = tmp_path / ".fdsx"
    with (
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=RuntimeError("interrupted"),
        ),
        pytest.raises(FlowExecutionError),
    ):
        run_flow(path, {"review": "text"}, "saved", base)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with patch("fdsx.core.engine.resume.display_wait_prompt", return_value="yes"):
        second = resume_flow("saved", base)
    assert second.status == "completed"
    assert second.results["assessment"]["answers"] == ANSWERS
    assert second.results["routed"] == "yes"
    assert len(wire[0]) == 1


@pytest.mark.parametrize("position", ["branch", "collect"])
@pytest.mark.parametrize("reachable", [True, False])
def test_parallel_checkpoint_evaluation_preflight(
    tmp_path, monkeypatch, wire, position, reachable
):
    """Resume real fan-out/fan-in checkpoints, not fabricated logical positions."""
    from fdsx.checkpoint.manager import CheckpointManager
    from fdsx.core.compiler import compile_flow
    from fdsx.core.loader import load_flow
    from fdsx.providers.base import ProviderResult

    data = definition()
    data["states"]["reassess"] = deepcopy(data["states"]["assess"])
    data["states"]["assess"]["next"] = "reviews"
    data["states"]["reviews"] = {
        "type": "parallel",
        "branches": [
            {
                "provider": "claude",
                "model": "fake",
                "prompt_template": "Review",
                "retry": 0,
            }
            for _ in range(2)
        ],
        "result_path": "$.reviews",
        "next": "reassess" if reachable else "route",
    }
    path = write_flow(tmp_path, data)
    base = tmp_path / ".fdsx"
    fake = ProviderResult(0, "branch review", "")
    # Fail inside the provider for a branch checkpoint, or immediately before
    # collector execution after both real branch nodes have completed.
    with patch(
        "fdsx.providers.claude._run_subprocess",
        side_effect=RuntimeError("branch interrupted")
        if position == "branch"
        else None,
        return_value=fake,
    ) as provider:
        if position == "collect":
            with (
                patch(
                    "fdsx.core.compiler.compile._create_collector_node",
                    return_value=Mock(
                        side_effect=RuntimeError("collector interrupted")
                    ),
                ),
                pytest.raises(FlowExecutionError),
            ):
                run_flow(path, {"review": "text"}, "parallel-saved", base)
            assert provider.call_count == 2
        else:
            with pytest.raises(FlowExecutionError):
                run_flow(path, {"review": "text"}, "parallel-saved", base)
            assert provider.called

    # Read the persisted graph snapshot without replacing its next/task data.
    flow, errors = load_flow(path, input_keys={"review"})
    assert flow is not None, errors
    saver = CheckpointManager(base).get_checkpointer()
    try:
        graph = compile_flow(flow, input_keys={"review"}, checkpointer=saver)
        snapshot = graph.graph.get_state(
            {"configurable": {"thread_id": "parallel-saved"}}
        )
        assert set(snapshot.next) == {f"_{position}_reviews"}
        assert {task.name for task in snapshot.tasks} == {f"_{position}_reviews"}
        saved_assessment = snapshot.values["assessment"]
        assert saved_assessment["answers"] == ANSWERS
    finally:
        saver.conn.close()

    assert len(wire[0]) == 1
    monkeypatch.delenv("TYPESAFE_API_KEY")
    before = Mock()
    with (
        patch("fdsx.providers.claude._run_subprocess", return_value=fake) as provider,
        patch("fdsx.core.compiler.compile.execute_hooks") as hooks,
    ):
        if reachable:
            with pytest.raises(
                FlowValidationError, match=r"State 'reassess'.*TYPESAFE_API_KEY"
            ):
                resume_flow("parallel-saved", base, before_start=before)
            before.assert_not_called()
            provider.assert_not_called()
            hooks.assert_not_called()
            assert len(wire[0]) == 1
            with patch("fdsx.cli.main.execute_run_hooks") as run_hooks:
                cli = CliRunner().invoke(
                    app, ["resume", "--thread-id", "parallel-saved"]
                )
                assert cli.exit_code != 0
                assert "TYPESAFE_API_KEY" in cli.stderr
                run_hooks.assert_not_called()
            provider.assert_not_called()
            hooks.assert_not_called()
            assert len(wire[0]) == 1
            monkeypatch.setenv("TYPESAFE_API_KEY", "fake-evaluation-key")

        result = resume_flow("parallel-saved", base, before_start=before)
        before.assert_called_once()
        assert result.status == "completed"
        assert result.results["assessment"] == saved_assessment
        assert result.results["routed"] == "yes"
        assert len(result.results["reviews"]) == 2
        assert provider.call_count == (2 if position == "branch" else 0)
        assert len(wire[0]) == (2 if reachable else 1)


@pytest.mark.parametrize("mode", ["choice", "default", "from"])
def test_resume_reachable_evaluation_requires_key_before_start(
    tmp_path, monkeypatch, wire, mode
):
    data = definition()
    data["states"]["assess"]["next"] = "pause"
    data["states"]["pause"] = {
        "type": "wait",
        "message": "Continue?",
        "choices": ["yes"],
        "result_path": "$.confirmation",
        "next": "route",
    }
    path = write_flow(tmp_path, data)
    base = tmp_path / ".fdsx"
    with (
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=RuntimeError("interrupted"),
        ),
        pytest.raises(FlowExecutionError),
    ):
        run_flow(path, {"review": "text"}, "saved", base)
    # Resume must reread the changed definition.
    if mode == "choice":
        data["states"]["route"]["choices"][0]["next"] = "assess"
    elif mode == "default":
        data["states"]["route"]["default"] = "assess"
    write_flow(tmp_path, data)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    before = Mock()
    with pytest.raises(FlowValidationError, match="TYPESAFE_API_KEY"):
        resume_flow(
            "saved",
            base,
            from_state="assess" if mode == "from" else None,
            before_start=before,
        )
    before.assert_not_called()
    assert len(wire[0]) == 1


def test_sdk_debug_and_evaluation_hooks_do_not_copy_content(
    tmp_path, monkeypatch, wire, caplog
):
    data = definition()
    data["states"]["assess"]["hooks"] = {
        "on_state_start": [{"command": "true"}],
        "on_state_end": [{"command": "true"}],
    }
    with caplog.at_level(logging.DEBUG, logger="typesafe_sdk"):
        run_flow(
            write_flow(tmp_path, data),
            {"review": "PRIVATE_MATERIAL"},
            "private",
            tmp_path / ".fdsx",
        )
    assert "PRIVATE_MATERIAL" not in caplog.text
    assert "fake-evaluation-key" not in caplog.text
    assert "probabilities" not in caplog.text
    files = list((tmp_path / ".fdsx/runs/private/hooks/assess").glob("*.json"))
    assert len(files) == 2
    for file in files:
        assert set(json.loads(file.read_text())) == {"state", "status"}


@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("key", [None, "  "])
def test_all_selected_tasks_preflight_before_any_start(
    tmp_path, monkeypatch, wire, auto, key
):
    data = definition()
    data["states"]["assess"]["input"] = {"document": {"ref": "$.task"}}
    workflows = tmp_path / ".fdsx/workflows"
    workflows.mkdir(parents=True)
    path = write_flow(workflows, data)
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
    (tasks / "001.yaml").write_text(
        "description: first\n" + ("" if auto else "workflow: plain.yaml\n")
    )
    (tasks / "002.yaml").write_text(
        "description: second\n" + ("" if auto else "workflow: flow.yaml\n")
    )
    if key is None:
        monkeypatch.delenv("TYPESAFE_API_KEY")
    else:
        monkeypatch.setenv("TYPESAFE_API_KEY", key)
    with (
        patch(
            "fdsx.core.selector.resolve_workflow_for_task", side_effect=[plain, path]
        ) as selector,
        patch("fdsx.cli.main.execute_run_hooks") as hooks,
        patch("fdsx.core.engine.run.execute_workflow_hooks") as flow_hooks,
        patch("fdsx.providers.system._run_subprocess") as provider,
    ):
        result = CliRunner().invoke(
            app, ["run", "--tasks-dir", str(tasks), "--auto-workflow"]
        )
        assert result.exit_code != 0, result.output
        assert "TYPESAFE_API_KEY" in result.stderr
        hooks.assert_not_called()
        flow_hooks.assert_not_called()
        provider.assert_not_called()
        assert selector.call_count == (2 if auto else 0)
    assert not wire[0]


def test_cli_resume_preflight_and_saved_only_execution(tmp_path, monkeypatch, wire):
    data = definition()
    data["states"]["assess"]["next"] = "pause"
    data["states"]["pause"] = {
        "type": "wait",
        "message": "Continue?",
        "choices": ["yes"],
        "result_path": "$.confirmation",
        "next": "route",
    }
    path = write_flow(tmp_path, data)
    base = tmp_path / ".fdsx"
    with (
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=RuntimeError("interrupted"),
        ),
        pytest.raises(FlowExecutionError),
    ):
        run_flow(path, {"review": "text"}, "saved", base)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with patch("fdsx.cli.main.execute_run_hooks") as hooks:
        denied = CliRunner().invoke(
            app, ["resume", "--thread-id", "saved", "--from", "assess"]
        )
        assert denied.exit_code != 0
        assert "TYPESAFE_API_KEY" in denied.stderr
        hooks.assert_not_called()
        allowed = CliRunner().invoke(app, ["resume", "--thread-id", "saved"])
        assert allowed.exit_code == 0, allowed.output
        assert [call.kwargs["event"] for call in hooks.call_args_list] == [
            "on_run_start",
            "on_run_end",
        ]
    assert len(wire[0]) == 1


def test_failed_evaluation_resume_resends_all_questions(tmp_path, wire):
    requests, payload = wire
    payload["answers"].pop("quality")
    path = write_flow(tmp_path)
    base = tmp_path / ".fdsx"
    with pytest.raises(FlowExecutionError):
        run_flow(path, {"review": "text"}, "retry", base)
    payload["answers"] = deepcopy(ANSWERS)
    result = resume_flow("retry", base)
    assert result.status == "completed"
    assert len(requests) == 2
    assert all(
        set(json.loads(options["content"])["questions"]) == set(QUESTIONS)
        for _, options in requests
    )
    assert result.results["assessment"]["answers"] == ANSWERS


def test_evaluation_end_hook_failure_can_require_reassessment(tmp_path, wire):
    data = definition()
    data["states"]["assess"]["hooks"] = {
        "on_state_end": [{"command": "exit 1", "on_failure": "abort"}],
    }
    path = write_flow(tmp_path, data)
    base = tmp_path / ".fdsx"
    with pytest.raises(RuntimeError):
        run_flow(path, {"review": "text"}, "hook", base)
    data["states"]["assess"].pop("hooks")
    write_flow(tmp_path, data)
    result = resume_flow("hook", base)
    assert result.status == "completed"
    assert len(wire[0]) == 2


def test_numeric_answers_drive_choice(tmp_path, wire):
    data = definition()
    data["states"]["route"]["choices"] = [
        {
            "variable": "$.assessment.answers.quality.score",
            "operator": "greater_than",
            "value": 1.5,
            "next": "noul",
        },
    ]
    data["states"]["noul"] = {
        "type": "choice",
        "choices": [
            {
                "variable": "$.assessment.answers.ambiguity.noul",
                "operator": "less_than",
                "value": 0.3,
                "next": "done",
            }
        ],
        "default": "wrong",
    }
    assert (
        run_flow(
            write_flow(tmp_path, data), {"review": "text"}, base_dir=tmp_path / ".fdsx"
        ).results["routed"]
        == "yes"
    )


def test_retry_budget_accounts_for_elapsed_request_time(tmp_path, monkeypatch, wire):
    calls, sleeps = [], []
    clock = [0.0]
    monkeypatch.setattr("tenacity.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("tenacity.nap.time.sleep", sleeps.append)

    def request(client, method, url, **kwargs):
        calls.append(url)
        clock[0] += 59
        return httpx2.Response(500, json={}, request=httpx2.Request(method, url))

    monkeypatch.setattr(httpx2.Client, "request", request)
    with pytest.raises(FlowExecutionError):
        run_flow(write_flow(tmp_path), {"review": "text"}, base_dir=tmp_path / ".fdsx")
    assert len(calls) == 1
    assert sleeps == []


def test_sdk_failure_never_exposes_exception_body_or_chain(
    tmp_path, monkeypatch, wire, caplog
):
    import traceback

    def request(client, method, url, **kwargs):
        return httpx2.Response(
            401, json={"error": "PRIVATE_ANSWER"}, request=httpx2.Request(method, url)
        )

    monkeypatch.setattr(httpx2.Client, "request", request)
    inputs = {"review": "PRIVATE_MATERIAL"}
    with (
        caplog.at_level(logging.DEBUG, logger="typesafe_sdk"),
        pytest.raises(FlowExecutionError) as error,
    ):
        run_flow(write_flow(tmp_path), inputs, base_dir=tmp_path / ".fdsx")
    rendered = "".join(traceback.format_exception(error.value))
    for secret in ("PRIVATE_ANSWER", "PRIVATE_MATERIAL", "fake-evaluation-key"):
        assert secret not in rendered + caplog.text


def test_documented_example_runs_offline(tmp_path, wire):
    example = (
        Path(__file__).resolve().parents[2]
        / "src/fdsx/examples/workflows/evaluate-review.yaml"
    )
    wire[1]["answers"]["action"] = {
        "type": "choice",
        "choice": "proceed",
        "probabilities": {"fix": 0.1, "investigate": 0.2, "proceed": 0.7},
        "confidence": 0.2,
    }
    wire[1]["answers"]["quality"]["legend"] = {
        "0": "Not met",
        "1": "Partially met",
        "2": "Fully met",
    }
    result = run_flow(
        example, {"review": "Explicit review"}, "example", tmp_path / ".fdsx"
    )
    assert result.status == "completed"
    record = json.loads((tmp_path / ".fdsx/runs/example/run.json").read_text())
    assert [entry["name"] for entry in record["states"]] == ["assess", "route", "done"]


def test_unused_evaluation_still_requires_key_but_plain_flow_does_not(
    tmp_path, monkeypatch, wire
):
    data = definition()
    data["start_at"] = "done"
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(FlowValidationError):
        run_flow(write_flow(tmp_path, data), base_dir=tmp_path / ".fdsx")
    data["states"] = {"done": data["states"]["done"]}
    assert (
        run_flow(write_flow(tmp_path, data), base_dir=tmp_path / ".fdsx").status
        == "completed"
    )
    assert wire[0] == []


@pytest.mark.parametrize("destination", ["_assessment", "review-score", "評価"])
def test_explicit_model_and_private_named_result(tmp_path, wire, destination):
    data = definition()
    data["states"]["assess"].update(
        model="jev-latest", result_path=f"$.{destination}", end=True
    )
    data["states"]["assess"].pop("next")
    data["states"] = {"assess": data["states"]["assess"]}
    result = run_flow(
        write_flow(tmp_path, data), {"review": "text"}, base_dir=tmp_path / ".fdsx"
    )
    assert result.results[destination]["model"] == {
        "requested": "jev-latest",
        "reported": "reported-model",
    }
    assert json.loads(wire[0][0][1]["content"])["model"] == "jev-latest"


def test_failed_evaluation_hook_files_are_summaries(tmp_path, wire):
    data = definition()
    data["states"]["assess"]["hooks"] = {
        "on_state_start": [{"command": "true"}],
        "on_state_end": [{"command": "true"}],
    }
    wire[1]["answers"].pop("quality")
    with pytest.raises(FlowExecutionError):
        run_flow(
            write_flow(tmp_path, data),
            {"review": "PRIVATE_MATERIAL"},
            "failed-hooks",
            tmp_path / ".fdsx",
        )
    folder = tmp_path / ".fdsx/runs/failed-hooks/hooks/assess"
    assert json.loads((folder / "input.json").read_text()) == {
        "state": "assess",
        "status": "starting",
    }
    assert json.loads((folder / "output.json").read_text()) == {
        "state": "assess",
        "status": "failed",
    }


def test_actual_loop_back_performs_a_new_evaluation(tmp_path, monkeypatch, wire):
    data = definition()
    data["states"]["route"] = {
        "type": "choice",
        "choices": [
            {
                "variable": "$.assessment.answers.ambiguity.noul",
                "operator": "greater_than",
                "value": 0.5,
                "next": "assess",
            }
        ],
        "default": "done",
    }
    original = httpx2.Client.request

    def request(client, method, url, **kwargs):
        wire[1]["answers"]["ambiguity"]["noul"] = 0.9 if not wire[0] else 0.1
        return original(client, method, url, **kwargs)

    monkeypatch.setattr(httpx2.Client, "request", request)
    result = run_flow(
        write_flow(tmp_path, data), {"review": "text"}, "loop", tmp_path / ".fdsx"
    )
    assert result.status == "completed"
    assert result.results["assessment"]["answers"]["ambiguity"]["noul"] == 0.1
    assert len(wire[0]) == 2
    record = json.loads((tmp_path / ".fdsx/runs/loop/run.json").read_text())
    assert [entry["name"] for entry in record["states"]].count("assess") == 2


@pytest.mark.parametrize(
    "path,key",
    [
        ('$.document[""]', ""),
        ('$.document["don\'t"]', "don't"),
        ("$.document['a.b']", "a.b"),
        ("$.文書.text", "text"),
    ],
)
def test_quoted_and_unicode_refs_resolve_exact_material(tmp_path, wire, path, key):
    data = definition()
    data["states"]["assess"]["input"] = {"document": {"ref": path}}
    root = "文書" if path.startswith("$.文書") else "document"
    result = run_flow(
        write_flow(tmp_path, data),
        {root: {key: "selected", "unselected": "PRIVATE"}},
        base_dir=tmp_path / ".fdsx",
    )
    assert result.status == "completed"
    assert json.loads(json.loads(wire[0][0][1]["content"])["state"]) == {
        "document": "selected"
    }


def test_ambiguous_quoted_ref_cannot_select_a_different_key(tmp_path, wire):
    data = definition()
    data["states"]["assess"]["input"] = {
        "document": {"ref": "$.document[\"'private'\"]"}
    }
    with pytest.raises(FlowValidationError, match="unsupported by the resolver"):
        run_flow(
            write_flow(tmp_path, data),
            {"document": {"private": "DO NOT SEND"}},
            base_dir=tmp_path / ".fdsx",
        )
    assert wire[0] == []

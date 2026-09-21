"""Local control flow through YAML, the engine, and offline provider boundaries."""

import json
import shlex
from collections import Counter
from pathlib import Path
from unittest.mock import Mock

import httpx2
import pytest
import yaml

from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.engine.errors import FlowExecutionError
from fdsx.core.engine.validate import FlowValidationError
from fdsx.providers.base import ProviderResult

ACTIONS = {"approved": "Complete", "prose": "Fix prose", "code": "Fix code"}


def local(kind="evaluate", subject="{item}"):
    question = {"type": "choice", "instructions": "Choose", "criteria": ACTIONS}
    if kind == "classifier":
        assess = {
            "type": kind,
            "input": {"draft": {"ref": "$.draft"}},
            "question": question,
            "result_path": "$.assessment",
        }
        variable = "$.assessment.answer"
    elif kind == "evaluate":
        assess = {
            "type": kind,
            "evaluator": "jev",
            "input": {"draft": {"ref": "$.draft"}},
            "questions": {"action": question},
            "result_path": "$.assessment",
        }
        variable = "$.assessment.answers.action.choice"
    else:
        assess = {
            "type": "task",
            "profile": "judge",
            "prompt_template": "{draft}",
            "structured_output": {
                "schema": "answer.json",
                "result_path": "$.assessment",
                "allow_extra_fields": False,
            },
        }
        variable = "$.assessment.action"
    assess["next"] = "route"
    return {
        "start_at": "generate",
        "output_path": "$.draft",
        "max_loop": 3,
        "states": {
            "generate": {
                "type": "task",
                "provider": "system",
                "command": f"echo {subject}",
                "result_path": "$.draft",
                "next": "assess",
            },
            "assess": assess,
            "route": {
                "type": "choice",
                "choices": [
                    {
                        "variable": variable,
                        "operator": "equals",
                        "value": "approved",
                        "next": "done",
                    },
                    {
                        "variable": variable,
                        "operator": "equals",
                        "value": "prose",
                        "next": "fix_prose",
                    },
                ],
                "default": "fix_code",
            },
            "fix_prose": {
                "type": "task",
                "provider": "system",
                "command": "echo fixed-prose",
                "result_path": "$.draft",
                "next": "assess",
            },
            "fix_code": {
                "type": "task",
                "provider": "system",
                "command": "echo fixed-code",
                "result_path": "$.draft",
                "next": "assess",
            },
            "done": {"type": "pass", "end": True},
        },
    }


def workflow(container, kind="evaluate", subjects=("A", "B", "C")):
    if container == "map":
        work = {
            "type": "map",
            "items_path": "$.items",
            "iterator": local(kind),
            "fail_fast": False,
        }
    else:
        work = {
            "type": "parallel",
            "branches": [
                {"name": subject, "workflow": local(kind, subject)}
                for subject in subjects
            ],
            "min_success": 0,
        }
    work.update(result_path="$.outcomes", end=True)
    return {
        "name": "local-test",
        "description": "Isolated repairs",
        "start_at": "work",
        "profiles": {"judge": {"provider": "jev", "model": "jev-1.13.0"}},
        "states": {"work": work},
    }


def definitions(data):
    work = data["states"]["work"]
    return (
        [work["iterator"]]
        if work["type"] == "map"
        else [b["workflow"] for b in work["branches"]]
    )


def write(tmp_path, data):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "description": "Choose",
                "oneOf": [
                    {"const": key, "description": value}
                    for key, value in ACTIONS.items()
                ],
            }
        },
    }
    (tmp_path / "answer.json").write_text(json.dumps(schema))
    path = tmp_path / "flow.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.fixture
def offline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-key")
    requests = []
    options = {"repeat": False, "invalid": False, "status": 200, "confidence": 1.0}

    def request(client, method, url, **kwargs):
        body = json.loads(kwargs["content"])
        requests.append(body)
        material = json.loads(body["state"])
        draft = next(iter(material.values()))
        action = (
            "prose"
            if options["repeat"] and draft != "A"
            else {"B": "prose", "C": "code"}.get(draft, "approved")
        )
        key = next(iter(body["questions"]))
        answer = {
            "type": "choice",
            "choice": "invalid" if options["invalid"] else action,
            "confidence": options["confidence"],
            "probabilities": {k: float(k == action) for k in ACTIONS},
        }
        return httpx2.Response(
            options["status"],
            json={"model": "reported", "usage": {}, "answers": {key: answer}},
            request=httpx2.Request(method, url),
        )

    monkeypatch.setattr(httpx2.Client, "request", request)
    system = Mock(
        side_effect=lambda **kw: ProviderResult(
            0, " ".join(shlex.split(kw["args"][0])[1:]), ""
        )
    )
    monkeypatch.setattr("fdsx.providers.system._run_subprocess", system)
    llm = Mock(
        return_value=ProviderResult(
            0, json.dumps({"answer": "approved", "reason": "Ready"}), ""
        )
    )
    monkeypatch.setattr("fdsx.providers.claude._run_subprocess", llm)
    return requests, options, system, llm


@pytest.mark.parametrize("concurrency", [1, 3])
@pytest.mark.parametrize("container", ["map", "parallel"])
def test_local_result_files_preserve_parent_and_sibling_artifacts(
    tmp_path, offline, container, concurrency
):
    data = workflow(container)
    if container == "map":
        data["states"]["work"]["max_concurrency"] = concurrency
    data["start_at"] = "parent"
    data["states"]["parent"] = {
        "type": "task",
        "provider": "system",
        "command": "echo parent",
        "result_file": "$.artifact",
        "next": "work",
    }
    for definition in definitions(data):
        generate = definition["states"]["generate"]
        generate["result_file"] = "$.artifact"
        generate["command"] += " {run_path}"
        generate["next"] = "done"
        definition["states"] = {
            "generate": generate,
            "done": {"type": "pass", "end": True},
        }
        definition["output_path"] = "$.artifact"
    result = run_flow(
        write(tmp_path, data),
        {"items": ["A", "B", "C"]},
        base_dir=tmp_path / ".fdsx",
    )
    assert result.status == "completed"
    parent = Path(result.results["artifact"])
    paths = [Path(outcome["output"]) for outcome in result.results["outcomes"]]
    assert len(set([parent, *paths])) == 4
    assert parent.read_text() == "parent"
    run_dir = parent.parent.parent
    assert [path.read_text() for path in paths] == [
        f"{subject} {run_dir}" for subject in ("A", "B", "C")
    ]
    assert all(path.is_relative_to(run_dir) for path in paths)


@pytest.mark.parametrize("container", ["map", "parallel"])
@pytest.mark.parametrize("kind", ["classifier", "evaluate", "task"])
@pytest.mark.parametrize("concurrency", [1, 3])
def test_repairs_route_locally_and_use_latest_material(
    tmp_path, offline, container, kind, concurrency
):
    data = workflow(container, kind)
    if container == "map":
        data["states"]["work"]["max_concurrency"] = concurrency
    result = run_flow(
        write(tmp_path, data),
        {"items": ["A", "B", "C"], "draft": "PARENT_PRIVATE"},
        base_dir=tmp_path / ".fdsx",
    )
    assert result.status == "completed"
    assert [r["output"] for r in result.results["outcomes"]] == [
        "A",
        "fixed-prose",
        "fixed-code",
    ]
    assert all(r["exit_code"] == 0 for r in result.results["outcomes"])
    materials = [next(iter(json.loads(r["state"]).values())) for r in offline[0]]
    assert Counter(materials) == Counter(["A", "B", "C", "fixed-prose", "fixed-code"])
    assert "PARENT_PRIVATE" not in json.dumps(offline[0])
    assert offline[2].call_count == 5
    assert offline[3].call_count == 0
    logs = list((tmp_path / ".fdsx" / "runs").glob("*/run.json"))
    record = json.loads(logs[0].read_text())
    assert len(record["local_workflows"]) == 3
    assert len({r["scope"] for r in record["local_workflows"]}) == 3


@pytest.mark.parametrize("container", ["map", "parallel"])
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("limit", ["max_loop", "max_iterations", "fail"])
def test_local_failures_finish_remaining_subjects_before_policy(
    tmp_path, offline, container, strict, limit
):
    data = workflow(container, subjects=("B", "A"))
    offline[1]["repeat"] = True
    for definition in definitions(data):
        definition["max_loop"] = 2
        if limit == "max_iterations":
            definition["states"]["fix_prose"]["max_iterations"] = 1
            definition["max_loop"] = 4
        if limit == "fail":
            definition["states"]["fix_prose"] = {
                "type": "fail",
                "error": "Rejected",
                "cause": "Cannot repair",
            }
    if strict:
        if container == "parallel":
            data["states"]["work"].pop("min_success")
        else:
            data["states"]["work"].pop("end")
            data["states"]["work"]["next"] = "all_success"
            data["states"].update(
                {
                    "all_success": {
                        "type": "pass",
                        "aggregate": {
                            "source": "$.outcomes",
                            "field": "exit_code",
                            "strategy": "all",
                            "match": "0",
                            "no_match": "1",
                            "result_path": "$.failed",
                        },
                        "next": "route",
                    },
                    "route": {
                        "type": "choice",
                        "choices": [
                            {
                                "variable": "$.failed",
                                "operator": "equals",
                                "value": "1",
                                "next": "fail",
                            }
                        ],
                    },
                    "fail": {
                        "type": "fail",
                        "error": "PartialFailure",
                        "cause": "One or more failures",
                    },
                }
            )
    path = write(tmp_path, data)
    if strict and container == "parallel":
        with pytest.raises(FlowExecutionError):
            run_flow(path, {"items": ["B", "A"]}, base_dir=tmp_path / ".fdsx")
    else:
        result = run_flow(path, {"items": ["B", "A"]}, base_dir=tmp_path / ".fdsx")
        assert result.status == ("aborted" if strict else "completed")
        outcomes = result.results["outcomes"]
        assert [r["exit_code"] for r in outcomes] == [1, 0]
        assert (
            outcomes[0]["error"]
            == {
                "max_loop": "max_loop_reached",
                "max_iterations": "MaxIterationsReachedError",
                "fail": "Rejected",
            }[limit]
        )
        assert outcomes[1]["output"] == "A"
    materials = [next(iter(json.loads(r["state"]).values())) for r in offline[0]]
    assert materials.count("A") == 1
    assert len(materials) == {"max_loop": 3, "max_iterations": 3, "fail": 2}[limit]


@pytest.mark.parametrize("container", ["map", "parallel"])
@pytest.mark.parametrize(
    "damage",
    [
        "start",
        "target",
        "wait",
        "map",
        "parallel",
        "question",
        "profile",
        "retry",
        "schema",
        "key",
    ],
)
def test_invalid_local_definitions_fail_before_provider_or_hooks(
    tmp_path, monkeypatch, offline, container, damage
):
    data = workflow(
        container, "task" if damage in {"profile", "retry", "schema"} else "evaluate"
    )
    definition = definitions(data)[0]
    if damage == "start":
        definition["start_at"] = "absent"
    elif damage == "target":
        definition["states"]["generate"]["next"] = "work"
    elif damage in {"wait", "map", "parallel"}:
        definition["states"]["done"] = {"type": damage}
    elif damage == "question":
        definition["states"]["assess"]["questions"] = {}
    elif damage == "profile":
        definition["states"]["assess"]["profile"] = "missing"
    elif damage == "retry":
        definition["states"]["assess"]["retry"] = 2
    elif damage == "schema":
        definition["states"]["assess"]["structured_output"]["schema"] = "missing.json"
    else:
        monkeypatch.delenv("TYPESAFE_API_KEY")
    hooks = Mock()
    monkeypatch.setattr("fdsx.core.engine.run.execute_workflow_hooks", hooks)
    with pytest.raises(FlowValidationError):
        run_flow(write(tmp_path, data), {"items": ["A"]}, base_dir=tmp_path / ".fdsx")
    assert not offline[0]
    offline[2].assert_not_called()
    hooks.assert_not_called()


@pytest.mark.parametrize("container", ["map", "parallel"])
@pytest.mark.parametrize("mode", ["fallback", "invalid", "transport"])
def test_classifier_fallback_conditions_are_preserved(
    tmp_path, offline, container, mode
):
    data = workflow(container, "classifier", subjects=("A",))
    for definition in definitions(data):
        definition["states"]["assess"].update(
            acceptance={"confidence": 0.8},
            fallback={"provider": "claude", "model": "test"},
        )
    offline[1]["confidence"] = 0.5
    offline[1]["invalid"] = mode == "invalid"
    offline[1]["status"] = 401 if mode == "transport" else 200
    result = run_flow(
        write(tmp_path, data), {"items": ["A"]}, base_dir=tmp_path / ".fdsx"
    )
    assert result.status == "completed"
    assert result.results["outcomes"][0]["exit_code"] == (
        0 if mode == "fallback" else 1
    )
    assert offline[3].call_count == (1 if mode == "fallback" else 0)
    assert len(offline[0]) == 1


@pytest.mark.parametrize("container", ["map", "parallel"])
def test_nested_results_do_not_mutate_parent_and_gate_uses_export(
    tmp_path, offline, container
):
    data = workflow(container, subjects=("A", "B"))
    for definition in definitions(data):
        definition["states"]["done"]["parameters"] = {
            "$.shared.value": "local",
            "$.export": {"approved": True},
        }
        definition["output_path"] = "$.export"
    work = data["states"]["work"]
    work.pop("end")
    work["next"] = "observe"
    if container == "parallel":
        work.pop("min_success")
        work["gate"] = {
            "required": ["A", "B"],
            "field": "$.output.approved",
            "expected": True,
            "result_path": "$.accepted",
        }
    data["states"]["observe"] = {
        "type": "pass",
        "parameters": {"$.parent_value": "{shared.value}"},
        "end": True,
    }
    result = run_flow(
        write(tmp_path, data),
        {"items": ["A", "B"], "shared": {"value": "parent"}},
        base_dir=tmp_path / ".fdsx",
    )
    assert result.results["parent_value"] == "parent"
    assert [r["output"] for r in result.results["outcomes"]] == [
        {"approved": True},
        {"approved": True},
    ]
    if container == "parallel":
        assert result.results["accepted"] is True


@pytest.mark.parametrize("container", ["map", "parallel"])
def test_resume_restarts_pending_local_graph_from_entry(tmp_path, offline, container):
    data = workflow(container, subjects=("A", "B"))
    path = write(tmp_path, data)
    base = tmp_path / ".fdsx"
    original = offline[2].side_effect
    interrupted = False

    def interrupt(**kwargs):
        nonlocal interrupted
        if "fixed-prose" in kwargs["args"][0] and not interrupted:
            interrupted = True
            raise SystemExit(130)
        return original(**kwargs)

    offline[2].side_effect = interrupt
    with pytest.raises(SystemExit):
        run_flow(path, {"items": ["A", "B"]}, thread_id="local-resume", base_dir=base)
    prior_a = sum(json.loads(r["state"]).get("draft") == "A" for r in offline[0])
    offline[2].side_effect = original
    result = resume_flow("local-resume", base, path)
    assert result.status == "completed"
    assert [r["output"] for r in result.results["outcomes"]] == ["A", "fixed-prose"]
    materials = [json.loads(r["state"])["draft"] for r in offline[0]]
    assert materials.count("B") == 2
    assert materials.count("fixed-prose") == 1
    if container == "map":
        assert materials.count("A") == prior_a == 1
    record = json.loads((base / "runs" / "local-resume" / "run.json").read_text())
    assert any(r["scope"].endswith(".1") for r in record["local_workflows"])


@pytest.mark.parametrize("container", ["map", "parallel"])
def test_explicit_recovery_replays_local_graph_and_preflights_key(
    tmp_path, monkeypatch, offline, container
):
    data = workflow(container, subjects=("A",))
    data["states"]["work"].pop("end")
    data["states"]["work"]["next"] = "stop"
    data["states"]["stop"] = {
        "type": "fail",
        "error": "Stopped",
        "cause": "Recovery test",
    }
    path = write(tmp_path, data)
    base = tmp_path / ".fdsx"
    run_flow(path, {"items": ["A"]}, thread_id="local-recovery", base_dir=base)
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(FlowValidationError, match="TYPESAFE_API_KEY"):
        resume_flow("local-recovery", base, path, from_state="work")
    assert len(offline[0]) == 1
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-key")
    result = resume_flow("local-recovery", base, path, from_state="work")
    assert result.status == "aborted"
    assert len(offline[0]) == 2


def test_map_new_parent_visit_does_not_reuse_previous_local_results(tmp_path, offline):
    data = workflow("map")
    work = data["states"]["work"]
    work.pop("end")
    work["next"] = "repeat"
    data["max_loop"] = 2
    data["states"]["repeat"] = {
        "type": "choice",
        "choices": [
            {"variable": "$.stop", "operator": "equals", "value": True, "next": "done"}
        ],
        "default": "work",
    }
    data["states"]["done"] = {"type": "pass", "end": True}
    result = run_flow(
        write(tmp_path, data), {"items": ["A"]}, base_dir=tmp_path / ".fdsx"
    )
    assert result.status == "max_loop_reached"
    assert len(offline[0]) == 2


@pytest.mark.parametrize("container", ["map", "parallel"])
def test_missing_output_is_collected_as_local_failure(tmp_path, offline, container):
    data = workflow(container, subjects=("A",))
    for definition in definitions(data):
        definition["output_path"] = "$.absent"
    result = run_flow(
        write(tmp_path, data), {"items": ["A"]}, base_dir=tmp_path / ".fdsx"
    )
    assert result.status == "completed"
    assert result.results["outcomes"][0]["error"] == "missing_local_output"


def test_map_fail_fast_stops_after_first_local_failure(tmp_path, offline):
    data = workflow("map")
    data["states"]["work"]["fail_fast"] = True
    definitions(data)[0]["states"]["fix_prose"] = {
        "type": "fail",
        "error": "Rejected",
        "cause": "Cannot repair",
    }
    with pytest.raises(FlowExecutionError, match="Rejected"):
        run_flow(
            write(tmp_path, data), {"items": ["B", "A"]}, base_dir=tmp_path / ".fdsx"
        )
    assert len(offline[0]) == 1


@pytest.mark.parametrize("container", ["map", "parallel"])
@pytest.mark.parametrize("profile", [False, True])
def test_jev_task_prompt_file_and_direct_or_config_profile(
    tmp_path, offline, container, profile
):
    data = workflow(container, "task", subjects=("A",))
    for definition in definitions(data):
        assess = definition["states"]["assess"]
        assess.pop("prompt_template")
        assess["prompt_file"] = "judge.txt"
        if not profile:
            assess.pop("profile")
            assess.update(provider="jev", model="jev-1.13.0")
    (tmp_path / "judge.txt").write_text("{draft}")
    if profile:
        (tmp_path / ".fdsx").mkdir()
        (tmp_path / ".fdsx" / "config.yaml").write_text(
            yaml.safe_dump({"profiles": data.pop("profiles")})
        )
    result = run_flow(
        write(tmp_path, data), {"items": ["A"]}, base_dir=tmp_path / ".fdsx"
    )
    assert result.status == "completed"
    assert result.results["outcomes"][0]["output"] == "A"
    assert json.loads(offline[0][0]["state"]) == {"prompt": "A"}


@pytest.mark.parametrize("container", ["map", "parallel"])
def test_local_provider_failure_is_collected_without_replaying_other_subjects(
    tmp_path, offline, container
):
    data = workflow(container, subjects=("B", "A"))
    for definition in definitions(data):
        definition["states"]["generate"]["retry"] = 0
    original = offline[2].side_effect
    offline[2].side_effect = lambda **kwargs: (
        ProviderResult(2, "", "provider failure")
        if kwargs["args"] == ["echo B"]
        else original(**kwargs)
    )
    result = run_flow(
        write(tmp_path, data), {"items": ["B", "A"]}, base_dir=tmp_path / ".fdsx"
    )
    assert result.status == "completed"
    assert [r["exit_code"] for r in result.results["outcomes"]] == [1, 0]
    assert offline[2].call_count == 2
    assert len(offline[0]) == 1


@pytest.mark.parametrize("container", ["map", "parallel"])
def test_documented_workflow_example_loads_and_runs(tmp_path, offline, container):
    from pathlib import Path

    document = Path(__file__).resolve().parents[2] / "docs" / "local-workflows.md"
    blocks = [
        part.split("```", 1)[0] for part in document.read_text().split("```yaml\n")[1:]
    ]
    data = yaml.safe_load(blocks[1])
    if container == "parallel":
        definition = data["states"]["work"]["iterator"]
        work = yaml.safe_load(blocks[2].replace("*local", "{}"))["work"]
        for branch in work["branches"]:
            branch["workflow"] = definition
        data["states"] = {"work": work}
    result = run_flow(
        write(tmp_path, data), {"items": ["A"]}, base_dir=tmp_path / ".fdsx"
    )
    assert result.status == "completed"
    assert all(r["output"] == "initial" for r in result.results["outcomes"])


@pytest.mark.parametrize("container", ["map", "parallel"])
def test_each_failing_subject_gets_its_own_inherited_loop_budget(
    tmp_path, offline, container
):
    data = workflow(container, subjects=("B", "C", "A"))
    data["max_loop"] = 2
    for definition in definitions(data):
        definition.pop("max_loop")
    offline[1]["repeat"] = True
    result = run_flow(
        write(tmp_path, data), {"items": ["B", "C", "A"]}, base_dir=tmp_path / ".fdsx"
    )
    assert result.status == "completed"
    assert [r["exit_code"] for r in result.results["outcomes"]] == [1, 1, 0]
    materials = [json.loads(r["state"])["draft"] for r in offline[0]]
    assert Counter(materials) == Counter(["B", "C", "A", "fixed-prose", "fixed-prose"])


@pytest.mark.parametrize("container", ["map", "parallel"])
@pytest.mark.parametrize("kind", ["classifier", "evaluate", "task"])
def test_local_evaluation_hooks_only_receive_summary(
    tmp_path, monkeypatch, offline, container, kind
):
    data = workflow(container, kind, subjects=("A",))
    for definition in definitions(data):
        definition["states"]["assess"]["hooks"] = {
            "on_state_start": [{"command": "true"}],
            "on_state_end": [{"command": "true"}],
        }
    hook = Mock()
    monkeypatch.setattr("fdsx.core.compiler.compile.execute_hooks", hook)
    result = run_flow(
        write(tmp_path, data),
        {"items": ["A"], "private": "NEVER_SEND"},
        base_dir=tmp_path / ".fdsx",
    )
    assert result.status == "completed"
    assert hook.call_count == 2
    for call in hook.call_args_list:
        summary = json.loads(call.kwargs["data_path"].read_text())
        assert set(summary) == {"state", "status"}
        assert summary["state"].endswith(".0.assess")
    assert "NEVER_SEND" not in json.dumps(offline[0])


def test_incomplete_local_form_cannot_silently_become_legacy_iterator(
    tmp_path, offline
):
    data = workflow("map")
    data["states"]["work"]["iterator"] = {
        "output_path": "$.draft",
        "states": [
            {
                "name": "generate",
                "provider": "system",
                "command": "echo A",
                "result_path": "$.draft",
            }
        ],
    }
    with pytest.raises(FlowValidationError):
        run_flow(write(tmp_path, data), {"items": ["A"]}, base_dir=tmp_path / ".fdsx")
    offline[2].assert_not_called()


@pytest.mark.parametrize("container", ["map", "parallel"])
def test_local_merge_contract_conflict_fails_before_execution(
    tmp_path, offline, container
):
    data = workflow(container, subjects=("A",))
    for definition in definitions(data):
        definition["states"] = {
            name: {
                "type": "task",
                "provider": "system",
                "command": "echo unused",
                "structured_output": {
                    "schema": "objects.json",
                    "result_path": "$.objects",
                    "merge": {"strategy": "upsert", "key": key},
                },
                **transition,
            }
            for name, key, transition in [
                ("generate", "id", {"next": "replace"}),
                ("replace", "other", {"end": True}),
            ]
        }
    with pytest.raises(FlowValidationError, match="identical merge configuration"):
        run_flow(write(tmp_path, data), {"items": ["A"]}, base_dir=tmp_path / ".fdsx")
    offline[2].assert_not_called()

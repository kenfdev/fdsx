"""Native-format fixtures establish FDSX's contract, not installed Pi semantics."""

import json
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

import pytest
import yaml
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.loader import load_flow
from fdsx.providers.base import ProviderResult, SessionRequest
from fdsx.providers.pi import PiProvider


def task(prompt, **kwargs):
    return dict(
        type="task",
        provider="pi",
        model="vendor/model",
        prompt_template=prompt,
        retry=0,
        **kwargs,
    )


def write_flow(tmp_path, states=None, **kwargs):
    path = tmp_path / "flow.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                name="forks",
                description="Native fork fixture",
                start_at="plan",
                states=states
                or {
                    "plan": task("plan", result_path="$.plan", next="implement"),
                    "implement": task("implement", fork_from="plan", next="review"),
                    "review": task(
                        "review", fork_from="plan", result_path="$.review", end=True
                    ),
                },
                **kwargs,
            )
        )
    )
    return path


class NativeFixture:
    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.executions = []
        self.forks = []
        self.responses = {}
        self.version = "0.85.1"
        self.fork_failure = False
        self.on_execute = None

    def __call__(self, **kwargs):
        args = kwargs["args"]
        if "--version" in args:
            return ProviderResult(0, self.version, "")
        if "-e" in args:
            request = json.loads(kwargs["env"]["FDSX_PI_FORK_REQUEST"])
            if self.fork_failure:
                return ProviderResult(1, "", "private conversation must not leak")
            entries = [
                json.loads(line)
                for line in Path(request["path"]).read_text().splitlines()
            ]
            selected = []
            for entry in entries[1:]:
                selected.append(entry)
                if entry["id"] == request["endpoint"]:
                    break
            path = Path(request["directory"]) / "child.jsonl"
            header = dict(
                type="session",
                version=3,
                id=str(uuid5(NAMESPACE_URL, str(path))),
                timestamp="2026-09-16T00:00:00Z",
                cwd=str(Path.cwd()),
                parentSession=request["path"],
            )
            path.write_text(
                "\n".join(json.dumps(e) for e in [header, *selected]) + "\n"
            )
            self.forks.append((request, path))
            return ProviderResult(0, json.dumps({"path": str(path)}), "")
        prompt = args[2] if not args[2].startswith("--") else kwargs["stdin_data"]
        action = prompt.split("\n")[0]
        directory = Path(args[args.index("--session-dir") + 1])
        if "--session" in args:
            path = Path(args[args.index("--session") + 1])
            entries = [json.loads(line) for line in path.read_text().splitlines()]
        else:
            path = directory / "source.jsonl"
            entries = [
                dict(
                    type="session",
                    version=3,
                    id=str(uuid5(NAMESPACE_URL, f"source-{len(self.executions)}")),
                    timestamp="2026-09-16T00:00:00Z",
                    cwd=str(Path.cwd()),
                )
            ]
        inherited = [
            block["text"]
            for entry in entries[1:]
            for block in entry.get("message", {}).get("content", [])
            if block.get("type") == "text"
        ]
        number = len(self.executions)
        self.executions.append((action, path, inherited, args, prompt))
        if self.on_execute:
            self.on_execute(action, path)
        if action == "implement":
            (self.tmp_path / "code.txt").write_text("implemented")
        if action == "review":
            assert (self.tmp_path / "code.txt").read_text() == "implemented"
        entries.append(
            dict(
                type="message",
                id=f"entry-{number}",
                parentId=entries[-1].get("id") if len(entries) > 1 else None,
                timestamp="2026-09-16T00:00:00Z",
                message=dict(
                    role="assistant",
                    content=[{"type": "text", "text": action}],
                    api="fixture",
                    provider="fixture",
                    model="fixture",
                    stopReason="stop",
                    timestamp=0,
                ),
            )
        )
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        choices = self.responses.get(action, [])
        result = (
            choices.pop(0) if choices else ProviderResult(0, action + " output", "")
        )
        if kwargs.get("output_callback"):
            kwargs["output_callback"](result.stdout)
        if kwargs.get("stderr_callback"):
            kwargs["stderr_callback"](result.stderr)
        return result


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "pi"))
    fixture = NativeFixture(tmp_path)
    with (
        patch("fdsx.providers.pi.shutil.which", return_value="/fixture/pi"),
        patch("fdsx.providers.pi._run_subprocess", side_effect=fixture),
        patch("fdsx.core.compiler.execution.time.sleep"),
    ):
        yield fixture


@pytest.mark.parametrize("forked", [False, True])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_native_session_diagnostics_never_reach_callbacks_or_results(
    tmp_path, native, caplog, capsys, forked, exit_code
):
    provider = PiProvider()
    source = provider.execute(
        "plan", session_request=SessionRequest(state_name="plan")
    ).session_reference
    native.responses["implement"] = [
        ProviderResult(exit_code, "task output", "PRIVATE conversation")
    ]
    streamed = []
    output = []
    result = provider.execute(
        "implement",
        session_request=SessionRequest(
            state_name="implement", source=source if forked else None
        ),
        stderr_callback=streamed.append,
        output_callback=output.append,
    )
    assert result.exit_code == exit_code
    assert result.stdout == "task output"
    assert output == ["task output"]
    if exit_code:
        assert streamed == [result.stderr]
        assert "State 'implement'" in result.stderr
        assert "session availability" in result.stderr
    else:
        assert streamed == []
        assert result.stderr == ""
        assert result.session_reference is not None
    captured = capsys.readouterr()
    assert "PRIVATE" not in repr(streamed) + result.stderr + caplog.text
    assert "PRIVATE" not in captured.out + captured.err
    assert len(native.executions) == 2
    assert len(native.forks) == int(forked)


def test_fork_execution_failure_does_not_leak_to_workflow_logs(
    tmp_path, native, caplog, capsys
):
    native.responses["implement"] = [ProviderResult(1, "", "PRIVATE conversation")]
    with pytest.raises(RuntimeError, match="native session execution failed") as error:
        run_flow(write_flow(tmp_path), base_dir=tmp_path / ".fdsx")
    captured = capsys.readouterr()
    assert "PRIVATE" not in str(error.value) + caplog.text
    assert "PRIVATE" not in captured.out + captured.err
    for log in (tmp_path / ".fdsx").rglob("*.log"):
        assert "PRIVATE" not in log.read_text()
    assert [execution[0] for execution in native.executions] == ["plan", "implement"]
    assert len(native.forks) == 1


def test_independent_children_current_files_and_chained_source(tmp_path, native):
    path = write_flow(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["states"]["review"].pop("end")
    data["states"]["review"]["next"] = "followup"
    data["states"]["followup"] = task("followup", fork_from="review", end=True)
    path.write_text(yaml.safe_dump(data))
    result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    assert result.results == {"plan": "plan output", "review": "review output"}
    assert [call[2] for call in native.executions] == [
        [],
        ["plan"],
        ["plan"],
        ["plan", "review"],
    ]
    assert native.forks[0][0]["path"] == native.forks[1][0]["path"]
    assert native.forks[0][1] != native.forks[1][1]
    assert native.forks[2][0]["path"] == str(native.executions[2][1])


@pytest.mark.parametrize("structured", [False, True])
def test_retries_fork_original_endpoint_with_model_escalation(
    tmp_path, native, structured
):
    destination = task("implement", fork_from="plan", end=True)
    destination["retry"] = 1
    if structured:
        (tmp_path / "schema.json").write_text(
            '{"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"}}}'
        )
        destination["structured_output"] = {
            "schema": "schema.json",
            "result_path": "$.answer",
        }
        native.responses["implement"] = [
            ProviderResult(0, "invalid", ""),
            ProviderResult(0, '{"ok":true}', ""),
        ]
    else:
        native.responses["implement"] = [
            ProviderResult(1, "", "failed"),
            ProviderResult(0, "fixed", ""),
        ]
    path = write_flow(
        tmp_path,
        {"plan": task("plan", next="implement"), "implement": destination},
        retry_escalation={"provider": "pi", "model": "other-vendor/model"},
    )
    result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    assert len(native.forks) == 2
    assert native.forks[0][0]["endpoint"] == native.forks[1][0]["endpoint"]
    assert native.forks[0][1] != native.forks[1][1]
    assert [call[2] for call in native.executions[1:]] == [["plan"], ["plan"]]
    assert "other-vendor/model" in native.executions[-1][3]
    assert ("Correct this validation" in native.executions[-1][4]) == structured


def test_replanning_uses_latest_completion(tmp_path, native):
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="implement"),
            "implement": task("implement", fork_from="plan", next="route"),
            "route": {
                "type": "choice",
                "choices": [
                    {
                        "variable": "$._state_iterations.plan",
                        "operator": "less_than",
                        "value": 2,
                        "next": "plan",
                    }
                ],
                "default": "done",
            },
            "done": {"type": "pass", "end": True},
        },
    )
    run_flow(path, base_dir=tmp_path / ".fdsx")
    assert len(native.forks) == 2
    assert native.forks[0][0]["path"] != native.forks[1][0]["path"]
    assert native.forks[1][0]["endpoint"] == "entry-2"


def interrupted_flow(tmp_path, native):
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="wait"),
            "wait": {
                "type": "wait",
                "mode": "prompt",
                "message": "Continue",
                "choices": ["yes"],
                "result_path": "$.approval",
                "next": "implement",
            },
            "implement": task("implement", fork_from="plan", end=True),
        },
    )
    with (
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=RuntimeError("interrupted"),
        ),
        pytest.raises(RuntimeError),
    ):
        run_flow(path, thread_id="resume-fork", base_dir=tmp_path / ".fdsx")
    assert len(native.executions) == 1
    return path


def test_resume_restores_reference_without_replanning(tmp_path, native):
    path = interrupted_flow(tmp_path, native)
    with patch("builtins.input", return_value="1"):
        result = resume_flow("resume-fork", tmp_path / ".fdsx", path)
    assert result.status == "completed"
    assert [call[0] for call in native.executions] == ["plan", "implement"]
    assert native.executions[1][2] == ["plan"]


@pytest.mark.parametrize(
    "damage", ["missing", "corrupt", "changed", "version", "deep_json"]
)
def test_resume_native_history_damage_fails_before_destination(
    tmp_path, native, damage, capsys, caplog
):
    path = interrupted_flow(tmp_path, native)
    session = native.executions[0][1]
    if damage == "missing":
        session.unlink()
    elif damage == "corrupt":
        session.write_text("not json private conversation")
    elif damage == "deep_json":
        depth = sys.getrecursionlimit() + 100
        session.write_text("[" * depth + '"private conversation"' + "]" * depth)
    elif damage == "version":
        session.write_text(session.read_text().replace('"version": 3', '"version": 9'))
    else:
        session.write_text(
            session.read_text().replace('"text": "plan"', '"text": "xxxx"')
        )
    with (
        patch("builtins.input", return_value="1"),
        pytest.raises(RuntimeError, match="State 'implement': Pi session") as error,
    ):
        resume_flow("resume-fork", tmp_path / ".fdsx", path)
    assert "private conversation" not in str(error.value)
    assert "restore the original" in str(error.value)
    captured = capsys.readouterr()
    assert "private conversation" not in captured.out + captured.err + caplog.text
    assert len(native.executions) == 1
    assert not native.forks


@pytest.mark.parametrize("damage", ["invalid_json", "deep_json"])
def test_resume_malformed_fork_metadata_fails_before_destination(
    tmp_path, native, damage, capsys, caplog
):
    path = interrupted_flow(tmp_path, native)
    depth = sys.getrecursionlimit() + 100
    metadata = (
        "[" * depth + '"private conversation"' + "]" * depth
        if damage == "deep_json"
        else "private conversation{"
    )

    def malformed_preparation(**kwargs):
        if "-e" in kwargs["args"]:
            return ProviderResult(0, metadata, "private conversation")
        return native(**kwargs)

    with (
        patch(
            "fdsx.providers.pi._run_subprocess", side_effect=malformed_preparation
        ) as subprocess,
        patch("builtins.input", return_value="1"),
        pytest.raises(RuntimeError, match="State 'implement': Pi session") as error,
    ):
        resume_flow("resume-fork", tmp_path / ".fdsx", path)
    assert "native endpoint fork failed; check Pi >= 0.85.1 and saved history" in str(
        error.value
    )
    captured = capsys.readouterr()
    assert "private conversation" not in (
        str(error.value) + captured.out + captured.err + caplog.text
    )
    assert len(native.executions) == 1
    assert not native.forks
    assert len(subprocess.call_args_list) == 2
    assert subprocess.call_args_list[0].kwargs["args"] == ["pi", "--version"]
    assert "-e" in subprocess.call_args_list[1].kwargs["args"]


def test_appended_history_still_forks_completed_endpoint(tmp_path, native):
    path = interrupted_flow(tmp_path, native)
    source = native.executions[0][1]
    with source.open("a") as file:
        file.write(
            json.dumps(
                dict(
                    type="message",
                    id="future",
                    parentId="entry-0",
                    message=dict(
                        role="assistant", content=[{"type": "text", "text": "future"}]
                    ),
                )
            )
            + "\n"
        )
    with patch("builtins.input", return_value="1"):
        result = resume_flow("resume-fork", tmp_path / ".fdsx", path)
    assert result.status == "completed"
    assert native.executions[-1][2] == ["plan"]
    assert native.forks[0][0]["endpoint"] == "entry-0"


@pytest.mark.parametrize("failure", ["version", "fork"])
def test_native_capability_failure_never_executes_blank_destination(
    tmp_path, native, failure
):
    path = interrupted_flow(tmp_path, native)
    if failure == "version":
        native.version = "0.50.0"
    else:
        native.fork_failure = True
    with (
        patch("builtins.input", return_value="1"),
        pytest.raises(RuntimeError, match="State 'implement': Pi session") as error,
    ):
        resume_flow("resume-fork", tmp_path / ".fdsx", path)
    assert "private conversation" not in str(error.value)
    assert len(native.executions) == 1


@pytest.mark.parametrize(
    "provider", ["claude", "codex", "grok", "opencode", "gemini", "cursor"]
)
def test_unsupported_provider_rejected_without_execution(tmp_path, native, provider):
    path = write_flow(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["states"]["review"]["provider"] = provider
    path.write_text(yaml.safe_dump(data))
    flow, errors = load_flow(path)
    assert flow is None
    assert "same supported provider" in " ".join(errors)
    assert not native.executions


def test_cli_rejects_inherited_cross_provider_escalation(tmp_path, native):
    path = write_flow(tmp_path)
    config_dir = tmp_path / ".fdsx"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "retry_escalation:\n  provider: codex\n  model: test\n"
    )
    result = CliRunner().invoke(app, ["validate", str(path)])
    assert result.exit_code != 0
    assert "retry_escalation" in result.output
    assert not native.executions


def test_profiles_and_explicit_disable_override_inherited_escalation(tmp_path, native):
    path = write_flow(tmp_path, retry_escalation=False)
    data = yaml.safe_load(path.read_text())
    for state in data["states"].values():
        state.pop("provider")
        state.pop("model")
        state["profile"] = "native"
    path.write_text(yaml.safe_dump(data))
    directory = tmp_path / ".fdsx"
    directory.mkdir()
    (directory / "config.yaml").write_text(
        "profiles:\n  native:\n    provider: pi\n    model: vendor/model\nretry_escalation:\n  provider: codex\n  model: test\n"
    )
    assert run_flow(path, base_dir=directory).status == "completed"


def test_older_checkpoint_without_references_fails_closed(tmp_path, native):
    from fdsx.checkpoint.manager import CheckpointManager
    from fdsx.core.compiler import compile_flow

    path = interrupted_flow(tmp_path, native)
    flow, errors = load_flow(path)
    assert not errors
    saver = CheckpointManager(tmp_path / ".fdsx").get_checkpointer()
    try:
        compiled = compile_flow(flow, checkpointer=saver)
        checkpoint_config = {"configurable": {"thread_id": "resume-fork"}}
        saved = compiled.graph.get_state(checkpoint_config).values
        reference = saved["_session_references"]["plan"]
        assert set(reference) == {
            "provider",
            "path",
            "id",
            "endpoint",
            "sha256",
            "size",
        }
        compiled.graph.update_state(
            checkpoint_config, {"_session_references": {}}, as_node="plan"
        )
    finally:
        saver.conn.close()
    with (
        patch("builtins.input", return_value="1"),
        pytest.raises(
            RuntimeError, match="missing native session reference for 'plan'"
        ),
    ):
        resume_flow("resume-fork", tmp_path / ".fdsx", path)
    assert len(native.executions) == 1


@pytest.mark.parametrize("invalid_output", [False, True])
def test_failed_replan_cannot_be_skipped_using_stale_reference(
    tmp_path, native, invalid_output
):
    plan = task("plan", next="implement")
    if invalid_output:
        (tmp_path / "schema.json").write_text('{"type":"object"}')
        plan["structured_output"] = {"schema": "schema.json", "result_path": "$.plan"}
        native.responses["plan"] = [
            ProviderResult(0, "{}", ""),
            ProviderResult(0, "invalid", ""),
        ]
    else:
        native.responses["plan"] = [
            ProviderResult(0, "ok", ""),
            ProviderResult(1, "", "failure"),
        ]
    path = write_flow(
        tmp_path,
        {
            "plan": plan,
            "implement": task("implement", fork_from="plan", next="route"),
            "route": {
                "type": "choice",
                "choices": [
                    {
                        "variable": "$._state_iterations.plan",
                        "operator": "less_than",
                        "value": 2,
                        "next": "plan",
                    }
                ],
                "default": "done",
            },
            "done": {"type": "pass", "end": True},
        },
    )
    with pytest.raises(RuntimeError):
        run_flow(path, thread_id="failed-replan", base_dir=tmp_path / ".fdsx")
    assert [call[0] for call in native.executions] == ["plan", "implement", "plan"]
    with pytest.raises(
        RuntimeError, match="missing native session reference for 'plan'"
    ):
        resume_flow("failed-replan", tmp_path / ".fdsx", path, from_state="implement")
    assert len(native.executions) == 3
    if invalid_output:
        native.responses["plan"] = [
            ProviderResult(0, "{}", ""),
            ProviderResult(0, "{}", ""),
        ]
    result = resume_flow("failed-replan", tmp_path / ".fdsx", path, from_state="plan")
    assert result.status == "completed"
    assert native.forks[-1][0]["path"] != native.forks[0][0]["path"]


def test_source_output_retry_publishes_only_validated_completion(tmp_path, native):
    (tmp_path / "schema.json").write_text('{"type":"object"}')
    plan = task("plan", next="implement")
    plan["retry"] = 1
    plan["structured_output"] = {"schema": "schema.json", "result_path": "$.plan"}
    native.responses["plan"] = [
        ProviderResult(0, "invalid", ""),
        ProviderResult(0, "{}", ""),
    ]
    path = write_flow(
        tmp_path,
        {"plan": plan, "implement": task("implement", fork_from="plan", end=True)},
    )
    result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.results == {"plan": {}}
    assert [call[0] for call in native.executions] == ["plan", "plan", "implement"]
    assert native.forks[0][0]["path"] == str(native.executions[1][1])
    assert native.forks[0][0]["endpoint"] == "entry-1"


@pytest.mark.parametrize(
    "source",
    [
        "unknown",
        "../external.jsonl",
        "branch-name",
        "iterator-name",
        "non_task",
        "system",
    ],
)
def test_invalid_source_rejected_at_loading(tmp_path, native, source):
    path = write_flow(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["states"]["review"]["fork_from"] = source
    data["states"]["non_task"] = {"type": "pass", "end": True}
    data["states"]["system"] = {
        "type": "task",
        "provider": "system",
        "command": "echo x",
        "end": True,
    }
    data["states"]["parallel"] = {
        "type": "parallel",
        "branches": [
            {
                "name": "branch-name",
                "provider": "pi",
                "model": "fixture",
                "prompt_template": "internal",
            }
        ],
        "result_path": "$.parallel",
        "end": True,
    }
    data["states"]["map"] = {
        "type": "map",
        "items_path": "$.items",
        "iterator": {
            "states": [
                {
                    "name": "iterator-name",
                    "provider": "pi",
                    "model": "fixture",
                    "prompt_template": "internal",
                    "result_path": "$.internal",
                }
            ]
        },
        "result_path": "$.mapped",
        "end": True,
    }
    path.write_text(yaml.safe_dump(data))
    flow, errors = load_flow(path)
    assert flow is None
    assert "top-level ordinary AI task" in " ".join(errors)
    assert not native.executions


@pytest.mark.parametrize("endpoint", ["plan", "review"])
def test_profile_provider_mismatch_is_rejected_on_either_endpoint(
    tmp_path, native, endpoint
):
    path = write_flow(tmp_path)
    data = yaml.safe_load(path.read_text())
    state = data["states"][endpoint]
    state.pop("provider")
    state.pop("model")
    state["profile"] = "other"
    path.write_text(yaml.safe_dump(data))
    flow, errors = load_flow(
        path, config_profiles={"other": {"provider": "codex", "model": "fixture"}}
    )
    assert flow is None
    assert "same supported provider" in " ".join(errors)
    assert not native.executions


@pytest.mark.parametrize("location", ["parallel", "map", "pass"])
def test_unsupported_fork_locations_are_explicit_errors(tmp_path, native, location):
    branch = dict(provider="pi", model="test", prompt_template="branch")
    iterator = dict(name="inner", **task("inner"))
    state = {
        "parallel": dict(
            type="parallel", branches=[branch], result_path="$.parallel", end=True
        ),
        "map": dict(
            type="map",
            items_path="$.items",
            iterator={"states": [iterator]},
            result_path="$.mapped",
            end=True,
        ),
        "pass": dict(type="pass", end=True),
    }[location]
    state["fork_from"] = "plan"
    path = write_flow(
        tmp_path, {"plan": task("plan", next="destination"), "destination": state}
    )
    flow, errors = load_flow(path)
    assert flow is None
    assert "fork_from" in " ".join(errors)
    assert not native.executions


@pytest.mark.parametrize("kind", ["parallel", "map"])
def test_outer_plan_fans_out_to_independent_children(tmp_path, native, kind):
    path = internal_flow(tmp_path, kind, model="other/model")
    native.responses["child0"] = [ProviderResult(1, "", "retry")]
    result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    assert len(native.forks) == 3
    assert len({str(path) for _, path in native.forks}) == 3
    assert (
        len({(request["path"], request["endpoint"]) for request, _ in native.forks})
        == 1
    )
    assert all(execution[2] == ["plan"] for execution in native.executions[1:])
    assert len(native.executions) == 4
    outputs = result.results["results"]
    if kind == "parallel":
        assert [row["output"] for row in outputs] == ["child0 output", "child1 output"]
    else:
        assert outputs == ["child0 output", "child1 output"]
    parent_history = native.executions[0][1].read_text()
    assert "child0" not in parent_history and "child1" not in parent_history


def internal_flow(tmp_path, kind, **options):
    child = dict(provider="pi", model="vendor/model", fork_from="plan", retry=1)
    child.update(options)
    if kind == "parallel":
        destination = dict(
            type="parallel",
            branches=[
                dict(**child, name=f"child{i}", prompt_template=f"child{i}")
                for i in range(2)
            ],
            result_path="$.results",
            end=True,
        )
    else:
        destination = dict(
            type="map",
            items_path="$.items",
            iterator={
                "states": [
                    dict(
                        **child,
                        name="plan",
                        prompt_template="child{item}",
                        result_path="$.value",
                    )
                ]
            },
            result_path="$.results",
            end=True,
        )
    return write_flow(
        tmp_path,
        {
            "plan": task("plan", next="items"),
            "items": dict(type="pass", parameters={"items": [0, 1]}, next="children"),
            "children": destination,
        },
    )


@pytest.mark.parametrize("kind", ["parallel", "map"])
def test_internal_resume_and_retry_keep_outer_source(tmp_path, native, kind):
    path = internal_flow(tmp_path, kind)

    def interrupt(action, session):
        if action == "child1":
            raise RuntimeError("interrupted child")

    native.on_execute = interrupt
    with pytest.raises(RuntimeError, match="interrupted child"):
        run_flow(path, thread_id="internal-resume", base_dir=tmp_path / ".fdsx")
    native.on_execute = None
    native.responses["child1"] = [ProviderResult(1, "", "retry after resume")]
    result = resume_flow("internal-resume", tmp_path / ".fdsx", path)
    assert result.status == "completed"
    actions = [execution[0] for execution in native.executions]
    assert actions.count("plan") == 1
    assert actions.count("child1") == 3
    if kind == "map":
        assert actions.count("child0") == 1
        assert result.results["results"] == ["child0 output", "child1 output"]
    assert all(execution[2] == ["plan"] for execution in native.executions[1:])
    assert len({request["path"] for request, _ in native.forks}) == 1
    assert len({str(path) for _, path in native.forks}) == len(native.forks)


@pytest.mark.parametrize("kind", ["parallel", "map"])
@pytest.mark.parametrize("failure", ["missing", "corrupt", "fork", "metadata"])
def test_internal_native_failure_never_runs_blank_child(
    tmp_path, native, kind, failure
):
    path = internal_flow(tmp_path, kind)
    # Stop after source publication using the existing wait/resume seam.
    data = yaml.safe_load(path.read_text())
    data["states"]["items"]["next"] = "wait"
    data["states"]["wait"] = dict(
        type="wait",
        mode="prompt",
        message="Continue",
        choices=["yes"],
        result_path="$.approval",
        next="children",
    )
    path.write_text(yaml.safe_dump(data))
    with (
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=RuntimeError("interrupted"),
        ),
        pytest.raises(RuntimeError),
    ):
        run_flow(path, thread_id="internal-failure", base_dir=tmp_path / ".fdsx")
    source = native.executions[0][1]
    if failure == "metadata":
        from fdsx.checkpoint.manager import CheckpointManager
        from fdsx.core.compiler import compile_flow

        flow, errors = load_flow(path)
        assert flow is not None, errors
        saver = CheckpointManager(tmp_path / ".fdsx").get_checkpointer()
        try:
            graph = compile_flow(flow, checkpointer=saver).graph
            checkpoint_config = {"configurable": {"thread_id": "internal-failure"}}
            saved = graph.get_state(checkpoint_config).values
            assert set(saved["_session_references"]) == {"plan"}
            assert set(saved["_session_references"]["plan"]) == {
                "provider",
                "path",
                "id",
                "endpoint",
                "sha256",
                "size",
            }
            graph.update_state(
                checkpoint_config, {"_session_references": {}}, as_node="items"
            )
        finally:
            saver.conn.close()
    elif failure == "missing":
        source.unlink()
    elif failure == "corrupt":
        source.write_text("invalid native history")
    else:
        native.fork_failure = True
    with (
        patch("builtins.input", return_value="1"),
        pytest.raises(
            RuntimeError,
            match="missing native session reference"
            if failure == "metadata"
            else "Pi session",
        ),
    ):
        resume_flow("internal-failure", tmp_path / ".fdsx", path)
    assert [execution[0] for execution in native.executions] == ["plan"]
    assert not native.forks


@pytest.mark.parametrize("kind", ["parallel", "map"])
@pytest.mark.parametrize("source", ["unknown", "child0", "children"])
def test_internal_invalid_sources_rejected_by_loader_and_cli(
    tmp_path, native, kind, source
):
    path = internal_flow(tmp_path, kind, fork_from=source)
    flow, errors = load_flow(path)
    assert flow is None
    assert "top-level ordinary AI task" in " ".join(errors)
    result = CliRunner().invoke(app, ["validate", str(path)])
    assert result.exit_code != 0
    assert "fork_from" in result.output
    assert not native.executions


@pytest.mark.parametrize("kind", ["parallel", "map"])
def test_internal_effective_profiles_and_escalation(tmp_path, native, kind):
    path = internal_flow(tmp_path, kind)
    data = yaml.safe_load(path.read_text())
    children = data["states"]["children"]
    tasks = (
        children["branches"] if kind == "parallel" else children["iterator"]["states"]
    )
    for child in tasks:
        child.pop("provider")
        child.pop("model")
        child["profile"] = "reviewer"
    data["profiles"] = {"reviewer": dict(provider="codex", model="test")}
    path.write_text(yaml.safe_dump(data))
    assert "same supported provider" in " ".join(load_flow(path)[1])
    data["profiles"]["reviewer"]["provider"] = "pi"
    data["retry_escalation"] = dict(provider="codex", model="test")
    path.write_text(yaml.safe_dump(data))
    assert "retry_escalation" in " ".join(load_flow(path)[1])
    data["retry_escalation"] = dict(provider="pi", model="other-vendor/model")
    path.write_text(yaml.safe_dump(data))
    native.responses["child0"] = [ProviderResult(1, "", "retry")]
    assert run_flow(path, base_dir=tmp_path / ".fdsx").status == "completed"
    retries = [execution for execution in native.executions if execution[0] == "child0"]
    assert "other-vendor/model" in retries[-1][3]
    assert all(execution[2] == ["plan"] for execution in retries)


@pytest.mark.parametrize("kind", ["parallel", "map"])
def test_internal_structured_retry_forks_original_source(tmp_path, native, kind):
    (tmp_path / "schema.json").write_text(
        '{"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"}}}'
    )
    path = internal_flow(
        tmp_path,
        kind,
        structured_output={"schema": "schema.json", "result_path": "$.answer"},
    )
    native.responses["child0"] = [
        ProviderResult(0, "invalid", ""),
        ProviderResult(0, '{"ok":true}', ""),
    ]
    native.responses["child1"] = [ProviderResult(0, '{"ok":true}', "")]
    result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    if kind == "parallel":
        assert all(row["answer"] == {"ok": True} for row in result.results["results"])
    else:
        assert result.results["results"] == [{"ok": True}, {"ok": True}]
    assert len(native.forks) == 3
    assert len({path for _, path in native.forks}) == 3
    assert {request["path"] for request, _ in native.forks} == {
        str(native.executions[0][1])
    }
    assert {request["endpoint"] for request, _ in native.forks} == {"entry-0"}
    assert all(execution[2] == ["plan"] for execution in native.executions[1:])
    assert "child" not in native.executions[0][1].read_text()


@pytest.mark.parametrize("fail_fast", [True, False])
def test_map_structured_retry_exhaustion_preserves_failure_policy(
    tmp_path, native, fail_fast
):
    (tmp_path / "schema.json").write_text('{"type":"object","required":["ok"]}')
    path = internal_flow(
        tmp_path,
        "map",
        structured_output={"schema": "schema.json", "result_path": "$.answer"},
    )
    data = yaml.safe_load(path.read_text())
    data["states"]["children"]["fail_fast"] = fail_fast
    path.write_text(yaml.safe_dump(data))
    native.responses["child0"] = [
        ProviderResult(0, "invalid", ""),
        ProviderResult(0, "{}", ""),
    ]
    native.responses["child1"] = [ProviderResult(0, '{"ok":true}', "")]
    with pytest.raises(
        RuntimeError,
        match="iteration 0 failed" if fail_fast else "1 of 2 iterations failed",
    ):
        run_flow(path, base_dir=tmp_path / ".fdsx")
    assert [execution[0] for execution in native.executions] == [
        "plan",
        "child0",
        "child0",
    ] + ([] if fail_fast else ["child1"])
    assert all(execution[2] == ["plan"] for execution in native.executions[1:])
    assert len({path for _, path in native.forks}) == len(native.executions) - 1


@pytest.mark.parametrize("schema", ["missing.json", "../outside.json"])
def test_map_structured_schema_errors_rejected_before_execution(
    tmp_path, native, schema
):
    path = internal_flow(
        tmp_path, "map", structured_output={"schema": schema, "result_path": "$.answer"}
    )
    flow, errors = load_flow(path)
    assert flow is None
    assert "Map state 'children' task 'plan': schema" in " ".join(errors)
    assert CliRunner().invoke(app, ["validate", str(path)]).exit_code != 0
    assert not native.executions


@pytest.mark.parametrize("kind", ["parallel", "map"])
def test_internal_replanning_uses_latest_outer_endpoint(tmp_path, native, kind):
    path = internal_flow(tmp_path, kind)
    data = yaml.safe_load(path.read_text())
    children = data["states"]["children"]
    children.pop("end")
    children["next"] = "route"
    data["states"]["route"] = dict(
        type="choice",
        choices=[
            dict(
                variable="$._state_iterations.plan",
                operator="less_than",
                value=2,
                next="plan",
            )
        ],
        default="done",
    )
    data["states"]["done"] = dict(type="pass", end=True)
    path.write_text(yaml.safe_dump(data))
    assert run_flow(path, base_dir=tmp_path / ".fdsx").status == "completed"
    sources = [
        execution[1] for execution in native.executions if execution[0] == "plan"
    ]
    assert len(sources) == 2
    assert [request["path"] for request, _ in native.forks] == [str(sources[0])] * 2 + [
        str(sources[1])
    ] * 2
    assert all(
        execution[2] == ["plan"]
        for execution in native.executions
        if execution[0] != "plan"
    )


@pytest.mark.parametrize(
    "policy", ["min_success", "required_gate", "fail_fast", "continue_items"]
)
def test_internal_fork_errors_follow_existing_failure_policy(tmp_path, native, policy):
    kind = "parallel" if policy in {"min_success", "required_gate"} else "map"
    path = internal_flow(tmp_path, kind)
    data = yaml.safe_load(path.read_text())
    children = data["states"]["children"]
    if kind == "parallel":
        children["min_success"] = 1
        if policy == "required_gate":
            children.pop("min_success")
            (tmp_path / "gate.json").write_text(
                '{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}'
            )
            children["branches"][0]["structured_output"] = {
                "schema": "gate.json",
                "result_path": "$.answer",
            }
            children["gate"] = dict(
                required=["child0"],
                field="$.ok",
                expected=True,
                result_path="$.approved",
            )
    else:
        children["fail_fast"] = policy == "fail_fast"
    path.write_text(yaml.safe_dump(data))
    preparation_count = 0

    def fail_first(**kwargs):
        nonlocal preparation_count
        if "-e" in kwargs["args"]:
            preparation_count += 1
            if kind == "map" and preparation_count == 1:
                return ProviderResult(1, "", "native failure")
            if kind == "parallel":
                # Select by child request prompt is unavailable at preparation;
                # Failing both exercises the configured collector policy.
                return ProviderResult(1, "", "native failure")
        return native(**kwargs)

    with (
        patch("fdsx.providers.pi._run_subprocess", side_effect=fail_first),
        pytest.raises(RuntimeError),
    ):
        run_flow(path, base_dir=tmp_path / ".fdsx")
    if policy == "continue_items":
        assert [execution[0] for execution in native.executions] == ["plan", "child1"]
    else:
        assert [execution[0] for execution in native.executions] == ["plan"]
    if policy == "fail_fast":
        assert preparation_count == 1


@pytest.mark.parametrize(
    "policy", ["min_success", "advisory_failure", "required_failure"]
)
def test_parallel_mixed_native_fork_failure_preserves_policy(tmp_path, native, policy):
    from fdsx.providers.pi_sessions import new_session_directory

    (tmp_path / "gate.json").write_text(
        '{"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"}}}'
    )
    path = internal_flow(tmp_path, "parallel", retry=0)
    data = yaml.safe_load(path.read_text())
    children = data["states"]["children"]
    if policy == "min_success":
        children["min_success"] = 1
    else:
        for branch in children["branches"]:
            branch["structured_output"] = {
                "schema": "gate.json",
                "result_path": "$.answer",
            }
        children["gate"] = dict(
            required=["child0"],
            field="$.answer.ok",
            expected=True,
            result_path="$.approved",
        )
    path.write_text(yaml.safe_dump(data))
    failed_name = "child0" if policy == "required_failure" else "child1"
    directories = {}
    rejected = []

    def allocate(state):
        directory = new_session_directory(state)
        directories[str(directory)] = state
        return directory

    def fail_selected(**kwargs):
        if "-e" in kwargs["args"]:
            request = json.loads(kwargs["env"]["FDSX_PI_FORK_REQUEST"])
            if directories[request["directory"]] == (
                "children_branch1" if failed_name == "child0" else "children_branch2"
            ):
                rejected.append(request)
                return ProviderResult(1, "", "native failure")
        return native(**kwargs)

    for name in ("child0", "child1"):
        native.responses[name] = [ProviderResult(0, '{"ok":true}', "")]
    with (
        patch("fdsx.providers.pi_sessions.new_session_directory", side_effect=allocate),
        patch("fdsx.providers.pi._run_subprocess", side_effect=fail_selected),
    ):
        if policy == "required_failure":
            with pytest.raises(RuntimeError, match="required branch 'child0' failed"):
                run_flow(path, base_dir=tmp_path / ".fdsx")
        else:
            result = run_flow(path, base_dir=tmp_path / ".fdsx")
            assert result.status == "completed"
            rows = {row["name"]: row for row in result.results["results"]}
            assert rows["child0"]["exit_code"] == 0
            assert rows["child1"]["exit_code"] != 0
            assert "Pi" in rows["child1"]["error"]
            if policy == "advisory_failure":
                assert result.results["approved"] is True
    assert len(rejected) == 1
    assert len(native.forks) == 1
    assert {execution[0] for execution in native.executions} == {
        "plan",
        "child1" if failed_name == "child0" else "child0",
    }
    assert native.executions[1][2] == ["plan"]
    assert "--session" in native.executions[1][3]


@pytest.mark.parametrize("kind", ["parallel", "map"])
def test_internal_common_ancestor_loads_and_validates_after_rejoin(
    tmp_path, native, kind
):
    path = internal_flow(tmp_path, kind)
    data = yaml.safe_load(path.read_text())
    data["states"]["items"]["next"] = "split"
    data["states"].update(
        {
            "split": dict(
                type="choice",
                choices=[
                    dict(
                        variable="$.items", operator="equals", value=[0, 1], next="left"
                    )
                ],
                default="right",
            ),
            "left": dict(type="pass", next="children"),
            "right": dict(type="pass", next="children"),
        }
    )
    path.write_text(yaml.safe_dump(data))
    flow, errors = load_flow(path)
    assert flow is not None, errors
    assert CliRunner().invoke(app, ["validate", str(path)]).exit_code == 0
    assert not native.executions


def test_replanned_map_resume_skips_only_current_visit_completed_items(
    tmp_path, native
):
    path = internal_flow(tmp_path, "map")
    data = yaml.safe_load(path.read_text())
    children = data["states"]["children"]
    children.pop("end")
    children["next"] = "route"
    data["states"]["route"] = dict(
        type="choice",
        choices=[
            dict(
                variable="$._state_iterations.plan",
                operator="less_than",
                value=2,
                next="plan",
            )
        ],
        default="done",
    )
    data["states"]["done"] = dict(type="pass", end=True)
    path.write_text(yaml.safe_dump(data))

    def interrupt_second_visit(action, session):
        if (
            action == "child1"
            and sum(execution[0] == "plan" for execution in native.executions) == 2
        ):
            raise RuntimeError("interrupted second visit")

    native.on_execute = interrupt_second_visit
    with pytest.raises(RuntimeError, match="interrupted second visit"):
        run_flow(path, thread_id="replan-map", base_dir=tmp_path / ".fdsx")
    native.on_execute = None
    native.responses["child1"] = [ProviderResult(1, "", "retry")]
    assert resume_flow("replan-map", tmp_path / ".fdsx", path).status == "completed"
    actions = [execution[0] for execution in native.executions]
    assert actions.count("plan") == 2
    assert actions.count("child0") == 2
    sources = [
        execution[1] for execution in native.executions if execution[0] == "plan"
    ]
    assert [request["path"] for request, _ in native.forks] == [str(sources[0])] * 2 + [
        str(sources[1])
    ] * 4

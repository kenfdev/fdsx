"""Offline Codex CLI contract checks; these do not qualify native support."""

import json
from pathlib import Path
from threading import Lock
from unittest.mock import patch
from uuid import uuid4

import pytest
import yaml
from typer.testing import CliRunner

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.cli.main import app
from fdsx.core.compiler import compile_flow
from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.loader import load_flow
from fdsx.providers.base import ProviderResult, ProviderSessionError, SessionRequest
from fdsx.providers.codex import CodexProvider


def task(prompt, **kwargs):
    return dict(
        type="task",
        provider="codex",
        model="gpt-test",
        prompt_template=prompt,
        retry=0,
        **kwargs,
    )


def write_flow(tmp_path, states, **kwargs):
    path = tmp_path / "flow.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                name="codex-forks",
                description="offline",
                start_at="plan",
                states=states,
                **kwargs,
            )
        )
    )
    return path


class NativeCLI:
    def __init__(self):
        self.calls = []
        self.responses = {}
        self.interrupt = None
        self.lock = Lock()
        self.stream_events = []

    def __call__(self, **kwargs):
        args = kwargs["args"]
        if args == ["codex", "--version"]:
            return ProviderResult(0, "codex-cli 0.154.0\n", "")
        prompt = kwargs["stdin_data"] or args[2]
        action = prompt.split("\n")[0]
        parent = args[args.index("fork") + 1] if "fork" in args else None
        assert args[:2] == ["codex", "exec"]
        assert "resume" not in args
        assert "--json" in args
        assert args[-1] == "-"
        assert "ephemeral=false" in args
        with self.lock:
            child = str(uuid4())
            self.calls.append(
                dict(
                    action=action, parent=parent, child=child, args=args, kwargs=kwargs
                )
            )
            responses = self.responses.get(action, [])
            response = responses.pop(0) if responses else {}
        if self.interrupt == action:
            raise RuntimeError("crash boundary")
        if action == "implement":
            Path("code.txt").write_text("implemented")
        if action == "review":
            assert Path("code.txt").read_text() == "implemented"
        output = response.get("result", action + " output")
        if "structured_output" in response:
            output = json.dumps(response["structured_output"])
        events = [
            dict(type="thread.started", thread_id=response.get("session_id", child)),
            *self.stream_events,
            dict(type="item.completed", item=dict(type="agent_message", text=output)),
            dict(type="turn.completed", usage={}),
        ]
        if response.get("is_error"):
            events = [dict(type="turn.failed", error={"message": "PRIVATE failure"})]
        callback = kwargs.get("output_callback")
        if callback:
            for event in events:
                callback(json.dumps(event))
        return ProviderResult(
            1 if response.get("is_error") else 0,
            "\n".join(json.dumps(event) for event in events),
            "PRIVATE native diagnostic",
        )


@pytest.fixture
def native():
    fixture = NativeCLI()
    with (
        patch("fdsx.providers.codex._run_subprocess", side_effect=fixture),
        patch("fdsx.core.compiler.execution.time.sleep"),
    ):
        yield fixture


def test_siblings_current_files_and_fork_chain(tmp_path, native):
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="implement"),
            "implement": task("implement", fork_from="plan", next="review"),
            "review": task("review", fork_from="plan", next="follow"),
            "follow": task(
                "follow", fork_from="implement", result_path="$.answer", end=True
            ),
        },
    )
    result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    a, b, c, d = native.calls
    assert a["parent"] is None
    assert b["parent"] == c["parent"] == a["child"]
    assert d["parent"] == b["child"]
    assert len({call["child"] for call in native.calls}) == 4
    assert result.results["answer"] == "follow output"
    assert "session_id" not in json.dumps(result.results)


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("kind", ["task", "parallel", "map"])
def test_retry_children_keep_selected_source(tmp_path, native, structured, kind):
    child = dict(
        provider="codex",
        model="gpt-test",
        fork_from="plan",
        retry=1,
        prompt_template="child",
    )
    if structured:
        (tmp_path / "schema.json").write_text('{"type":"object","required":["ok"]}')
        child["structured_output"] = dict(schema="schema.json", result_path="$.value")
        native.responses["child"] = [
            dict(structured_output={}),
            dict(structured_output={"ok": True}),
        ]
    else:
        native.responses["child"] = [dict(is_error=True, result="PRIVATE failure")]
    if kind == "task":
        destination = dict(type="task", **child, end=True)
    elif kind == "parallel":
        destination = dict(
            type="parallel",
            branches=[dict(**child, name="one"), dict(**child, name="two")],
            result_path="$.children",
            end=True,
        )
        if structured:
            native.responses["child"].append(dict(structured_output={"ok": True}))
    else:
        iterator = dict(**child, name="plan")  # local name cannot shadow outer plan
        iterator["result_path"] = "$.value"
        destination = dict(
            type="map",
            items_path="$.items",
            iterator=dict(states=[iterator]),
            result_path="$.children",
            end=True,
        )
        if structured:
            native.responses["child"].append(dict(structured_output={"ok": True}))
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="items"),
            "items": dict(type="pass", parameters={"items": [0, 1]}, next="children"),
            "children": destination,
        },
    )
    result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    calls = native.calls
    assert len(calls) == (3 if kind == "task" else 4)
    assert all(c["parent"] == calls[0]["child"] for c in calls[1:])
    assert len({c["child"] for c in calls}) == len(calls)


def looping_flow(tmp_path, structured=False):
    plan = task("plan", next="child")
    if structured:
        (tmp_path / "schema.json").write_text('{"type":"object"}')
        plan["structured_output"] = dict(schema="schema.json", result_path="$.plan")
        plan["retry"] = 1
    return write_flow(
        tmp_path,
        {
            "plan": plan,
            "child": task("child", fork_from="plan", next="route"),
            "route": dict(
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
            ),
            "done": dict(type="pass", end=True),
        },
    )


@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("kind", ["task", "parallel", "map"])
def test_exhausted_retries_preserve_source_and_files(
    tmp_path, native, structured, kind
):
    child = dict(
        provider="codex",
        model="gpt-test",
        fork_from="plan",
        prompt_template="implement",
        retry=1,
    )
    if structured:
        (tmp_path / "schema.json").write_text('{"type":"object","required":["ok"]}')
        child["structured_output"] = dict(schema="schema.json", result_path="$.value")
        native.responses["implement"] = [dict(structured_output={}) for _ in range(2)]
    else:
        native.responses["implement"] = [dict(is_error=True) for _ in range(2)]
    if kind == "task":
        destination = dict(type="task", **child, end=True)
    elif kind == "parallel":
        destination = dict(
            type="parallel",
            branches=[dict(name="implementation", **child)],
            result_path="$.children",
            end=True,
        )
    else:
        destination = dict(
            type="map",
            items_path="$.items",
            iterator=dict(
                states=[dict(name="implementation", result_path="$.raw", **child)]
            ),
            result_path="$.children",
            fail_fast=True,
            end=True,
        )
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="items"),
            "items": dict(type="pass", parameters={"items": [0, 1]}, next="children"),
            "children": destination,
        },
    )
    with pytest.raises(RuntimeError):
        run_flow(path, thread_id="exhausted", base_dir=tmp_path / ".fdsx")
    source, first, second = native.calls
    assert first["parent"] == second["parent"] == source["child"]
    assert len({call["child"] for call in native.calls}) == 3
    assert (tmp_path / "code.txt").read_text() == "implemented"
    flow, errors = load_flow(path)
    assert not errors
    saver = CheckpointManager(tmp_path / ".fdsx").get_checkpointer()
    try:
        saved = (
            compile_flow(flow, checkpointer=saver)
            .graph.get_state({"configurable": {"thread_id": "exhausted"}})
            .values
        )
        assert saved["_session_references"] == {
            "plan": {"provider": "codex", "session_id": source["child"]}
        }
    finally:
        saver.conn.close()


def test_replan_publication_after_validation(tmp_path, native):
    native.responses["plan"] = [
        dict(result="bad"),
        dict(structured_output={}),
        dict(structured_output={}),
    ]
    result = run_flow(looping_flow(tmp_path, True), base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    a, b, c, d, e = native.calls
    assert a["parent"] is b["parent"] is d["parent"] is None
    assert c["parent"] == b["child"]
    assert e["parent"] == d["child"]


@pytest.mark.parametrize("from_state", ["child", "plan"])
def test_failed_replan_recovery_reuses_or_replaces_source(tmp_path, native, from_state):
    native.responses["plan"] = [{}, dict(is_error=True)]
    path = looping_flow(tmp_path)
    with pytest.raises(RuntimeError):
        run_flow(path, thread_id="replan", base_dir=tmp_path / ".fdsx")
    assert len(native.calls) == 3
    # End after recovery rather than reading the reset plan iteration counter.
    data = yaml.safe_load(path.read_text())
    data["states"]["route"] = dict(type="pass", end=True)
    path.write_text(yaml.safe_dump(data))
    result = resume_flow("replan", tmp_path / ".fdsx", path, from_state=from_state)
    assert result.status == "completed"
    if from_state == "child":
        assert native.calls[3]["parent"] == native.calls[0]["child"]
        assert native.calls[3]["child"] != native.calls[1]["child"]
    else:
        assert native.calls[3]["parent"] is None
        assert native.calls[4]["parent"] == native.calls[3]["child"]


@pytest.mark.parametrize("erase", [False, True])
def test_interrupted_resume_reference_only_checkpoint(tmp_path, native, erase):
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="child"),
            "child": task("child", fork_from="plan", end=True),
        },
    )
    native.interrupt = "child"
    with pytest.raises(RuntimeError, match="crash boundary"):
        run_flow(path, thread_id="resume", base_dir=tmp_path / ".fdsx")
    flow, errors = load_flow(path)
    assert not errors
    saver = CheckpointManager(tmp_path / ".fdsx").get_checkpointer()
    try:
        graph = compile_flow(flow, checkpointer=saver).graph
        config = {"configurable": {"thread_id": "resume"}}
        refs = graph.get_state(config).values["_session_references"]
        assert refs == {
            "plan": {"provider": "codex", "session_id": native.calls[0]["child"]}
        }
        if erase:
            graph.update_state(config, {"_session_references": {}}, as_node="plan")
    finally:
        saver.conn.close()
    native.interrupt = None
    if erase:
        with pytest.raises(RuntimeError, match="missing native session reference"):
            resume_flow("resume", tmp_path / ".fdsx", path)
        assert len(native.calls) == 2
    else:
        assert resume_flow("resume", tmp_path / ".fdsx", path).status == "completed"
        assert [c["action"] for c in native.calls] == ["plan", "child", "child"]
        assert native.calls[-1]["parent"] == native.calls[0]["child"]


@pytest.mark.parametrize("damage", [None, "bad", "parent", "error"])
def test_bad_native_metadata_fails_closed_without_leaking(native, caplog, damage):
    parent = str(uuid4())
    native.responses["child"] = [
        dict(
            session_id=parent if damage == "parent" else damage,
            is_error=damage == "error",
            result="PRIVATE failure" if damage == "error" else "answer",
        )
    ]
    output = []
    kwargs = dict(
        prompt="child", stderr_callback=output.append, output_callback=output.append
    )
    request = SessionRequest("child", {"provider": "codex", "session_id": parent})
    if damage == "error":
        result = CodexProvider().execute_with_session(request, **kwargs)
        assert result.exit_code != 0
        assert "rerun the source" in result.stderr
        assert "PRIVATE" not in result.stderr
    else:
        with pytest.raises(ProviderSessionError, match=r"metadata|child reference"):
            CodexProvider().execute_with_session(request, **kwargs)
    assert len(native.calls) == 1
    assert "PRIVATE" not in repr(output) + caplog.text


@pytest.mark.parametrize(
    "provider", ["pi", "claude", "cursor", "grok", "opencode", "gemini"]
)
def test_mixed_providers_rejected_by_loader_and_cli(tmp_path, native, provider):
    destination = task("child", fork_from="plan", end=True)
    destination["provider"] = provider
    path = write_flow(
        tmp_path, {"plan": task("plan", next="child"), "child": destination}
    )
    assert load_flow(path)[1]
    assert CliRunner().invoke(app, ["validate", str(path)]).exit_code != 0
    assert native.calls == []


@pytest.mark.parametrize(
    "escalation",
    [
        dict(provider="pi", model="gpt-test"),
    ],
)
def test_incompatible_escalation_rejected(tmp_path, native, escalation):
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="child"),
            "child": task("child", fork_from="plan", end=True),
        },
        retry_escalation=escalation,
    )
    assert "retry_escalation" in " ".join(load_flow(path)[1])
    assert not native.calls


def internal_flow(tmp_path, kind, **options):
    child = dict(provider="codex", model="gpt-test", fork_from="plan", retry=0)
    if kind == "map":
        destination = dict(
            type="map",
            items_path="$.items",
            iterator=dict(
                states=[
                    dict(
                        **child,
                        name="plan",
                        prompt_template="child{item}",
                        result_path="$.value",
                    )
                ]
            ),
            result_path="$.children",
            end=True,
            **options,
        )
    else:
        destination = dict(
            type="parallel",
            branches=[
                dict(**child, name=f"child{i}", prompt_template=f"child{i}")
                for i in range(2)
            ],
            result_path="$.children",
            end=True,
            **options,
        )
    return write_flow(
        tmp_path,
        {
            "plan": task("plan", next="items"),
            "items": dict(type="pass", parameters={"items": [0, 1]}, next="children"),
            "children": destination,
        },
    )


@pytest.mark.parametrize("fail_fast", [False, True])
def test_map_native_failure_policy(tmp_path, native, fail_fast):
    path = internal_flow(tmp_path, "map", fail_fast=fail_fast)
    native.responses["child0"] = [dict(is_error=True)]
    with pytest.raises(
        RuntimeError,
        match="iteration 0 failed" if fail_fast else "1 of 2 iterations failed",
    ):
        run_flow(path, base_dir=tmp_path / ".fdsx")
    assert [c["action"] for c in native.calls] == ["plan", "child0"] + (
        [] if fail_fast else ["child1"]
    )
    assert all(c["parent"] == native.calls[0]["child"] for c in native.calls[1:])


@pytest.mark.parametrize("required_failure", [False, True])
def test_parallel_required_gate_and_advisory_failures(
    tmp_path, native, required_failure
):
    path = internal_flow(tmp_path, "parallel")
    data = yaml.safe_load(path.read_text())
    (tmp_path / "schema.json").write_text('{"type":"object","required":["ok"]}')
    children = data["states"]["children"]
    for branch in children["branches"]:
        branch["structured_output"] = dict(schema="schema.json", result_path="$.answer")
    children["gate"] = dict(
        required=["child0"],
        field="$.answer.ok",
        expected=True,
        result_path="$.approved",
    )
    path.write_text(yaml.safe_dump(data))
    native.responses["child0"] = [
        dict(is_error=True)
        if required_failure
        else dict(structured_output={"ok": True})
    ]
    native.responses["child1"] = [dict(is_error=True)]
    if required_failure:
        with pytest.raises(RuntimeError):
            run_flow(path, base_dir=tmp_path / ".fdsx")
    else:
        result = run_flow(path, base_dir=tmp_path / ".fdsx")
        assert result.status == "completed"
        assert result.results["approved"] is True
    assert all(c["parent"] == native.calls[0]["child"] for c in native.calls[1:])


def test_map_new_visit_and_interrupted_progress(tmp_path, native):
    path = internal_flow(tmp_path, "map")
    data = yaml.safe_load(path.read_text())
    data["states"]["children"].pop("end")
    data["states"]["children"]["next"] = "route"
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
    original = native.__call__

    def interrupt_second_visit(**kwargs):
        if sum(c["action"] == "plan" for c in native.calls) == 2:
            native.interrupt = "child1"
        return original(**kwargs)

    with (
        patch(
            "fdsx.providers.codex._run_subprocess", side_effect=interrupt_second_visit
        ),
        pytest.raises(RuntimeError, match="crash boundary"),
    ):
        run_flow(path, thread_id="map", base_dir=tmp_path / ".fdsx")
    native.interrupt = None
    assert resume_flow("map", tmp_path / ".fdsx", path).status == "completed"
    sources = [c["child"] for c in native.calls if c["action"] == "plan"]
    assert len(sources) == 2
    assert [c["parent"] for c in native.calls if c["action"] != "plan"] == [
        sources[0]
    ] * 2 + [sources[1]] * 3
    assert sum(c["action"] == "child0" for c in native.calls) == 2


def test_effective_profiles_inherited_escalation_and_opt_out(tmp_path, native):
    path = write_flow(
        tmp_path,
        {
            "plan": dict(
                type="task", profile="same", prompt_template="plan", next="child"
            ),
            "child": dict(
                type="task",
                profile="same",
                prompt_template="child",
                fork_from="plan",
                end=True,
            ),
        },
        profiles={"same": dict(provider="codex", model="gpt-test")},
    )
    config_dir = tmp_path / ".fdsx"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "retry_escalation:\n  provider: claude\n  model: other\n"
    )
    assert CliRunner().invoke(app, ["validate", str(path)]).exit_code != 0
    assert not native.calls
    data = yaml.safe_load(path.read_text())
    data["retry_escalation"] = False
    path.write_text(yaml.safe_dump(data))
    assert run_flow(path, base_dir=config_dir).status == "completed"
    assert native.calls[-1]["parent"] == native.calls[0]["child"]


def test_model_switch_forks_source_with_requested_model(tmp_path, native):
    child = task("child", fork_from="plan", end=True)
    child["model"] = "other-model"
    path = write_flow(tmp_path, {"plan": task("plan", next="child"), "child": child})
    assert run_flow(path, base_dir=tmp_path / ".fdsx").status == "completed"
    source, destination = native.calls
    assert destination["parent"] == source["child"]
    args = destination["args"]
    assert args[args.index("--model") + 1] == "other-model"


@pytest.mark.parametrize(
    "payload",
    [
        "not json PRIVATE",
        "[]",
        '{"type":"item.completed","item":null}',
        '{"type":"thread.started"}',
    ],
)
def test_malformed_stream_is_domain_error(payload, caplog):
    def malformed(**kwargs):
        if kwargs["args"] == ["codex", "--version"]:
            return ProviderResult(0, "codex-cli 0.154.0", "")
        kwargs["output_callback"](payload)
        return ProviderResult(0, payload, "PRIVATE")

    with (
        patch("fdsx.providers.codex._run_subprocess", side_effect=malformed),
        pytest.raises(ProviderSessionError),
    ):
        CodexProvider().execute_with_session(SessionRequest("plan"), prompt="plan")
    assert "PRIVATE" not in caplog.text


def test_session_large_prompt_timeout_and_callbacks(native):
    from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD
    from fdsx.providers.codex import CodexOptions

    prompt = "x" * ARG_MAX_STDIN_THRESHOLD
    result = CodexProvider(CodexOptions(inactivity_timeout=17)).execute_with_session(
        SessionRequest("plan"), prompt=prompt, timeout=29
    )
    call = native.calls[0]
    assert call["args"][-1] == "-"
    assert call["kwargs"]["stdin_data"] == prompt
    assert call["kwargs"]["timeout"] == 29
    assert call["kwargs"]["inactivity_timeout"] == 17
    assert result.session_reference["session_id"] == call["child"]


@pytest.mark.parametrize("model", ["gpt-test", "other-model"])
def test_model_escalation_keeps_source(tmp_path, native, model):
    child = task("child", fork_from="plan", end=True)
    child["retry"] = 1
    path = write_flow(
        tmp_path,
        {"plan": task("plan", next="child"), "child": child},
        retry_escalation=dict(provider="codex", model=model),
    )
    native.responses["child"] = [dict(is_error=True)]
    assert run_flow(path, base_dir=tmp_path / ".fdsx").status == "completed"
    assert len(native.calls) == 3
    args = native.calls[-1]["args"]
    assert args[args.index("--model") + 1] == model
    assert (
        native.calls[1]["parent"]
        == native.calls[2]["parent"]
        == native.calls[0]["child"]
    )


@pytest.mark.parametrize("exit_code", [124, 127, 1])
def test_native_timeout_and_unavailable_history_never_fall_back(exit_code):
    parent = str(uuid4())
    with patch(
        "fdsx.providers.codex._run_subprocess",
        side_effect=[
            ProviderResult(0, "codex-cli 0.154.0", ""),
            ProviderResult(exit_code, "PRIVATE", "PRIVATE"),
        ],
    ) as subprocess:
        result = CodexProvider().execute_with_session(
            SessionRequest("child", {"provider": "codex", "session_id": parent}),
            prompt="child",
        )
    assert result.exit_code == exit_code
    assert result.stdout == ""
    assert "PRIVATE" not in result.stderr
    assert subprocess.call_count == 2
    assert "fork" in subprocess.call_args.kwargs["args"]


def test_forked_source_reexecution_uses_upstream(tmp_path, native):
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="subplan"),
            "subplan": task("subplan", fork_from="plan", next="child"),
            "child": task("child", fork_from="subplan", next="route"),
            "route": dict(
                type="choice",
                choices=[
                    dict(
                        variable="$._state_iterations.subplan",
                        operator="less_than",
                        value=2,
                        next="subplan",
                    )
                ],
                default="done",
            ),
            "done": dict(type="pass", end=True),
        },
    )
    assert run_flow(path, base_dir=tmp_path / ".fdsx").status == "completed"
    a, b, c, d, e = native.calls
    assert b["parent"] == d["parent"] == a["child"]
    assert c["parent"] == b["child"]
    assert e["parent"] == d["child"]


@pytest.mark.parametrize(
    "version", ["codex-cli 0.153.0", "codex-cli 0.155.0", "PRIVATE", ""]
)
def test_unqualified_versions_fail_before_prompt(version, caplog):
    with (
        patch(
            "fdsx.providers.codex._run_subprocess",
            return_value=ProviderResult(0, version, "PRIVATE"),
        ) as run,
        pytest.raises(ProviderSessionError, match=r"0\.154\.0"),
    ):
        CodexProvider().execute_with_session(SessionRequest("plan"), prompt="plan")
    assert run.call_count == 1
    assert run.call_args.kwargs["args"] == ["codex", "--version"]
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize(
    "source",
    [
        {"provider": "pi", "session_id": str(uuid4())},
        {"provider": "codex", "session_id": "--last"},
        {},
    ],
)
def test_invalid_saved_reference_rejected_before_subprocess(source):
    with (
        patch("fdsx.providers.codex._run_subprocess") as run,
        pytest.raises(ProviderSessionError, match="source reference"),
    ):
        CodexProvider().execute_with_session(
            SessionRequest("child", source), prompt="child"
        )
    run.assert_not_called()


def native_events(child, text="answer"):
    return [
        {"type": "thread.started", "thread_id": child},
        {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": {}},
    ]


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate_id",
        "missing_id",
        "missing_completion",
        "duplicate_completion",
        "array",
        "item",
        "text",
        "json",
    ],
)
def test_invalid_stream_never_publishes_reference(damage, caplog):
    events = native_events(str(uuid4()))
    if damage == "duplicate_id":
        events.insert(1, events[0])
    elif damage == "missing_id":
        events.pop(0)
    elif damage == "missing_completion":
        events.pop()
    elif damage == "duplicate_completion":
        events.append(events[-1])
    elif damage == "array":
        events.append([])
    elif damage == "item":
        events.append({"type": "item.completed", "item": None})
    elif damage == "text":
        events.append(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": ["PRIVATE"]},
            }
        )

    def replay(**kwargs):
        if kwargs["args"] == ["codex", "--version"]:
            return ProviderResult(0, "codex-cli 0.154.0", "")
        for event in events:
            kwargs["output_callback"](json.dumps(event))
        if damage == "json":
            kwargs["output_callback"]("PRIVATE malformed")
        return ProviderResult(0, "PRIVATE", "PRIVATE")

    with (
        patch("fdsx.providers.codex._run_subprocess", side_effect=replay),
        pytest.raises(ProviderSessionError, match="metadata"),
    ):
        CodexProvider().execute_with_session(SessionRequest("plan"), prompt="plan")
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("structured", [False, True])
def test_session_callbacks_schema_and_clean_output(structured):
    from fdsx.providers.codex import CodexOptions

    output, errors, starts = [], [], []
    child = str(uuid4())
    schema = {"type": "object", "required": ["ok"]} if structured else None
    final = '{"ok":true}' if structured else "APPROVED"
    paths = []

    def replay(**kwargs):
        args = kwargs["args"]
        if args == ["codex", "--version"]:
            return ProviderResult(0, "codex-cli 0.154.0", "")
        assert kwargs["timeout"] == 29
        assert kwargs["inactivity_timeout"] == 17
        assert kwargs["stderr_callback"] is None
        assert "completion_event" not in kwargs  # never terminate before native flush
        assert args.index("--sandbox") < args.index("fork")
        if schema:
            path = Path(args[args.index("--output-schema") + 1])
            assert json.loads(path.read_text()) == schema
            paths.append(path)
        kwargs["on_process_start"]("synthetic-process")
        events = native_events(child, final)
        events.insert(
            1,
            {
                "type": "item.started",
                "item": {"type": "command_execution", "command": "echo check"},
            },
        )
        for event in events:
            kwargs["output_callback"](json.dumps(event))
        return ProviderResult(0, "PRIVATE raw stream", "PRIVATE diagnostics")

    with patch("fdsx.providers.codex._run_subprocess", side_effect=replay):
        result = CodexProvider(
            CodexOptions(sandbox="read-only", inactivity_timeout=17)
        ).execute_with_session(
            SessionRequest("child", {"provider": "codex", "session_id": str(uuid4())}),
            prompt="child",
            timeout=29,
            output_schema=schema,
            output_callback=output.append,
            stderr_callback=errors.append,
            on_process_start=starts.append,
        )
    assert result.stdout == result.final_message == final
    assert result.session_reference == {"provider": "codex", "session_id": child}
    assert output == ["[tool: echo check]", final]
    assert errors == []
    assert starts == ["synthetic-process"]
    assert all(not path.exists() for path in paths)


def test_parallel_children_overlap_without_shared_adapter_metadata(tmp_path, native):
    from threading import Barrier

    rendezvous = Barrier(2, timeout=5)
    original = native.__call__

    def overlap(**kwargs):
        if "fork" in kwargs["args"]:
            rendezvous.wait()
        return original(**kwargs)

    with patch("fdsx.providers.codex._run_subprocess", side_effect=overlap):
        result = run_flow(
            internal_flow(tmp_path, "parallel"), base_dir=tmp_path / ".fdsx"
        )
    assert result.status == "completed"
    a, b, c = native.calls
    assert b["parent"] == c["parent"] == a["child"]
    assert len({a["child"], b["child"], c["child"]}) == 3


def test_extraction_and_machine_readable_cli_output(tmp_path, native):
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="child"),
            "child": task(
                "child",
                fork_from="plan",
                end=True,
                result_path="$.answer",
                extract={
                    "strategy": ["keyword"],
                    "pattern": "APPROVED|REJECTED",
                    "result_path": "$.decision",
                },
            ),
        },
    )
    native.responses["child"] = [{"result": "APPROVED"}]
    result = CliRunner().invoke(app, ["run", str(path), "--quiet"])
    assert result.exit_code == 0, result.output
    assert result.stdout == ""  # run writes its UI to stderr
    native.responses["child"] = [{"result": "APPROVED"}]
    value = run_flow(path, base_dir=tmp_path / "second-run").results
    assert value["answer"] == value["decision"] == "APPROVED"
    assert "session_id" not in json.dumps(value)
    assert all(call["child"] not in result.stdout for call in native.calls)


def test_crash_after_native_completion_before_publication_reruns_source(
    tmp_path, native
):
    path = write_flow(
        tmp_path,
        {
            "plan": task(
                "plan",
                next="child",
                extract={
                    "strategy": ["keyword"],
                    "pattern": "output",
                    "result_path": "$.decision",
                },
            ),
            "child": task("child", fork_from="plan", end=True),
        },
    )
    with (
        patch(
            "fdsx.core.compiler.execution.extract_value",
            side_effect=RuntimeError("publication crash"),
        ),
        pytest.raises(RuntimeError, match="publication crash"),
    ):
        run_flow(path, thread_id="publication", base_dir=tmp_path / ".fdsx")
    assert len(native.calls) == 1  # native source completed, but no accepted reference
    flow, errors = load_flow(path)
    assert not errors
    saver = CheckpointManager(tmp_path / ".fdsx").get_checkpointer()
    try:
        saved = (
            compile_flow(flow, checkpointer=saver)
            .graph.get_state({"configurable": {"thread_id": "publication"}})
            .values
        )
        assert not saved.get("_session_references")
    finally:
        saver.conn.close()
    assert resume_flow("publication", tmp_path / ".fdsx", path).status == "completed"
    orphan, accepted, child = native.calls
    assert orphan["child"] != accepted["child"]
    assert child["parent"] == accepted["child"]


def test_external_source_change_is_not_rejected_or_snapshotted(tmp_path, native):
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="implement"),
            "implement": task("implement", fork_from="plan", next="review"),
            "review": task(
                "review", fork_from="plan", result_path="$.answer", end=True
            ),
        },
    )
    native_history = {}
    original = native.__call__

    def current_native_history(**kwargs):
        if kwargs["args"] == ["codex", "--version"]:
            return original(**kwargs)
        action = kwargs["stdin_data"].split("\n")[0]
        if action == "implement":
            # Model an external continuation; FDSX must still select the saved ID.
            native_history[native.calls[0]["child"]] = "externally continued"
        if action == "review":
            parent = kwargs["args"][kwargs["args"].index("fork") + 1]
            native.responses["review"] = [{"result": native_history[parent]}]
        result = original(**kwargs)
        if action == "plan":
            native_history[native.calls[0]["child"]] = "original"
        return result

    with patch(
        "fdsx.providers.codex._run_subprocess", side_effect=current_native_history
    ):
        result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.results["answer"] == "externally continued"
    assert (
        native.calls[1]["parent"]
        == native.calls[2]["parent"]
        == native.calls[0]["child"]
    )


@pytest.mark.parametrize("mode", ["empty_output", "error_event", "failed_turn"])
def test_session_completion_failure_cleans_schema(mode, caplog):
    paths = []

    def replay(**kwargs):
        args = kwargs["args"]
        if args == ["codex", "--version"]:
            return ProviderResult(0, "codex-cli 0.154.0", "")
        paths.append(Path(args[args.index("--output-schema") + 1]))
        events = native_events(str(uuid4()))
        if mode == "empty_output":
            events.pop(1)
        else:
            events.insert(
                1,
                {
                    "type": "error" if mode == "error_event" else "turn.failed",
                    "message": "PRIVATE",
                },
            )
        for event in events:
            kwargs["output_callback"](json.dumps(event))
        return ProviderResult(0, "PRIVATE raw stream", "PRIVATE diagnostics")

    with patch("fdsx.providers.codex._run_subprocess", side_effect=replay):
        if mode == "empty_output":
            with pytest.raises(ProviderSessionError, match="output is missing"):
                CodexProvider().execute_with_session(
                    SessionRequest("plan"),
                    prompt="plan",
                    output_schema={"type": "object"},
                )
        else:
            result = CodexProvider().execute_with_session(
                SessionRequest("plan"), prompt="plan", output_schema={"type": "object"}
            )
            assert result.exit_code == 1
            assert result.session_reference is None
            assert result.stdout == ""
            assert "PRIVATE" not in result.stderr
    assert paths and all(not path.exists() for path in paths)
    assert "PRIVATE" not in caplog.text

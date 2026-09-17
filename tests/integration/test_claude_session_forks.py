"""Offline Claude CLI contract checks; these do not qualify native support."""

import json
import sys
from pathlib import Path
from threading import Lock, Thread
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
from fdsx.providers.claude import ClaudeProvider


def task(prompt, **kwargs):
    return dict(
        type="task",
        provider="claude",
        model="sonnet",
        prompt_template=prompt,
        retry=0,
        **kwargs,
    )


def write_flow(tmp_path, states, **kwargs):
    path = tmp_path / "flow.yaml"
    path.write_text(
        yaml.safe_dump(
            dict(
                name="claude-forks",
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
        prompt = kwargs["stdin_data"] or args[2]
        action = prompt.split("\n")[0]
        parent = args[args.index("--resume") + 1] if "--resume" in args else None
        assert ("--fork-session" in args) == (parent is not None)
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
        event = dict(type="result", result=action + " output", session_id=child)
        event.update(response)
        callback = kwargs.get("output_callback")
        if callback:
            for stream_event in self.stream_events:
                callback(json.dumps(stream_event))
            callback(json.dumps(event))
        return ProviderResult(
            1 if event.get("is_error") else 0,
            json.dumps(event),
            "PRIVATE native diagnostic",
        )


@pytest.fixture
def native():
    fixture = NativeCLI()
    with (
        patch("fdsx.providers.claude._run_subprocess", side_effect=fixture),
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
        provider="claude",
        model="sonnet",
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


@pytest.mark.parametrize("update_inputs", [False, True])
@pytest.mark.parametrize("from_state", ["review", "plan"])
def test_blocked_review_recovery_preserves_source_choice(
    tmp_path, native, update_inputs, from_state
):
    native.responses["inspect"] = [dict(result="BLOCKED"), dict(result="APPROVED")]
    path = write_flow(
        tmp_path,
        {
            "plan": task("plan", next="review"),
            "review": task(
                "inspect\n{feedback}",
                fork_from="plan",
                result_path="$.decision",
                next="route",
            ),
            "route": dict(
                type="choice",
                choices=[
                    dict(
                        variable="$.decision",
                        operator="equals",
                        value="APPROVED",
                        next="done",
                    )
                ],
                default="blocked",
            ),
            "blocked": dict(type="fail", error="BLOCKED", cause="Needs changes"),
            "done": dict(type="pass", end=True),
        },
    )
    first = run_flow(
        path,
        thread_id="blocked",
        base_dir=tmp_path / ".fdsx",
        inputs={"feedback": "original"},
    )
    assert first.status == "aborted"
    result = resume_flow(
        "blocked",
        tmp_path / ".fdsx",
        path,
        from_state=from_state,
        input_updates={"feedback": "revised"} if update_inputs else None,
        confirm_inputs=lambda *_: True,
    )
    assert result.status == "completed"
    assert [call["action"] for call in native.calls] == (
        ["plan", "inspect", "inspect"]
        if from_state == "review"
        else ["plan", "inspect", "plan", "inspect"]
    )
    source = native.calls[0] if from_state == "review" else native.calls[2]
    assert native.calls[-1]["parent"] == source["child"]
    assert native.calls[-1]["child"] != native.calls[1]["child"]
    call = native.calls[-1]
    prompt = call["kwargs"]["stdin_data"] or call["args"][2]
    assert prompt == "inspect\n" + ("revised" if update_inputs else "original")


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
            "plan": {"provider": "claude", "session_id": native.calls[0]["child"]}
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
    request = SessionRequest("child", {"provider": "claude", "session_id": parent})
    if damage == "error":
        result = ClaudeProvider().execute_with_session(request, **kwargs)
        assert result.exit_code != 0
        assert "rerun the source" in result.stderr
        assert "PRIVATE" not in result.stderr
    else:
        with pytest.raises(ProviderSessionError, match="child reference"):
            ClaudeProvider().execute_with_session(request, **kwargs)
    assert len(native.calls) == 1
    assert "PRIVATE" not in repr(output) + caplog.text


@pytest.mark.parametrize(
    "provider", ["pi", "codex", "cursor", "grok", "opencode", "gemini"]
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
    [dict(provider="pi", model="sonnet")],
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
    child = dict(provider="claude", model="sonnet", fork_from="plan", retry=0)
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
            "fdsx.providers.claude._run_subprocess", side_effect=interrupt_second_visit
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
        profiles={"same": dict(provider="claude", model="sonnet")},
    )
    config_dir = tmp_path / ".fdsx"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "retry_escalation:\n  provider: codex\n  model: other\n"
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
    child["model"] = "opus"
    path = write_flow(tmp_path, {"plan": task("plan", next="child"), "child": child})
    assert run_flow(path, base_dir=tmp_path / ".fdsx").status == "completed"
    source, destination = native.calls
    assert destination["parent"] == source["child"]
    args = destination["args"]
    assert args[args.index("--model") + 1] == "opus"


@pytest.mark.parametrize(
    "payload",
    [
        "not json PRIVATE",
        "[]",
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":null}}',
        '{"type":"result","result":"ok"}',
    ],
)
def test_malformed_stream_is_domain_error(payload, caplog):
    def malformed(**kwargs):
        kwargs["output_callback"](payload)
        return ProviderResult(0, payload, "PRIVATE")

    with (
        patch("fdsx.providers.claude._run_subprocess", side_effect=malformed),
        pytest.raises(ProviderSessionError),
    ):
        ClaudeProvider().execute_with_session(SessionRequest("plan"), prompt="plan")
    assert "PRIVATE" not in caplog.text


def test_session_large_prompt_timeout_and_callbacks(native):
    from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD
    from fdsx.providers.claude import ClaudeOptions

    prompt = "x" * ARG_MAX_STDIN_THRESHOLD
    result = ClaudeProvider(ClaudeOptions(inactivity_timeout=17)).execute_with_session(
        SessionRequest("plan"), prompt=prompt, timeout=29
    )
    call = native.calls[0]
    assert call["args"][2] == "-"
    assert call["kwargs"]["stdin_data"] == prompt
    assert call["kwargs"]["timeout"] == 29
    assert call["kwargs"]["inactivity_timeout"] == 17
    assert result.session_reference["session_id"] == call["child"]


@pytest.mark.parametrize("model", ["sonnet", "opus"])
def test_model_escalation_keeps_source(tmp_path, native, model):
    child = task("child", fork_from="plan", end=True)
    child["retry"] = 1
    path = write_flow(
        tmp_path,
        {"plan": task("plan", next="child"), "child": child},
        retry_escalation=dict(provider="claude", model=model),
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
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(exit_code, "PRIVATE", "PRIVATE"),
    ) as subprocess:
        result = ClaudeProvider().execute_with_session(
            SessionRequest("child", {"provider": "claude", "session_id": parent}),
            prompt="child",
        )
    assert result.exit_code == exit_code
    assert result.stdout == ""
    assert "PRIVATE" not in result.stderr
    assert subprocess.call_count == 1
    assert "--fork-session" in subprocess.call_args.kwargs["args"]


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


def stream_event(event_type, **fields):
    return dict(type="stream_event", event=dict(type=event_type, **fields))


def successful_stream():
    return [
        stream_event(
            "content_block_delta", delta=dict(type="text_delta", text="APPROVED\n")
        ),
        stream_event(
            "content_block_start", content_block=dict(type="tool_use", name="Read")
        ),
        stream_event(
            "content_block_delta",
            delta=dict(
                type="input_json_delta", partial_json='{"file_path":"code.txt"}'
            ),
        ),
        stream_event("content_block_stop"),
        stream_event("content_block_delta", delta=dict(type="text_delta", text="done")),
    ]


@pytest.mark.parametrize("structured", [False, True])
def test_streamed_session_workflow_output(tmp_path, native, structured):
    native.stream_events = successful_stream()
    child = task("child", fork_from="plan", end=True, result_path="$.answer")
    if structured:
        del child["result_path"]
        (tmp_path / "schema.json").write_text('{"type":"object","required":["ok"]}')
        child["structured_output"] = dict(schema="schema.json", result_path="$.value")
        native.responses["child"] = [dict(structured_output={"ok": True})]
    else:
        child["extract"] = dict(
            strategy=["keyword"], pattern="APPROVED|REJECTED", result_path="$.decision"
        )
    path = write_flow(tmp_path, {"plan": task("plan", next="child"), "child": child})
    result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    if structured:
        assert result.results["value"] == {"ok": True}
        assert "APPROVED" not in json.dumps(result.results["value"])
    else:
        assert result.results["answer"] == "APPROVED\ndone"
        assert result.results["decision"] == "APPROVED"
    assert "session_id" not in json.dumps(result.results)
    assert all(call["child"] not in json.dumps(result.results) for call in native.calls)


@pytest.mark.parametrize("structured", [False, True])
def test_session_stream_callbacks_and_inactivity_hooks(structured):
    output, summaries, hooks = [], [], []
    child = str(uuid4())
    event = dict(type="result", result="done", session_id=child)
    if structured:
        event["structured_output"] = {"ok": True}

    def replay(**kwargs):
        kwargs["on_inactivity_hooks"](
            lambda: hooks.append("suspend"), lambda: hooks.append("resume")
        )
        for item in [*successful_stream(), event]:
            kwargs["output_callback"](json.dumps(item))
        assert kwargs["completion_event"].is_set()
        return ProviderResult(0, "", "")

    with patch("fdsx.providers.claude._run_subprocess", side_effect=replay):
        result = ClaudeProvider().execute_with_session(
            SessionRequest("child", {"provider": "claude", "session_id": str(uuid4())}),
            prompt="child",
            output_callback=output.append,
            summary_callback=summaries.append,
            output_schema={"type": "object"} if structured else None,
        )
    assert output == ["APPROVED", "done"]
    assert summaries == ["[Read] code.txt"]
    assert hooks == ["suspend", "resume"]
    assert result.stdout == ('{"ok": true}' if structured else "APPROVED\ndone")
    assert result.final_message == ('{"ok": true}' if structured else "done")
    assert result.session_reference == {"provider": "claude", "session_id": child}
    assert child not in "".join(output + summaries)


@pytest.mark.parametrize("damage", ["nested_tool", "integer"])
def test_session_parsing_limits_keep_reader_alive(damage, caplog, capsys):
    if damage == "nested_tool":
        depth = max(10000, sys.getrecursionlimit() * 10)
        nested = '{"PRIVATE":' + "[" * depth + "0" + "]" * depth + "}"
        lines = [
            json.dumps(item)
            for item in [
                stream_event(
                    "content_block_start",
                    content_block=dict(type="tool_use", name="Read"),
                ),
                stream_event(
                    "content_block_delta",
                    delta=dict(type="input_json_delta", partial_json=nested),
                ),
                stream_event("content_block_stop"),
            ]
        ]
    else:
        limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
        if not limit:
            pytest.skip("Runtime does not enforce an integer decoding limit")
        lines = ['{"PRIVATE":' + "9" * (limit + 1) + "}"]
    lines.append(json.dumps(dict(type="result", result="ok", session_id=str(uuid4()))))
    consumed, failures = [], []

    def replay(**kwargs):
        def read_stdout():
            for line in lines:
                kwargs["output_callback"](line)
                consumed.append(True)

        reader = Thread(target=read_stdout)
        reader.start()
        reader.join(timeout=5)
        assert not reader.is_alive()
        return ProviderResult(0, "", "PRIVATE")

    with (
        patch("threading.excepthook", side_effect=failures.append),
        patch(
            "fdsx.providers.claude._run_subprocess", side_effect=replay
        ) as invocation,
        pytest.raises(
            ProviderSessionError, match="stream metadata is malformed"
        ) as error,
    ):
        ClaudeProvider().execute_with_session(
            SessionRequest("child", {"provider": "claude", "session_id": str(uuid4())}),
            prompt="child",
        )
    assert not failures
    assert len(consumed) == len(lines)
    assert invocation.call_count == 1
    captured = capsys.readouterr()
    diagnostics = str(error.value) + caplog.text + captured.err + captured.out
    assert "PRIVATE" not in diagnostics
    assert "Traceback" not in diagnostics

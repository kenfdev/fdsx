"""Common instructions observed at the provider subprocess boundary."""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from fdsx.core.engine import run_flow
from fdsx.providers.base import ProviderResult


@pytest.fixture(autouse=True)
def mock_provider_environment():
    with (
        patch("fdsx.providers.cursor.shutil.which", return_value="/mock/agent"),
        patch("fdsx.core.compiler.execution.retry_wait"),
    ):
        yield


def write_config(tmp_path, value, *, global_config=False):
    directory = tmp_path / ("xdg/fdsx" if global_config else ".fdsx")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text(yaml.safe_dump(value))


def write_flow(tmp_path, **task):
    path = tmp_path / "flow.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "prefix",
                "description": "Prefix integration",
                "start_at": "work",
                "states": {
                    "work": {
                        "type": "task",
                        "provider": "claude",
                        "model": "test-model",
                        "prompt_template": "task",
                        "end": True,
                        **task,
                    }
                },
            }
        )
    )
    return path


@pytest.mark.parametrize(
    "global_value,project_value,expected",
    [
        ({}, {}, "task"),
        ({"prompt_prefix": "global"}, {}, "global\n\ntask"),
        ({}, {"prompt_prefix": "local"}, "local\n\ntask"),
        ({"prompt_prefix": "global"}, {"prompt_prefix": "local"}, "local\n\ntask"),
        *[
            ({"prompt_prefix": "global"}, {"prompt_prefix": value}, "task")
            for value in ["", " ", "\n\t"]
        ],
        ({}, {"prompt_prefix": "  {literal}\n"}, "  {literal}\n\n\ntask"),
        ({}, {"prompt_prefix": "\t {task}  \n\t"}, "\t {task}  \n\t\n\ntask"),
        ({"prompt_prefix": "\n\t"}, {}, "task"),
    ],
)
def test_config_precedence_delivers_exact_body(
    tmp_path, global_value, project_value, expected
):
    write_config(tmp_path, global_value, global_config=True)
    write_config(tmp_path, project_value)
    path = write_flow(tmp_path)
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "ok", ""),
    ) as call:
        run_flow(path, base_dir=tmp_path / ".fdsx")
    assert call.call_count == 1
    args = call.call_args.kwargs["args"]
    assert args[args.index("-p") + 1] == expected
    assert "--system-prompt" not in args
    assert "--append-system-prompt" not in args


def test_run_flow_adds_inline_prefix_once_to_prompt_body(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "isolated-xdg"))
    write_config(tmp_path, {"prompt_prefix": "policy"})
    path = write_flow(tmp_path)

    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "ok", ""),
    ) as call:
        run_flow(path, base_dir=tmp_path / ".fdsx")

    args = call.call_args.kwargs["args"]
    prompt = args[args.index("-p") + 1]
    assert prompt == "policy\n\ntask"
    assert prompt.count("policy") == 1


@pytest.mark.parametrize(
    "value", [None, 123, True, ["private-body"], {"private-body": "x"}]
)
@pytest.mark.parametrize("global_config", [False, True])
def test_invalid_config_rejected_before_task_even_when_overridden(
    tmp_path, value, global_config, capsys
):
    write_config(tmp_path, {"prompt_prefix": value}, global_config=global_config)
    if global_config:
        write_config(tmp_path, {"prompt_prefix": "valid"})
    with (
        patch("fdsx.providers.claude._run_subprocess") as call,
        pytest.raises(ValueError, match="prompt_prefix") as error,
    ):
        run_flow(write_flow(tmp_path), base_dir=tmp_path / ".fdsx")
    call.assert_not_called()
    assert "private-body" not in str(error.value) + capsys.readouterr().err


@pytest.mark.parametrize("mode", ["rule", "recovery"])
@pytest.mark.usefixtures("prefix_storage")
def test_internal_extraction_prompt_unchanged_with_prefixed_task(tmp_path, mode):
    extract = {
        "strategy": ["keyword"],
        "pattern": "APPROVED|REJECTED",
        "result_path": "$.decision",
    }
    extra = {}
    if mode == "rule":
        extract["fallback"] = {
            "provider": "claude",
            "model": "test-model",
            "prompt": "Classify: {output}",
        }
    else:
        extra["extraction_fallback"] = {"provider": "claude", "model": "test-model"}
    path = save_states(
        tmp_path, {"work": ai_task(extract=extract, result_path="$.raw", end=True)}
    )
    requests = []
    for prefix in ["", "policy"]:
        write_config(tmp_path, {"prompt_prefix": prefix, **extra})
        with patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=[
                ProviderResult(0, "unclear output", ""),
                ProviderResult(0, "APPROVED", ""),
            ],
        ) as call:
            result = run_flow(path, base_dir=tmp_path / ".fdsx")
        assert result.results["decision"] == "APPROVED"
        requests.append(list(map(claude_body, call.call_args_list)))
    assert requests[0][0] == "task"
    assert requests[1][0] == "policy\n\ntask"
    assert len(requests[0]) == len(requests[1]) == 2
    assert requests[0][1] == requests[1][1]
    assert "policy" not in requests[1][1]


@pytest.mark.usefixtures("prefix_storage")
def test_selector_excluded_and_multiple_workflows_share_config(tmp_path):
    from fdsx.core.config import load_config
    from fdsx.core.selector import select_workflow

    first = write_flow(tmp_path)
    second = tmp_path / "second.yaml"
    second.write_text(first.read_text().replace("name: prefix", "name: second"))
    requests = []
    for prefix in ["", "policy"]:
        write_config(tmp_path, {"prompt_prefix": prefix}, global_config=True)
        with patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=[
                ProviderResult(0, "prefix", ""),
                ProviderResult(0, "ok", ""),
                ProviderResult(0, "ok", ""),
            ],
        ) as call:
            chosen = select_workflow(
                "task",
                [(first, "first flow", "prefix"), (second, "second flow", "second")],
                load_config().workflow_selector,
            )
            assert chosen == first
            run_flow(chosen, base_dir=tmp_path / ".fdsx")
            run_flow(second, base_dir=tmp_path / ".fdsx")
        requests.append(list(map(claude_body, call.call_args_list)))
    assert requests[0][0] == requests[1][0]
    assert requests[0][1:] == ["task", "task"]
    assert requests[1][1:] == ["policy\n\ntask", "policy\n\ntask"]
    assert not (tmp_path / "AGENTS.md").exists()


@pytest.mark.usefixtures("prefix_storage")
def test_system_and_hooks_remain_commands_with_prefixed_ai(tmp_path):
    write_config(
        tmp_path,
        {
            "prompt_prefix": "policy",
            "hooks": {"on_workflow_start": [{"command": "echo hook > hook.txt"}]},
        },
    )
    path = save_states(
        tmp_path,
        {
            "shell": {
                "type": "task",
                "provider": "system",
                "command": "echo shell",
                "result_path": "$.shell",
                "next": "work",
            },
            "work": ai_task(end=True),
        },
    )
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "ok", ""),
    ) as call:
        result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.results["shell"] == "shell"
    assert (tmp_path / "hook.txt").read_text() == "hook\n"
    assert list(map(claude_body, call.call_args_list)) == ["policy\n\ntask"]


@pytest.mark.usefixtures("prefix_storage")
def test_config_changes_during_run_apply_only_to_next_run(tmp_path):
    write_config(tmp_path, {"prompt_prefix": "old"})
    path = save_states(
        tmp_path, {"first": ai_task(next="second"), "second": ai_task(end=True)}
    )

    def respond(**kwargs):
        write_config(tmp_path, {"prompt_prefix": "new"})
        return ProviderResult(0, "ok", "")

    with patch("fdsx.providers.claude._run_subprocess", side_effect=respond) as call:
        run_flow(path, base_dir=tmp_path / ".fdsx")
        run_flow(path, base_dir=tmp_path / ".fdsx")
    assert list(map(claude_body, call.call_args_list)) == [
        "old\n\ntask",
        "old\n\ntask",
        "new\n\ntask",
        "new\n\ntask",
    ]


def claude_body(call):
    args = call.kwargs["args"]
    return args[args.index("-p") + 1]


@pytest.mark.parametrize(
    "provider", ["claude", "codex", "cursor", "gemini", "grok", "opencode"]
)
@pytest.mark.parametrize("from_file", [False, True])
@pytest.mark.usefixtures("prefix_storage")
def test_all_providers_receive_literal_prefix_after_body_substitution(
    tmp_path, provider, from_file
):
    write_config(tmp_path, {"prompt_prefix": "  {literal}\n"})
    task = {"provider": provider, "prompt_template": "work {task}"}
    if from_file:
        (tmp_path / "prompt.txt").write_text("work {task}")
        task.update(prompt_template=None, prompt_file="prompt.txt")

    def respond(**kwargs):
        if provider == "grok":
            kwargs["output_callback"](
                '{"type":"end","stopReason":"end_turn","structuredOutput":{"ok":true}}'
            )
        return ProviderResult(0, "ok", "")

    with patch(
        f"fdsx.providers.{provider}._run_subprocess", side_effect=respond
    ) as call:
        run_flow(
            write_flow(tmp_path, **task),
            inputs={"task": "resolved"},
            base_dir=tmp_path / ".fdsx",
        )
    args = call.call_args.kwargs["args"]
    assert "  {literal}\n\n\nwork resolved" in args
    assert "--system-prompt" not in args
    assert "--append-system-prompt" not in args


def save_states(tmp_path, states, **flow):
    path = tmp_path / "flow.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": "prefix",
                "description": "Prefix coverage",
                "start_at": next(iter(states)),
                "states": states,
                **flow,
            }
        )
    )
    return path


def ai_task(**changes):
    return {
        "type": "task",
        "provider": "claude",
        "model": "test-model",
        "prompt_template": "task",
        "retry": 0,
        **changes,
    }


@pytest.mark.parametrize("mode", ["parallel", "map", "loop"])
@pytest.mark.usefixtures("prefix_storage")
def test_execution_structures_deliver_once_per_task(tmp_path, mode):
    write_config(tmp_path, {"prompt_prefix": "policy"})
    if mode == "parallel":
        states = {
            "work": {
                "type": "parallel",
                "result_path": "$.results",
                "branches": [
                    ai_task(prompt_template="one"),
                    ai_task(prompt_template="two"),
                ],
                "end": True,
            }
        }
        expected = ["policy\n\none", "policy\n\ntwo"]
    elif mode == "map":
        states = {
            "setup": {
                "type": "pass",
                "parameters": {"$.items": ["one", "two"]},
                "next": "work",
            },
            "work": {
                "type": "map",
                "result_path": "$.results",
                "items_path": "$.items",
                "iterator": {
                    "states": [
                        ai_task(
                            name="item",
                            prompt_template="{item}",
                            result_path="$.result",
                        )
                    ]
                },
                "end": True,
            },
        }
        expected = ["policy\n\none", "policy\n\ntwo"]
    else:
        states = {
            "work": ai_task(next="decide", result_path="$.answer"),
            "decide": {
                "type": "choice",
                "choices": [
                    {
                        "variable": "$.answer",
                        "operator": "equals",
                        "value": "again",
                        "next": "work",
                    }
                ],
                "default": "done",
            },
            "done": {"type": "pass", "end": True},
        }
        expected = ["policy\n\ntask"] * 2
    with patch(
        "fdsx.providers.claude._run_subprocess",
        side_effect=[ProviderResult(0, "again", ""), ProviderResult(0, "done", "")],
    ) as call:
        run_flow(save_states(tmp_path, states), base_dir=tmp_path / ".fdsx")
    assert sorted(map(claude_body, call.call_args_list)) == expected


@pytest.mark.parametrize("escalate", [False, True])
@pytest.mark.usefixtures("prefix_storage")
def test_retry_and_provider_switch_keep_single_prefix(tmp_path, escalate):
    write_config(tmp_path, {"prompt_prefix": "policy"})
    flow = (
        {"retry_escalation": {"provider": "codex", "model": "test-model"}}
        if escalate
        else {}
    )
    path = save_states(tmp_path, {"work": ai_task(retry=1, end=True)}, **flow)
    with (
        patch("fdsx.core.compiler.execution.retry_wait"),
        patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=[ProviderResult(1, "", "fail"), ProviderResult(0, "ok", "")],
        ) as primary,
        patch(
            "fdsx.providers.codex._run_subprocess",
            return_value=ProviderResult(0, "ok", ""),
        ) as secondary,
    ):
        run_flow(path, base_dir=tmp_path / ".fdsx")
    assert [claude_body(c) for c in primary.call_args_list] == ["policy\n\ntask"] * (
        1 if escalate else 2
    )
    if escalate:
        assert "policy\n\ntask" in secondary.call_args.kwargs["args"]
    else:
        secondary.assert_not_called()


@pytest.mark.usefixtures("prefix_storage")
def test_structured_retry_keeps_schema_and_feedback(tmp_path):
    write_config(tmp_path, {"prompt_prefix": "policy"})
    (tmp_path / "schema.json").write_text(
        '{"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"}}}'
    )
    path = write_flow(
        tmp_path,
        retry=1,
        structured_output={"schema": "schema.json", "result_path": "$.payload"},
    )
    with (
        patch("fdsx.core.compiler.execution.retry_wait"),
        patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=[
                ProviderResult(0, '{"ok":"bad"}', ""),
                ProviderResult(0, '{"ok":true}', ""),
            ],
        ) as call,
    ):
        result = run_flow(path, base_dir=tmp_path / ".fdsx")
    assert result.results["payload"] == {"ok": True}
    bodies = list(map(claude_body, call.call_args_list))
    assert bodies[0] == "policy\n\ntask"
    assert bodies[1].startswith("policy\n\ntask\n\nYour previous response")
    assert bodies[1].count("policy") == 1
    assert "boolean" in bodies[1]
    assert all("--json-schema" in c.kwargs["args"] for c in call.call_args_list)


@pytest.mark.parametrize(
    "updated,expected",
    [
        ({"prompt_prefix": "new"}, "new\n\nafter"),
        ({"prompt_prefix": ""}, "after"),
        ({"prompt_prefix": "\n "}, "after"),
        ({}, "global\n\nafter"),
        ({"prompt_prefix": None}, None),
        ({"prompt_prefix": ["private-body"]}, None),
    ],
)
@pytest.mark.parametrize("global_update", [False, True])
def test_resume_uses_current_config_before_remaining_task(
    tmp_path, updated, expected, global_update
):
    from fdsx.core.engine import resume_flow

    write_config(tmp_path, {"prompt_prefix": "global"}, global_config=True)
    write_config(tmp_path, {"prompt_prefix": "old"})
    path = save_states(
        tmp_path,
        {
            "before": ai_task(prompt_template="before", next="wait"),
            "wait": {
                "type": "wait",
                "result_path": "$.approval",
                "message": "Continue?",
                "choices": ["yes"],
                "next": "after",
            },
            "after": ai_task(prompt_template="after", end=True),
        },
    )
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "ok", ""),
    ) as call:
        with (
            patch(
                "fdsx.core.engine.interrupts.display_wait_prompt",
                side_effect=RuntimeError("interrupted"),
            ),
            pytest.raises(RuntimeError, match="Flow execution failed"),
        ):
            run_flow(path, thread_id="resume-prefix", base_dir=tmp_path / ".fdsx")
        assert list(map(claude_body, call.call_args_list)) == ["old\n\nbefore"]
        call.reset_mock()
        if global_update:
            # A valid project override must not hide invalid global settings
            # during resume. Otherwise remove the override to observe changes.
            write_config(tmp_path, updated, global_config=True)
            write_config(
                tmp_path, {"prompt_prefix": "valid"} if expected is None else {}
            )
            if not updated:
                expected = "after"
        else:
            write_config(tmp_path, updated)
        with patch("builtins.input", return_value="1"):
            if expected is None:
                with pytest.raises(RuntimeError, match="prompt_prefix"):
                    resume_flow("resume-prefix", tmp_path / ".fdsx", path)
                call.assert_not_called()
            else:
                resume_flow("resume-prefix", tmp_path / ".fdsx", path)
                assert list(map(claude_body, call.call_args_list)) == [expected]


@pytest.mark.parametrize(
    "raw",
    ["prompt_prefix:\n", "prompt_prefix: null\n", "prompt_prefix: [private-body]\n"],
)
@pytest.mark.parametrize("command", ["run", "resume"])
@pytest.mark.parametrize("global_config", [False, True])
def test_cli_invalid_config_reports_safe_reason(tmp_path, raw, command, global_config):
    from typer.testing import CliRunner

    from fdsx.cli.main import app

    write_config(tmp_path, {}, global_config=global_config)
    directory = tmp_path / ("xdg/fdsx" if global_config else ".fdsx")
    (directory / "config.yaml").write_text(raw)
    if global_config:
        write_config(tmp_path, {"prompt_prefix": "valid"})
    args = (
        ["run", str(write_flow(tmp_path))]
        if command == "run"
        else ["resume", "--thread-id", "unused"]
    )
    with patch("fdsx.providers.claude._run_subprocess") as call:
        result = CliRunner().invoke(app, args)
    assert result.exit_code != 0
    assert "prompt_prefix" in result.stderr
    assert "string" in result.stderr
    assert "private-body" not in result.stderr
    call.assert_not_called()


@pytest.mark.parametrize("option", ["system_prompt", "append_system_prompt"])
@pytest.mark.usefixtures("prefix_storage")
def test_prefix_coexists_with_provider_system_options_and_profiles(tmp_path, option):
    write_config(
        tmp_path,
        {
            "prompt_prefix": "policy",
            "providers": {"claude": {option: "system {task}"}},
            "profiles": {"worker": {"provider": "claude", "model": "test-model"}},
        },
        global_config=True,
    )
    write_config(tmp_path, {"providers": {"claude": {"permission_mode": "default"}}})
    path = save_states(
        tmp_path,
        {
            "work": {
                "type": "task",
                "profile": "worker",
                "prompt_template": "body {task}",
                "end": True,
            }
        },
    )
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "ok", ""),
    ) as call:
        run_flow(path, inputs={"task": "resolved"}, base_dir=tmp_path / ".fdsx")
    args = call.call_args.kwargs["args"]
    assert claude_body(call.call_args) == "policy\n\nbody resolved"
    flag = "--" + option.replace("_", "-")
    assert args[args.index(flag) + 1] == "system resolved"
    assert args[args.index("--permission-mode") + 1] == "default"


def test_file_prefix_preserves_utf8_and_line_endings(tmp_path):
    write_config(tmp_path, {"prompt_prefix_file": "rules.txt"})
    (tmp_path / ".fdsx/rules.txt").write_bytes("  日本語 {task}\r\nrule\r  ".encode())
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "ok", ""),
    ) as call:
        run_flow(write_flow(tmp_path), base_dir=tmp_path / ".fdsx")
    assert claude_body(call.call_args) == "  日本語 {task}\r\nrule\r  \n\ntask"


@pytest.fixture(params=["inline", "file"])
def prefix_storage(request, monkeypatch):
    """Run the shared delivery contract with either configuration spelling."""
    if request.param == "inline":
        return
    original = write_config

    def write_file_config(tmp_path, value, *, global_config=False):
        value = dict(value)
        if isinstance(value.get("prompt_prefix"), str):
            directory = tmp_path / ("xdg/fdsx" if global_config else ".fdsx")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "policy.txt").write_bytes(value.pop("prompt_prefix").encode())
            value["prompt_prefix_file"] = "policy.txt"
        original(tmp_path, value, global_config=global_config)

    monkeypatch.setattr(__name__ + ".write_config", write_file_config)


@pytest.mark.parametrize(
    "global_value,project_value,content,expected",
    [
        ({"prompt_prefix_file": "rules.txt"}, {}, "project", "global\n\ntask"),
        ({}, {"prompt_prefix_file": "rules.txt"}, "project", "project\n\ntask"),
        (
            {"prompt_prefix": "global"},
            {"prompt_prefix_file": "rules.txt"},
            "project",
            "project\n\ntask",
        ),
        (
            {"prompt_prefix_file": "missing.txt"},
            {"prompt_prefix": "project"},
            "unused",
            "project\n\ntask",
        ),
        (
            {"prompt_prefix_file": "missing.txt"},
            {"prompt_prefix_file": "rules.txt"},
            "project",
            "project\n\ntask",
        ),
        ({"prompt_prefix_file": "rules.txt"}, {"prompt_prefix": ""}, "unused", "task"),
        *[
            (
                {"prompt_prefix_file": "rules.txt"},
                {"prompt_prefix_file": "rules.txt"},
                value,
                "task",
            )
            for value in ["", " \t\r\n"]
        ],
    ],
)
def test_file_and_inline_choices_replace_as_a_pair(
    tmp_path, global_value, project_value, content, expected
):
    write_config(tmp_path, global_value, global_config=True)
    write_config(tmp_path, project_value)
    (tmp_path / "xdg/fdsx/rules.txt").write_text("global")
    (tmp_path / ".fdsx/rules.txt").write_text(content)
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "ok", ""),
    ) as call:
        run_flow(write_flow(tmp_path), base_dir=tmp_path / ".fdsx")
    assert list(map(claude_body, call.call_args_list)) == [expected]


@pytest.mark.parametrize("global_config", [False, True])
@pytest.mark.parametrize(
    "path_kind", ["relative", "absolute", "parent", "symlink", "home"]
)
def test_file_paths_resolve_from_selected_configuration(
    tmp_path, monkeypatch, global_config, path_kind
):
    project = tmp_path / "project"
    project.mkdir()
    directory = tmp_path / "xdg/fdsx" if global_config else project / ".fdsx"
    directory.mkdir(parents=True)
    target = directory / "rules.txt"
    target.write_text("selected")
    reference = "rules.txt"
    if path_kind == "absolute":
        reference = str(target)
    elif path_kind == "parent":
        target = directory.parent / "parent.txt"
        target.write_text("selected")
        reference = "../parent.txt"
    elif path_kind == "symlink":
        (directory / "link.txt").symlink_to(target)
        reference = "link.txt"
    elif path_kind == "home":
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        (home / "rules.txt").write_text("selected")
        reference = "~/rules.txt"
    (directory / "config.yaml").write_text(
        yaml.safe_dump({"prompt_prefix_file": reference})
    )
    # Neither CWD nor the workflow folder is the configuration folder.
    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (tmp_path / "rules.txt").write_text("wrong cwd")
    (workflow_dir / "rules.txt").write_text("wrong workflow")
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "ok", ""),
    ) as call:
        run_flow(write_flow(workflow_dir), base_dir=project / ".fdsx")
    assert claude_body(call.call_args) == "selected\n\ntask"


@pytest.mark.parametrize("global_config", [False, True])
@pytest.mark.parametrize(
    "raw,reason",
    [
        ("prompt_prefix_file:\n", "string"),
        ("prompt_prefix_file: null\n", "string"),
        ("prompt_prefix_file: 12\n", "string"),
        ("prompt_prefix_file: true\n", "string"),
        ("prompt_prefix_file: [private-body]\n", "string"),
        ("prompt_prefix_file: {private-body: x}\n", "string"),
        ('prompt_prefix_file: ""\n', "empty path"),
        (
            "prompt_prefix: private-body\nprompt_prefix_file: rules.txt\n",
            "mutually exclusive",
        ),
        ('prompt_prefix: ""\nprompt_prefix_file: rules.txt\n', "mutually exclusive"),
        ('prompt_prefix: private-body\nprompt_prefix_file: ""\n', "mutually exclusive"),
    ],
)
def test_invalid_file_settings_stop_cli_before_provider(
    tmp_path, global_config, raw, reason
):
    from typer.testing import CliRunner

    from fdsx.cli.main import app
    from fdsx.core.config import load_config

    write_config(tmp_path, {}, global_config=global_config)
    directory = tmp_path / ("xdg/fdsx" if global_config else ".fdsx")
    (directory / "config.yaml").write_text(raw)
    if global_config:
        write_config(tmp_path, {"prompt_prefix": "valid"})
    with patch("fdsx.providers.claude._run_subprocess") as call:
        with pytest.raises(ValueError, match=reason) as error:
            load_config()
        assert type(error.value) is ValueError
        for args in [
            ["run", str(write_flow(tmp_path))],
            ["resume", "--thread-id", "unused"],
        ]:
            result = CliRunner().invoke(app, args)
            assert result.exit_code != 0
            assert "prompt_prefix" in result.stderr
            assert reason in result.stderr
            assert "private-body" not in result.stderr
    call.assert_not_called()


def interrupt_prefix_flow(tmp_path, *, remaining_states=None):
    path = save_states(
        tmp_path,
        {
            "before": ai_task(prompt_template="before", next="wait"),
            "wait": {
                "type": "wait",
                "result_path": "$.approval",
                "message": "Continue?",
                "choices": ["yes"],
                "next": "after",
            },
            **(
                remaining_states
                or {"after": ai_task(prompt_template="after", end=True)}
            ),
        },
    )
    with (
        patch(
            "fdsx.providers.claude._run_subprocess",
            return_value=ProviderResult(0, "ok", ""),
        ),
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=RuntimeError("interrupted"),
        ),
        pytest.raises(RuntimeError, match="Flow execution failed"),
    ):
        run_flow(path, thread_id="file-resume", base_dir=tmp_path / ".fdsx")
    return path


@pytest.mark.parametrize("mode", ["run", "resume", "cli", "cli_resume"])
@pytest.mark.parametrize("global_config", [False, True])
def test_loaded_prefix_is_absent_from_model_validation_diagnostics(
    tmp_path, mode, global_config
):
    from structlog.testing import capture_logs
    from typer.testing import CliRunner

    from fdsx.cli.main import app
    from fdsx.core.config import load_config
    from fdsx.core.engine import resume_flow

    path = interrupt_prefix_flow(tmp_path) if mode == "resume" else write_flow(tmp_path)
    # Keep the marker visible even in Pydantic's abbreviated input rendering.
    marker = "ZXQ"
    write_config(tmp_path, {})
    write_config(
        tmp_path,
        {"prompt_prefix_file": "rules.txt", "task_splitter": {}},
        global_config=global_config,
    )
    directory = tmp_path / ("xdg/fdsx" if global_config else ".fdsx")
    (directory / "rules.txt").write_text(marker, encoding="utf-8")
    with capture_logs() as logs, patch("fdsx.providers.claude._run_subprocess") as call:
        with pytest.raises(ValueError, match="task_splitter has been removed") as error:
            load_config()
        diagnostics = str(error.value)
        if mode in ("cli", "cli_resume"):
            args = (
                ["run", str(path)]
                if mode == "cli"
                else ["resume", "--thread-id", "unused"]
            )
            result = CliRunner().invoke(app, args)
            assert result.exit_code != 0
            assert "task_splitter has been removed" in result.stderr
            diagnostics += result.output + str(result.exception)
        else:
            with pytest.raises(
                ValueError if mode == "run" else RuntimeError,
                match="task_splitter has been removed",
            ) as execution_error:
                if mode == "run":
                    run_flow(path, base_dir=tmp_path / ".fdsx")
                else:
                    resume_flow("file-resume", tmp_path / ".fdsx", path)
            diagnostics += str(execution_error.value)
    call.assert_not_called()
    if mode == "resume":
        assert any("task_splitter has been removed" in str(log) for log in logs)
    assert marker not in diagnostics + str(logs)


@pytest.mark.parametrize("mode", ["run", "resume"])
@pytest.mark.parametrize("failure", ["missing", "permission", "decode"])
@pytest.mark.parametrize("global_config", [False, True])
def test_file_read_failures_stop_tasks_with_safe_configuration_error(
    tmp_path, monkeypatch, mode, failure, global_config
):
    from structlog.testing import capture_logs

    from fdsx.core.config import load_config
    from fdsx.core.engine import resume_flow

    path = interrupt_prefix_flow(tmp_path) if mode == "resume" else write_flow(tmp_path)
    write_config(
        tmp_path, {"prompt_prefix_file": "rules.txt"}, global_config=global_config
    )
    directory = tmp_path / ("xdg/fdsx" if global_config else ".fdsx")
    target = directory / "rules.txt"
    if failure == "decode":
        target.write_bytes(b"private-body\xff")
    elif failure == "permission":
        target.write_text("private-body")
        original = Path.open

        def denied(self, *args, **kwargs):
            if self == target:
                raise PermissionError("private-body")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)
    expected = {
        "missing": "FileNotFoundError",
        "permission": "PermissionError",
        "decode": "UnicodeDecodeError",
    }[failure]
    with capture_logs() as logs, patch("fdsx.providers.claude._run_subprocess") as call:
        with pytest.raises(ValueError, match=expected) as error:
            load_config()
        assert type(error.value) is ValueError
        assert error.value.__suppress_context__
        with pytest.raises(
            ValueError if mode == "run" else RuntimeError, match="prompt_prefix_file"
        ):
            if mode == "run":
                run_flow(path, base_dir=tmp_path / ".fdsx")
            else:
                resume_flow("file-resume", tmp_path / ".fdsx", path)
    call.assert_not_called()
    assert any(
        log.get("log_level") in ("error", "warning") and log.get("reason") == expected
        for log in logs
    )
    assert "private-body" not in str(error.value) + str(logs)


@pytest.mark.parametrize(
    "change,expected",
    [
        ("content", "new\n\nafter"),
        ("reference", "new\n\nafter"),
        ("to_inline", "new\n\nafter"),
        ("to_file", "new\n\nafter"),
        ("empty", "after"),
        ("inherit", "global\n\nafter"),
    ],
)
def test_resume_rereads_file_and_cross_form_overrides(tmp_path, change, expected):
    from fdsx.core.engine import resume_flow

    write_config(tmp_path, {"prompt_prefix_file": "global.txt"}, global_config=True)
    (tmp_path / "xdg/fdsx/global.txt").write_text("global")
    write_config(
        tmp_path,
        {"prompt_prefix": "old"}
        if change == "to_file"
        else {"prompt_prefix_file": "rules.txt"},
    )
    target = tmp_path / ".fdsx/rules.txt"
    target.write_text("old")
    path = interrupt_prefix_flow(tmp_path)
    if change == "to_inline":
        write_config(tmp_path, {"prompt_prefix": "new"})
    elif change == "inherit":
        write_config(tmp_path, {})
    elif change == "reference":
        (tmp_path / ".fdsx/new.txt").write_text("new")
        write_config(tmp_path, {"prompt_prefix_file": "new.txt"})
    else:
        target.write_text("" if change == "empty" else "new")
        if change == "to_file":
            write_config(tmp_path, {"prompt_prefix_file": "rules.txt"})
    with (
        patch("builtins.input", return_value="1"),
        patch(
            "fdsx.providers.claude._run_subprocess",
            return_value=ProviderResult(0, "ok", ""),
        ) as call,
    ):
        resume_flow("file-resume", tmp_path / ".fdsx", path)
    assert list(map(claude_body, call.call_args_list)) == [expected]


@pytest.mark.parametrize("failure", ["missing", "permission", "decode"])
def test_cli_file_failure_prevents_automatic_selection(tmp_path, monkeypatch, failure):
    from typer.testing import CliRunner

    from fdsx.cli.main import app

    write_config(tmp_path, {"prompt_prefix_file": "rules.txt"})
    target = tmp_path / ".fdsx/rules.txt"
    if failure == "decode":
        target.write_bytes(b"private-body\xff")
    elif failure == "permission":
        original = Path.open

        def denied(self, *args, **kwargs):
            if self == target:
                raise PermissionError("private-body")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", denied)
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "one.yaml").write_text("description: work\n")
    with (
        patch("fdsx.core.selector.resolve_workflow_for_task") as select,
        patch("fdsx.providers.claude._run_subprocess") as call,
    ):
        result = CliRunner().invoke(
            app, ["run", "--tasks-dir", str(tasks), "--auto-workflow"]
        )
    assert result.exit_code != 0
    assert "prompt_prefix_file" in result.stderr
    assert {
        "missing": "FileNotFoundError",
        "permission": "PermissionError",
        "decode": "UnicodeDecodeError",
    }[failure] in result.stderr
    assert "private-body" not in result.stderr
    select.assert_not_called()
    call.assert_not_called()


def test_overridden_unreadable_global_file_is_not_opened(tmp_path, monkeypatch):
    write_config(tmp_path, {"prompt_prefix_file": "rules.txt"}, global_config=True)
    write_config(tmp_path, {"prompt_prefix_file": "rules.txt"})
    (tmp_path / ".fdsx/rules.txt").write_text("project")
    original = Path.open

    def denied(self, *args, **kwargs):
        if self == tmp_path / "xdg/fdsx/rules.txt":
            pytest.fail("overridden global file was opened")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(0, "ok", ""),
    ) as call:
        run_flow(write_flow(tmp_path), base_dir=tmp_path / ".fdsx")
    assert claude_body(call.call_args) == "project\n\ntask"


def test_cli_consecutive_task_files_reread_prefix_after_preflight(tmp_path):
    from typer.testing import CliRunner

    from fdsx.cli.main import app

    write_config(tmp_path, {"prompt_prefix_file": "rules.txt"})
    target = tmp_path / ".fdsx/rules.txt"
    target.write_text("old")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    for name in ["one", "two"]:
        (tasks / f"{name}.yaml").write_text(f"description: {name}\n")
    path = save_states(
        tmp_path, {"first": ai_task(next="second"), "second": ai_task(end=True)}
    )

    def respond(**kwargs):
        target.write_text("new")
        return ProviderResult(0, "ok", "")

    with patch("fdsx.providers.claude._run_subprocess", side_effect=respond) as call:
        result = CliRunner().invoke(
            app, ["run", str(path), "--tasks-dir", str(tasks), "--auto-workflow"]
        )
    assert result.exit_code == 0, result.stderr
    assert list(map(claude_body, call.call_args_list)) == [
        "old\n\ntask",
        "old\n\ntask",
        "new\n\ntask",
        "new\n\ntask",
    ]


def test_resume_keeps_file_contents_fixed_until_next_invocation(tmp_path):
    from fdsx.core.engine import resume_flow

    write_config(tmp_path, {"prompt_prefix_file": "rules.txt"})
    target = tmp_path / ".fdsx/rules.txt"
    target.write_text("old")
    path = interrupt_prefix_flow(
        tmp_path,
        remaining_states={"after": ai_task(next="last"), "last": ai_task(end=True)},
    )
    target.write_text("current")

    def respond(**kwargs):
        target.write_text("next")
        return ProviderResult(0, "ok", "")

    with (
        patch("builtins.input", return_value="1"),
        patch("fdsx.providers.claude._run_subprocess", side_effect=respond) as call,
    ):
        resume_flow("file-resume", tmp_path / ".fdsx", path)
        run_flow(write_flow(tmp_path), base_dir=tmp_path / ".fdsx")
    assert list(map(claude_body, call.call_args_list)) == [
        "current\n\ntask",
        "current\n\ntask",
        "next\n\ntask",
    ]

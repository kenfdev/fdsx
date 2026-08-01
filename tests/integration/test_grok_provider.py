import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.core.engine import run_flow
from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD, ProviderResult


def _write_grok_flow(
    tmp_path: Path, *, provider_options: str = "", retry: int = 0
) -> Path:
    flow_path = tmp_path / "workflow.yaml"
    options_block = (
        f"    provider_options:\n{provider_options}" if provider_options else ""
    )
    flow_path.write_text(
        f"""
name: grok-provider
description: Exercise the Grok provider
start_at: generate
states:
  generate:
    type: task
    provider: grok
    model: grok-4.5
    prompt_template: Return a greeting
    result_path: $.answer
    retry: {retry}
{options_block}
    end: true
""".lstrip()
    )
    return flow_path


def test_grok_workflow_streams_final_answer_with_safe_headless_defaults(
    tmp_path: Path,
) -> None:
    flow_path = _write_grok_flow(tmp_path)
    captured_args: list[str] = []

    def fake_run_subprocess(*, args: list[str], **kwargs: object) -> ProviderResult:
        captured_args.extend(args)
        output_callback = kwargs["output_callback"]
        output_callback(  # type: ignore[operator]
            json.dumps(
                {
                    "method": "session/update",
                    "params": {
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "hello from grok"},
                        }
                    },
                }
            )
        )
        output_callback(  # type: ignore[operator]
            json.dumps(
                {
                    "type": "end",
                    "stopReason": "end_turn",
                    "structuredOutput": None,
                }
            )
        )
        return ProviderResult(exit_code=0, stdout="", stderr="")

    with patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"answer": "hello from grok"}
    assert captured_args[0] == "grok"
    assert "--no-auto-update" in captured_args
    assert "--no-ask-user" in captured_args
    assert "--no-memory" in captured_args
    assert "--no-plan" in captured_args
    assert "--no-subagents" in captured_args
    assert "--verbatim" in captured_args
    assert captured_args[captured_args.index("--permission-mode") + 1] == "dontAsk"
    assert captured_args[captured_args.index("--model") + 1] == "grok-4.5"
    assert captured_args[captured_args.index("--output-format") + 1] == (
        "streaming-json"
    )


def test_grok_workflow_rejects_empty_permission_rule(tmp_path: Path) -> None:
    flow_path = _write_grok_flow(tmp_path, provider_options="      allow: ['']\n")

    with (
        patch(
            "fdsx.providers.grok._run_subprocess",
            return_value=ProviderResult(exit_code=1, stdout="", stderr="mocked"),
        ),
        pytest.raises(RuntimeError, match="at least 1 character"),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)


@pytest.mark.parametrize(
    ("provider_options", "error"),
    [
        ("      permission_mode: unrestricted\n", "permission_mode"),
        ("      max_turns: 0\n", "greater than 0"),
        ("      on_max_turns: ignore\n", "on_max_turns"),
        ("      cross_session_memory: sometimes\n", "cross_session_memory"),
        ("      reasoning_effort: ''\n", "at least 1 character"),
        (
            "      rules: extra rules\n      system_prompt_override: replacement\n",
            "mutually exclusive",
        ),
        (
            "      agents:\n        researcher:\n          prompt: Research this\n",
            "requires no_subagents: false",
        ),
    ],
)
def test_grok_workflow_rejects_invalid_provider_options(
    tmp_path: Path, provider_options: str, error: str
) -> None:
    flow_path = _write_grok_flow(tmp_path, provider_options=provider_options)

    with (
        patch("fdsx.providers.grok._run_subprocess") as provider,
        pytest.raises(RuntimeError, match=error),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    provider.assert_not_called()


def test_grok_workflow_translates_headless_run_options(tmp_path: Path) -> None:
    flow_path = _write_grok_flow(
        tmp_path,
        provider_options="""      permission_mode: bypassPermissions
      sandbox: workspace
      allow: [Read, 'Bash(git status:*)']
      deny: [Write]
      tools: [Read, Bash]
      disallowed_tools: [Write]
      reasoning_effort: ultra
      max_turns: 7
      on_max_turns: return_partial
      no_subagents: false
      no_plan: false
      cross_session_memory: on
      disable_web_search: true
      verbatim: false
      cwd: project
      agent: reviewer
      agents:
        researcher:
          description: Research specialist
          prompt: Gather evidence
      rules: Review carefully
""",
    )
    captured_args: list[str] = []

    def fake_run_subprocess(*, args: list[str], **kwargs: object) -> ProviderResult:
        captured_args.extend(args)
        output_callback = kwargs["output_callback"]
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "text", "data": "done"})
        )
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "end", "stopReason": "EndTurn"})
        )
        return ProviderResult(exit_code=0, stdout="", stderr="")

    with patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"answer": "done"}
    assert captured_args[captured_args.index("--permission-mode") + 1] == (
        "bypassPermissions"
    )
    assert captured_args[captured_args.index("--sandbox") + 1] == "workspace"
    assert [
        captured_args[index + 1]
        for index, value in enumerate(captured_args)
        if value == "--allow"
    ] == ["Read", "Bash(git status:*)"]
    assert captured_args[captured_args.index("--deny") + 1] == "Write"
    assert captured_args[captured_args.index("--tools") + 1] == "Read,Bash"
    assert captured_args[captured_args.index("--disallowed-tools") + 1] == "Write"
    assert captured_args[captured_args.index("--reasoning-effort") + 1] == "ultra"
    assert captured_args[captured_args.index("--max-turns") + 1] == "7"
    assert "--no-subagents" not in captured_args
    assert "--no-plan" not in captured_args
    assert "--experimental-memory" in captured_args
    assert "--no-memory" not in captured_args
    assert "--disable-web-search" in captured_args
    assert "--verbatim" not in captured_args
    assert captured_args[captured_args.index("--cwd") + 1] == "project"
    assert captured_args[captured_args.index("--agent") + 1] == "reviewer"
    assert json.loads(captured_args[captured_args.index("--agents") + 1]) == {
        "researcher": {
            "description": "Research specialist",
            "prompt": "Gather evidence",
        }
    }
    assert captured_args[captured_args.index("--rules") + 1] == "Review carefully"


def test_grok_workflow_uses_restrictive_temporary_file_for_large_prompt(
    tmp_path: Path,
) -> None:
    prompt = "x" * ARG_MAX_STDIN_THRESHOLD
    flow_path = _write_grok_flow(tmp_path)
    flow_path.write_text(flow_path.read_text().replace("Return a greeting", prompt))
    prompt_path: list[Path] = []

    def fake_run_subprocess(*, args: list[str], **kwargs: object) -> ProviderResult:
        path = Path(args[args.index("--prompt-file") + 1])
        prompt_path.append(path)
        assert path.read_text() == prompt
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        output_callback = kwargs["output_callback"]
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "text", "data": "done"})
        )
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "end", "stopReason": "EndTurn"})
        )
        return ProviderResult(exit_code=0, stdout="", stderr="")

    with patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"answer": "done"}
    assert prompt_path and not prompt_path[0].exists()


def test_grok_workflow_cleans_up_large_prompt_after_provider_failure(
    tmp_path: Path,
) -> None:
    prompt = "x" * ARG_MAX_STDIN_THRESHOLD
    flow_path = _write_grok_flow(tmp_path)
    flow_path.write_text(flow_path.read_text().replace("Return a greeting", prompt))
    prompt_path: list[Path] = []

    def fake_run_subprocess(*, args: list[str], **_: object) -> ProviderResult:
        path = Path(args[args.index("--prompt-file") + 1])
        prompt_path.append(path)
        assert path.exists()
        return ProviderResult(exit_code=1, stdout="", stderr="provider failed")

    with (
        patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess),
        pytest.raises(RuntimeError, match="provider failed"),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert prompt_path and not prompt_path[0].exists()


def test_grok_workflow_fails_when_maximum_turns_are_reached(tmp_path: Path) -> None:
    flow_path = _write_grok_flow(tmp_path)

    def fake_run_subprocess(**kwargs: object) -> ProviderResult:
        output_callback = kwargs["output_callback"]
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "text", "data": "partial"})
        )
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "end", "stopReason": "MaxTurnsReached"})
        )
        return ProviderResult(exit_code=0, stdout="", stderr="")

    with (
        patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess),
        pytest.raises(RuntimeError, match="maximum number of turns"),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)


def test_grok_workflow_can_return_partial_result_at_maximum_turns(
    tmp_path: Path,
) -> None:
    flow_path = _write_grok_flow(
        tmp_path,
        provider_options="      on_max_turns: return_partial\n",
    )

    def fake_run_subprocess(**kwargs: object) -> ProviderResult:
        output_callback = kwargs["output_callback"]
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "text", "data": "partial"})
        )
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "end", "stopReason": "MaxTurnsReached"})
        )
        return ProviderResult(exit_code=0, stdout="", stderr="")

    with patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"answer": "partial"}


def test_grok_workflow_does_not_accept_cancelled_stream_as_success(
    tmp_path: Path,
) -> None:
    flow_path = _write_grok_flow(tmp_path)

    def fake_run_subprocess(**kwargs: object) -> ProviderResult:
        output_callback = kwargs["output_callback"]
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "text", "data": "unfinished"})
        )
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "end", "stopReason": "Cancelled"})
        )
        return ProviderResult(exit_code=0, stdout="", stderr="")

    with (
        patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess),
        pytest.raises(RuntimeError, match="cancelled before producing a final result"),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)


def test_grok_workflow_reports_explicit_stream_error(tmp_path: Path) -> None:
    flow_path = _write_grok_flow(tmp_path)

    def fake_run_subprocess(**kwargs: object) -> ProviderResult:
        output_callback = kwargs["output_callback"]
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "error", "message": "quota exceeded"})
        )
        return ProviderResult(exit_code=0, stdout="", stderr="")

    with (
        patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess),
        pytest.raises(RuntimeError, match="quota exceeded"),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)


def test_grok_workflow_retries_after_provider_failure(tmp_path: Path) -> None:
    flow_path = _write_grok_flow(tmp_path, retry=1)
    attempts = 0

    def fake_run_subprocess(**kwargs: object) -> ProviderResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return ProviderResult(exit_code=1, stdout="", stderr="transient failure")
        output_callback = kwargs["output_callback"]
        output_callback(json.dumps({"type": "text", "data": "recovered"}))  # type: ignore[operator]
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "end", "stopReason": "EndTurn"})
        )
        return ProviderResult(exit_code=0, stdout="", stderr="")

    with (
        patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess),
        patch("fdsx.core.compiler.execution.time.sleep"),
    ):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert attempts == 2
    assert result.results == {"answer": "recovered"}


def test_grok_profile_options_merge_with_state_overrides(tmp_path: Path) -> None:
    flow_path = _write_grok_flow(tmp_path)
    flow_path.write_text(
        flow_path.read_text()
        .replace(
            "start_at: generate",
            """profiles:
  grok-default:
    provider: grok
    model: grok-4.5
    reasoning_effort: high
    cross_session_memory: inherit
start_at: generate""",
        )
        .replace(
            "    type: task\n    provider: grok\n    model: grok-4.5",
            "    type: task\n"
            "    profile: grok-default\n"
            "    provider_options:\n"
            "      reasoning_effort: ultra",
        )
    )
    captured_args: list[str] = []

    def fake_run_subprocess(*, args: list[str], **kwargs: object) -> ProviderResult:
        captured_args.extend(args)
        output_callback = kwargs["output_callback"]
        output_callback(json.dumps({"type": "text", "data": "done"}))  # type: ignore[operator]
        output_callback(  # type: ignore[operator]
            json.dumps({"type": "end", "stopReason": "EndTurn"})
        )
        return ProviderResult(exit_code=0, stdout="", stderr="")

    with patch("fdsx.providers.grok._run_subprocess", side_effect=fake_run_subprocess):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"answer": "done"}
    assert captured_args[captured_args.index("--reasoning-effort") + 1] == "ultra"
    assert "--no-memory" not in captured_args
    assert "--experimental-memory" not in captured_args


def test_grok_workflow_reports_missing_cli_with_actionable_error(
    tmp_path: Path,
) -> None:
    flow_path = _write_grok_flow(tmp_path)
    missing = ProviderResult(
        exit_code=1,
        stdout="",
        stderr="[Errno 2] No such file or directory: 'grok'",
    )

    with (
        patch("fdsx.providers.grok._run_subprocess", return_value=missing),
        pytest.raises(RuntimeError, match="Grok CLI not found on PATH"),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

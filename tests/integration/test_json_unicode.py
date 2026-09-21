"""Japanese stays readable in saved JSON and provider boundaries."""

import json
from unittest.mock import patch

import pytest

from fdsx.checkpoint.map_progress import MapProgress
from fdsx.core.hooks import write_hook_data
from fdsx.providers.base import ProviderResult
from fdsx.providers.claude import ClaudeProvider
from fdsx.providers.grok import GrokOptions, GrokProvider
from fdsx.providers.opencode import OpenCodeOptions


def test_hook_data_preserves_japanese(tmp_path):
    data = {"結果": "日本語の出力"}
    path = write_hook_data(
        data,
        state_name="task",
        filename="output.json",
        thread_id="unicode-test",
        base_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")
    assert "日本語の出力" in text
    assert "\\u" not in text
    assert json.loads(text) == data


def test_map_progress_preserves_japanese_and_can_be_resumed(tmp_path):
    results = [{"結果": "日本語の出力"}]
    MapProgress(str(tmp_path), "map", 1, 1).collect(0, results[0], "success")
    files = list((tmp_path / "map").glob("*.json"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "日本語の出力" in text
    assert "\\u" not in text
    assert MapProgress(str(tmp_path), "map", 1, 1, resume=True).snapshot() == {
        0: {"result": results[0], "status": "success"}
    }


@pytest.mark.parametrize("provider_name", ["claude", "grok"])
def test_provider_structured_result_preserves_japanese(
    provider_name, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    data = {"結果": "日本語の出力"}
    event = (
        {"type": "result", "structured_output": data}
        if provider_name == "claude"
        else {"type": "end", "stopReason": "end_turn", "structuredOutput": data}
    )

    def fake_run(**kwargs):
        kwargs["output_callback"](json.dumps(event))
        return ProviderResult(exit_code=0, stdout="", stderr="")

    provider = ClaudeProvider() if provider_name == "claude" else GrokProvider()
    with patch(f"fdsx.providers.{provider_name}._run_subprocess", side_effect=fake_run):
        result = provider.execute(
            "日本語で回答", model="test-model", output_callback=lambda _: None
        )

    assert "日本語の出力" in result.stdout
    assert "\\u" not in result.stdout
    assert json.loads(result.stdout) == data
    assert result.final_message == result.stdout


def test_provider_options_preserve_japanese_in_env_and_args():
    permission = {"read": {"日本語/*": "allow"}}
    config = OpenCodeOptions(permission=permission).to_env()["OPENCODE_CONFIG_CONTENT"]
    assert "日本語/*" in config
    assert json.loads(config) == {"permission": permission}

    agents = {"reviewer": {"description": "日本語でレビュー"}}
    flags = GrokOptions(agents=agents, no_subagents=False).to_cli_flags()
    encoded = flags[flags.index("--agents") + 1]
    assert "日本語でレビュー" in encoded
    assert json.loads(encoded) == agents

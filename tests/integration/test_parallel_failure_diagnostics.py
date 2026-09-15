"""Provider failures must remain actionable in persisted parallel run records."""

import json
from unittest.mock import patch

import pytest
import yaml

from fdsx.core.engine import run_flow
from fdsx.providers.base import ProviderResult


@pytest.mark.parametrize("branch_count", [1, 4])
@pytest.mark.parametrize("event_type", ["error", "turn.failed", "silent", "timeout"])
def test_codex_parallel_failure_preserves_reason_and_exit_code(
    tmp_path, event_type, branch_count
):
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    (tmp_path / "schema.json").write_text(json.dumps(schema))
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text("""
name: review diagnostics
description: Preserve provider failure diagnostics
start_at: review
retry_escalation: false
states:
  review:
    type: parallel
    branches:
      - name: acceptance
        provider: codex
        model: test-model
        prompt_template: Review locally
        retry: 0
        structured_output:
          schema: schema.json
          result_path: $.report
    result_path: $.reviews
    end: true
""")
    definition = yaml.safe_load(workflow.read_text())
    names = ["acceptance", "architecture", "cleanliness", "hardening"][:branch_count]
    branch = definition["states"]["review"]["branches"][0]
    definition["states"]["review"]["branches"] = [
        {**branch, "name": name} for name in names
    ]
    workflow.write_text(yaml.safe_dump(definition))
    reason = "invalid_json_schema: reference can only point to definitions defined at the top level"

    def fail(**kwargs):
        if event_type == "timeout":
            raise TimeoutError()
        if event_type == "silent":
            return ProviderResult(exit_code=1, stdout="", stderr="")
        event = {"type": event_type}
        if event_type == "error":
            event["message"] = reason
        else:
            event["error"] = {"message": reason}
        line = json.dumps(event)
        kwargs["output_callback"](line)
        return ProviderResult(exit_code=1, stdout=line, stderr="")

    expected = {
        "silent": "exit code 1",
        "timeout": "TimeoutError",
    }.get(event_type, "invalid_json_schema")
    with (
        patch("fdsx.providers.codex._run_subprocess", side_effect=fail),
        pytest.raises(RuntimeError, match=expected),
    ):
        run_flow(workflow, base_dir=tmp_path, quiet=True)

    record_path = next(tmp_path.rglob("run.json"))
    record = json.loads(record_path.read_text())
    branches = record["states"][-1]["branches"]
    assert [branch["name"] for branch in branches] == names
    for index, branch in enumerate(branches, 1):
        assert branch["exit_code"] == 1
        assert expected in branch["error"]
        logs = list((record_path.parent / "logs").glob(f"review_branch{index}_*.log"))
        assert logs
        assert expected in logs[0].read_text()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_codex_stream_errors_do_not_replace_agent_output_or_stderr(exit_code):
    from fdsx.providers.codex import CodexProvider

    stdout_lines = []
    stderr_lines = []

    def respond(**kwargs):
        for event in [
            {"type": "error", "message": "Connection interrupted"},
            {"type": "turn.failed", "error": {"message": "Connection interrupted"}},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "partial result"},
            },
        ]:
            kwargs["output_callback"](json.dumps(event))
        return ProviderResult(
            exit_code=exit_code, stdout="raw events", stderr="CLI diagnostic"
        )

    with patch("fdsx.providers.codex._run_subprocess", side_effect=respond):
        result = CodexProvider().execute(
            "Review",
            output_callback=stdout_lines.append,
            stderr_callback=stderr_lines.append,
        )
    assert result.exit_code == exit_code
    assert result.stdout == result.final_message == "partial result"
    assert stdout_lines == ["partial result"]
    assert stderr_lines == ["Connection interrupted"]
    assert "CLI diagnostic" in result.stderr
    if exit_code:
        assert result.stderr.count("Connection interrupted") == 1
    else:
        assert result.stderr == "CLI diagnostic"

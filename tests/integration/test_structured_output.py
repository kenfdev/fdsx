import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.core.engine import run_flow
from fdsx.core.engine.validate import FlowValidationError
from fdsx.providers.base import ProviderResult


def _write_flow(
    tmp_path: Path,
    schema_name: str = "output.schema.json",
    *,
    provider: str = "claude",
    retry: int = 3,
) -> Path:
    flow_path = tmp_path / "workflow.yaml"
    provider_fields = (
        '    command: "printf \'{\\"approved\\": \\"yes\\"}\'"\n'
        if provider == "system"
        else "    model: test-model\n    prompt_template: Return JSON\n"
    )
    flow_path.write_text(
        f"""
name: structured-output
description: Validate provider output before storing it
start_at: generate
states:
  generate:
    type: task
    provider: {provider}
{provider_fields}    retry: {retry}
    structured_output:
      schema: {schema_name}
      result_path: $.payload
    end: true
""".lstrip()
    )
    return flow_path


def test_valid_structured_object_is_stored_as_workflow_state(tmp_path: Path) -> None:
    flow_path = _write_flow(tmp_path)
    (tmp_path / "output.schema.json").write_text(
        """
{
  "type": "object",
  "required": ["approved"],
  "properties": {"approved": {"type": "boolean"}}
}
""".strip()
    )

    fake = ProviderResult(exit_code=0, stdout='{"approved": true}', stderr="")
    with patch("fdsx.providers.claude._run_subprocess", return_value=fake):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"payload": {"approved": True}}


def test_codex_structured_output_uses_final_agent_message(tmp_path: Path) -> None:
    flow_path = _write_flow(tmp_path, provider="codex", retry=0)
    (tmp_path / "output.schema.json").write_text(
        """
{
  "type": "object",
  "required": ["approved"],
  "properties": {"approved": {"type": "boolean"}}
}
""".strip()
    )
    messages = [
        "I'll inspect the repository before producing the result.",
        '{"approved": true}',
    ]

    def fake_run(**kwargs: object) -> ProviderResult:
        output_callback = kwargs["output_callback"]
        for index, message in enumerate(messages):
            output_callback(  # type: ignore[operator]
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": f"item_{index}",
                            "type": "agent_message",
                            "text": message,
                        },
                    }
                )
            )
        return ProviderResult(exit_code=0, stdout="<raw jsonl>", stderr="")

    with patch("fdsx.providers.codex._run_subprocess", side_effect=fake_run):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"payload": {"approved": True}}


def test_claude_structured_output_uses_final_result_message(tmp_path: Path) -> None:
    flow_path = _write_flow(tmp_path, retry=0)
    (tmp_path / "output.schema.json").write_text(
        """
{
  "type": "object",
  "required": ["approved"],
  "properties": {"approved": {"type": "boolean"}}
}
""".strip()
    )
    events = [
        {
            "type": "content_block_delta",
            "delta": {
                "type": "text_delta",
                "text": "I'll inspect the repository first.\n",
            },
        },
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": '{"approved": true}'},
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '{"approved": true}',
        },
    ]

    def fake_run(**kwargs: object) -> ProviderResult:
        output_callback = kwargs["output_callback"]
        for event in events:
            output_callback(json.dumps(event))  # type: ignore[operator]
        return ProviderResult(exit_code=0, stdout="<raw ndjson>", stderr="")

    with patch("fdsx.providers.claude._run_subprocess", side_effect=fake_run):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"payload": {"approved": True}}


def test_missing_structured_output_schema_is_rejected_before_provider_execution(
    tmp_path: Path,
) -> None:
    flow_path = _write_flow(tmp_path, "missing.schema.json")

    with (
        patch("fdsx.providers.claude._run_subprocess") as provider,
        pytest.raises(FlowValidationError, match=r"schema.*not found"),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    provider.assert_not_called()


def test_complete_markdown_fence_around_json_is_accepted(tmp_path: Path) -> None:
    flow_path = _write_flow(tmp_path)
    (tmp_path / "output.schema.json").write_text(
        '{"type": "array", "items": {"type": "integer"}}'
    )
    fake = ProviderResult(exit_code=0, stdout="```json\n[1, 2]\n```", stderr="")

    with patch("fdsx.providers.claude._run_subprocess", return_value=fake):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"payload": [1, 2]}


def test_schema_invalid_output_retries_with_validation_feedback(
    tmp_path: Path,
) -> None:
    flow_path = _write_flow(tmp_path, retry=1)
    (tmp_path / "output.schema.json").write_text(
        """
{
  "type": "object",
  "required": ["approved"],
  "properties": {"approved": {"type": "boolean"}}
}
""".strip()
    )
    attempts = [
        ProviderResult(exit_code=0, stdout='{"approved": "yes"}', stderr=""),
        ProviderResult(exit_code=0, stdout='{"approved": true}', stderr=""),
    ]
    captured_args: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> ProviderResult:
        captured_args.append(args)
        return attempts[len(captured_args) - 1]

    with (
        patch("fdsx.providers.claude._run_subprocess", side_effect=fake_run),
        patch("fdsx.core.compiler.execution.time.sleep"),
    ):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results == {"payload": {"approved": True}}
    assert "JSON Schema validation failed" in " ".join(captured_args[1])
    assert '{"approved": "yes"}' not in " ".join(captured_args[1])


def test_system_provider_is_not_retried_after_schema_validation_failure(
    tmp_path: Path,
) -> None:
    flow_path = _write_flow(tmp_path, provider="system", retry=2)
    (tmp_path / "output.schema.json").write_text(
        """
{
  "type": "object",
  "properties": {"approved": {"type": "boolean"}},
  "required": ["approved"]
}
""".strip()
    )

    with pytest.raises(RuntimeError, match="JSON Schema validation failed"):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)


def test_malformed_json_exhausts_retries_with_domain_error(tmp_path: Path) -> None:
    flow_path = _write_flow(tmp_path, retry=1)
    (tmp_path / "output.schema.json").write_text('{"type": "object"}')
    fake = ProviderResult(exit_code=0, stdout="not json", stderr="")

    with (
        patch("fdsx.providers.claude._run_subprocess", return_value=fake) as provider,
        patch("fdsx.core.compiler.execution.time.sleep"),
        pytest.raises(RuntimeError, match="Invalid JSON"),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert provider.call_count == 2


def test_invalid_schema_document_is_rejected_at_load_time(tmp_path: Path) -> None:
    flow_path = _write_flow(tmp_path)
    (tmp_path / "output.schema.json").write_text("{invalid")

    with pytest.raises(FlowValidationError, match="invalid schema"):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)


def test_structured_output_cannot_also_use_legacy_result_path(
    tmp_path: Path,
) -> None:
    flow_path = _write_flow(tmp_path)
    text = flow_path.read_text().replace(
        "    structured_output:", "    result_path: $.raw\n    structured_output:"
    )
    flow_path.write_text(text)
    (tmp_path / "output.schema.json").write_text('{"type": "object"}')

    with pytest.raises(
        FlowValidationError, match="mutually exclusive with result_path"
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

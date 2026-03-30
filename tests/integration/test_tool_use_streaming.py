"""Integration test for tool_use streaming in ClaudeProvider.

Verifies:
1. Formatted tool lines (e.g., [Bash] ls /workspace/) appear in summary_callback
2. Old [tool: ToolName] format does NOT appear when JSON parsing succeeds
3. Fallback [tool: ToolName] format IS emitted when input JSON is malformed
"""

import json
from unittest.mock import MagicMock, patch

from fdsx.providers.base import ProviderResult
from fdsx.providers.claude import ClaudeProvider


def _make_ndjson_line(event: dict[str, object]) -> str:
    return json.dumps(event)


def _fake_run_subprocess_factory(ndjson_lines: list[str]) -> MagicMock:
    def fake_run_subprocess(**kwargs: object) -> ProviderResult:
        output_callback = kwargs.get("output_callback")
        if output_callback is not None:
            for line in ndjson_lines:
                output_callback(line)  # type: ignore[operator]
        return ProviderResult(exit_code=0, stdout="<raw ndjson>", stderr="")

    return MagicMock(side_effect=fake_run_subprocess)


def _build_tool_use_stream(
    tool_name: str,
    input_json_fragments: list[str],
    include_result: bool = True,
) -> list[str]:
    session_id = "test-session-123"
    lines: list[str] = []

    lines.append(
        _make_ndjson_line(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "name": tool_name,
                    "id": "tool_01",
                },
                "session_id": session_id,
            }
        )
    )

    for fragment in input_json_fragments:
        lines.append(
            _make_ndjson_line(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": fragment},
                    "session_id": session_id,
                }
            )
        )

    lines.append(
        _make_ndjson_line(
            {"type": "content_block_stop", "index": 0, "session_id": session_id}
        )
    )

    if include_result:
        lines.append(
            _make_ndjson_line(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "Tool executed successfully",
                    "session_id": session_id,
                }
            )
        )

    return lines


class TestToolUseStreaming:
    def setup_method(self) -> None:
        self.provider = ClaudeProvider()

    def test_formatted_tool_line_appears_in_summary_callback(self) -> None:
        """When JSON input parses successfully, summary_callback receives [ToolName] summary."""
        lines = _build_tool_use_stream(
            tool_name="Bash",
            input_json_fragments=['{"command": "ls /workspace/"}'],
        )
        mock_run = _fake_run_subprocess_factory(lines)

        summary_received: list[str] = []
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            self.provider.execute(
                "Run ls",
                output_callback=lambda _: None,
                summary_callback=summary_received.append,
            )

        assert any("[Bash] ls /workspace/" in line for line in summary_received), (
            f"Expected [Bash] ls /workspace/ in summary_callback, got: {summary_received!r}"
        )

    def test_old_tool_format_not_present_on_success(self) -> None:
        """When JSON parses successfully, [tool: ToolName] must NOT appear."""
        lines = _build_tool_use_stream(
            tool_name="Bash",
            input_json_fragments=['{"command": "ls /workspace/"}'],
        )
        mock_run = _fake_run_subprocess_factory(lines)

        summary_received: list[str] = []
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            self.provider.execute(
                "Run ls",
                output_callback=lambda _: None,
                summary_callback=summary_received.append,
            )

        old_format_present = any("[tool: Bash]" in line for line in summary_received)
        assert not old_format_present, (
            f"[tool: Bash] should not appear on success, got: {summary_received!r}"
        )

    def test_old_tool_format_appears_on_malformed_json(self) -> None:
        """When JSON is malformed, summary_callback falls back to [tool: ToolName]."""
        lines = _build_tool_use_stream(
            tool_name="Bash",
            input_json_fragments=['{"command": "ls /worksp', "bad json}"],
        )
        mock_run = _fake_run_subprocess_factory(lines)

        summary_received: list[str] = []
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            self.provider.execute(
                "Run ls",
                output_callback=lambda _: None,
                summary_callback=summary_received.append,
            )

        assert any("[tool: Bash]" in line for line in summary_received), (
            f"Expected [tool: Bash] fallback on malformed JSON, got: {summary_received!r}"
        )

    def test_fragmented_json_accumulates_across_deltas(self) -> None:
        """JSON input split across multiple input_json_delta events is accumulated correctly."""
        lines = _build_tool_use_stream(
            tool_name="Bash",
            input_json_fragments=[
                '{"command": "ls ',
                "/workspace/",
                '"}',
            ],
        )
        mock_run = _fake_run_subprocess_factory(lines)

        summary_received: list[str] = []
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            self.provider.execute(
                "Run ls",
                output_callback=lambda _: None,
                summary_callback=summary_received.append,
            )

        assert any("[Bash] ls /workspace/" in line for line in summary_received), (
            f"Expected accumulated JSON to produce [Bash] ls /workspace/, got: {summary_received!r}"
        )

    def test_result_event_present_stdout_from_result(self) -> None:
        """When result event is present, provider.stdout comes from the result field."""
        lines = _build_tool_use_stream(
            tool_name="Bash",
            input_json_fragments=['{"command": "ls /workspace/"}'],
            include_result=True,
        )
        mock_run = _fake_run_subprocess_factory(lines)

        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            result = self.provider.execute(
                "Run ls",
                output_callback=lambda _: None,
                summary_callback=lambda _: None,
            )

        assert result.stdout == "Tool executed successfully"

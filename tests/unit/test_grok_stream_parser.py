import json

import pytest

from fdsx.providers.grok import GrokProviderError, GrokStreamParser


def test_grok_stream_parser_separates_progress_tools_and_final_text() -> None:
    output: list[str] = []
    summaries: list[str] = []
    tool_lifecycle: list[str] = []
    parser = GrokStreamParser(
        output_callback=output.append,
        summary_callback=summaries.append,
        on_tool_start=lambda: tool_lifecycle.append("start"),
        on_tool_end=lambda: tool_lifecycle.append("end"),
    )

    events = [
        {"type": "thought", "data": "Inspecting the project"},
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-1",
                    "title": "read_file",
                }
            },
        },
        {
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-1",
                    "status": "completed",
                }
            },
        },
        {"type": "text", "data": "final "},
        {"type": "text", "data": "answer"},
        {"type": "end", "stopReason": "EndTurn"},
    ]
    for event in events:
        parser.feed(json.dumps(event))

    result = parser.finish()

    assert output == ["final answer"]
    assert summaries == ["[thinking] Inspecting the project", "[tool: read_file]"]
    assert tool_lifecycle == ["start", "end"]
    assert result.final_text == "final answer"
    assert result.stop_reason == "EndTurn"
    assert result.ended is True


def test_grok_stream_parser_returns_only_message_after_last_tool() -> None:
    parser = GrokStreamParser()
    events = [
        {"type": "text", "data": "I'll inspect first."},
        {"type": "tool_call", "toolCallId": "call-1", "title": "read_file"},
        {
            "type": "tool_call_update",
            "toolCallId": "call-1",
            "status": "completed",
        },
        {"type": "text", "data": "Final answer"},
        {"type": "end", "stopReason": "EndTurn"},
    ]

    for event in events:
        parser.feed(json.dumps(event))

    assert parser.finish().final_text == "Final answer"


def test_grok_stream_parser_rejects_malformed_json() -> None:
    parser = GrokStreamParser()

    with pytest.raises(GrokProviderError, match="malformed streaming JSON"):
        parser.feed("not json")

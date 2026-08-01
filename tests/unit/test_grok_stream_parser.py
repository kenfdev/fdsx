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
    assert summaries == [
        "[thinking] Inspecting the project",
        "[tool: read_file]",
        "[tool update: call-1 completed]",
    ]
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


def test_grok_stream_parser_uses_terminal_structured_output() -> None:
    parser = GrokStreamParser()

    parser.feed(
        json.dumps(
            {
                "type": "end",
                "stopReason": "EndTurn",
                "structuredOutput": {"approved": True},
            }
        )
    )

    result = parser.finish()
    assert result.structured_output == {"approved": True}
    assert result.stop_reason == "EndTurn"
    assert result.ended is True


@pytest.mark.parametrize(
    ("event", "error"),
    [
        ({"type": "text"}, "text event requires string data"),
        ({"type": "thought", "data": 42}, "thought event requires string data"),
        (
            {"type": "tool_call", "title": "read_file"},
            "tool-call event requires an id",
        ),
        (
            {"type": "tool_call_update", "toolCallId": "call-1"},
            "tool-update event requires a status",
        ),
        ({"type": "end"}, "end event requires a stopReason"),
        (
            {"type": "end", "stopReason": ["EndTurn"]},
            "end event requires a stopReason",
        ),
        ({"type": "error"}, "error event requires a message"),
        (
            {"method": "session/update", "params": {}},
            "session update requires an update object",
        ),
        (
            {
                "params": {
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {},
                    }
                }
            },
            "message chunk requires string content",
        ),
    ],
)
def test_grok_stream_parser_rejects_missing_or_invalid_required_fields(
    event: dict[str, object], error: str
) -> None:
    parser = GrokStreamParser()

    with pytest.raises(GrokProviderError, match=error):
        parser.feed(json.dumps(event))


def test_grok_stream_parser_reports_explicit_error_event() -> None:
    parser = GrokStreamParser()

    with pytest.raises(GrokProviderError, match="rate limit reached"):
        parser.feed(json.dumps({"type": "error", "message": "rate limit reached"}))


def test_grok_stream_parser_preserves_maximum_turn_stop_reason() -> None:
    parser = GrokStreamParser()
    parser.feed(json.dumps({"type": "text", "data": "partial"}))
    parser.feed(json.dumps({"type": "end", "stopReason": "MaxTurnsReached"}))

    result = parser.finish()
    assert result.final_text == "partial"
    assert result.stop_reason == "MaxTurnsReached"


def test_grok_stream_parser_ignores_unknown_future_event() -> None:
    parser = GrokStreamParser()
    parser.feed(json.dumps({"type": "future_protocol_event", "value": 7}))
    parser.feed(json.dumps({"type": "text", "data": "answer"}))
    parser.feed(json.dumps({"type": "end", "stopReason": "EndTurn"}))

    assert parser.finish().final_text == "answer"


def test_grok_stream_parser_exposes_incomplete_stream_state() -> None:
    parser = GrokStreamParser()
    parser.feed(json.dumps({"type": "text", "data": "unfinished"}))

    result = parser.finish()
    assert result.final_text == "unfinished"
    assert result.ended is False


def test_grok_stream_parser_rejects_non_object_event() -> None:
    parser = GrokStreamParser()

    with pytest.raises(GrokProviderError, match="non-object streaming event"):
        parser.feed("[]")

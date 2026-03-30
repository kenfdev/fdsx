"""Unit tests for summary_callback routing in Claude stream callback (T005, T006, T007).

Tests verify that:
- tool_use content_block_start → [tool: X] routed to summary_callback
- thinking_delta → [thinking] ... routed to summary_callback
- text_delta → text routed to output_callback (not summary_callback)
"""

import json

from fdsx.providers.claude import (
    _CONTENT_TYPE_TOOL_USE,
    _DELTA_TYPE_TEXT,
    _DELTA_TYPE_THINKING,
    _EVENT_CONTENT_BLOCK_DELTA,
    _EVENT_CONTENT_BLOCK_START,
    _EVENT_CONTENT_BLOCK_STOP,
    ClaudeProvider,
)


def _make_provider() -> ClaudeProvider:
    return ClaudeProvider()


def _build_text_delta_line(text: str, index: int = 0) -> str:
    return json.dumps(
        {
            "type": _EVENT_CONTENT_BLOCK_DELTA,
            "index": index,
            "delta": {"type": _DELTA_TYPE_TEXT, "text": text},
        }
    )


def _build_thinking_delta_line(thinking: str, index: int = 0) -> str:
    return json.dumps(
        {
            "type": _EVENT_CONTENT_BLOCK_DELTA,
            "index": index,
            "delta": {"type": _DELTA_TYPE_THINKING, "thinking": thinking},
        }
    )


def _build_tool_use_start_line(tool_name: str, index: int = 1) -> str:
    return json.dumps(
        {
            "type": _EVENT_CONTENT_BLOCK_START,
            "index": index,
            "content_block": {
                "type": _CONTENT_TYPE_TOOL_USE,
                "id": "tu_001",
                "name": tool_name,
            },
        }
    )


def _build_content_block_stop_line(index: int = 0) -> str:
    return json.dumps(
        {
            "type": _EVENT_CONTENT_BLOCK_STOP,
            "index": index,
        }
    )


def _build_input_json_delta_line(partial_json: str, index: int = 1) -> str:
    return json.dumps(
        {
            "type": _EVENT_CONTENT_BLOCK_DELTA,
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial_json},
        }
    )


class TestToolStartCallsSummaryCallback:
    """T005: tool_use content_block_stop routes [tool: X] or [X] summary to summary_callback."""

    def test_tool_stop_routes_to_summary_callback(self) -> None:
        """When summary_callback is provided, tool summary goes to summary_callback only on stop."""
        output_received: list[str] = []
        summary_received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            output_received.append, summary_callback=summary_received.append
        )

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_content_block_stop_line())

        assert summary_received == ["[tool: Bash]"]
        assert output_received == []

    def test_tool_stop_routes_to_output_when_no_summary_callback(self) -> None:
        """When summary_callback is None, tool summary falls back to output_callback on stop."""
        output_received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(output_received.append)

        cb(_build_tool_use_start_line("Read"))
        cb(_build_content_block_stop_line())

        assert output_received == ["[tool: Read]"]

    def test_tool_stop_with_input_json_routes_formatted_to_summary(self) -> None:
        """When input_json_delta is provided, formatted [Bash] ls goes to summary_callback."""
        output_received: list[str] = []
        summary_received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            output_received.append, summary_callback=summary_received.append
        )

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_input_json_delta_line('{"command": "ls /workspace"}'))
        cb(_build_content_block_stop_line())

        assert summary_received == ["[Bash] ls /workspace"]
        assert output_received == []


class TestThinkingCallsSummaryCallback:
    """T006: thinking_delta routes [thinking] ... to summary_callback."""

    def test_thinking_routes_to_summary_callback_on_flush(self) -> None:
        """When summary_callback is provided, [thinking] goes to summary_callback on flush."""
        output_received: list[str] = []
        summary_received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(
            output_received.append, summary_callback=summary_received.append
        )

        cb(_build_thinking_delta_line("Let me think..."))
        flush()

        assert summary_received == ["[thinking] Let me think..."]
        assert output_received == []

    def test_thinking_routes_to_summary_callback_on_newline(self) -> None:
        """When summary_callback is provided, thinking fragments with newlines route immediately."""
        output_received: list[str] = []
        summary_received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(
            output_received.append, summary_callback=summary_received.append
        )

        cb(_build_thinking_delta_line("line one\nline two"))
        flush()

        assert summary_received == ["[thinking] line one", "[thinking] line two"]
        assert output_received == []

    def test_thinking_routes_to_output_when_no_summary_callback(self) -> None:
        """When summary_callback is None, [thinking] falls back to output_callback."""
        output_received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(output_received.append)

        cb(_build_thinking_delta_line("reasoning..."))
        flush()

        assert output_received == ["[thinking] reasoning..."]

    def test_thinking_to_text_transition_flushes_to_summary(self) -> None:
        """Switching from thinking to text flushes the thinking buffer to summary_callback."""
        output_received: list[str] = []
        summary_received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(
            output_received.append, summary_callback=summary_received.append
        )

        cb(_build_thinking_delta_line("reasoning"))
        assert summary_received == []

        cb(_build_text_delta_line("visible output"))
        assert summary_received == ["[thinking] reasoning"]

        flush()
        assert summary_received == ["[thinking] reasoning"]
        assert output_received == ["visible output"]


class TestTextDeltaCallsOutputCallback:
    """T007: text_delta routes text to output_callback (not summary_callback)."""

    def test_text_delta_routes_to_output_callback(self) -> None:
        """text_delta content goes to output_callback regardless of summary_callback."""
        output_received: list[str] = []
        summary_received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(
            output_received.append, summary_callback=summary_received.append
        )

        cb(_build_text_delta_line("Hello"))
        cb(_build_text_delta_line(" world"))
        flush()

        assert output_received == ["Hello world"]
        assert summary_received == []

    def test_text_delta_routes_to_output_callback_with_newlines(self) -> None:
        """text_delta with embedded newlines emits to output_callback, not summary_callback."""
        output_received: list[str] = []
        summary_received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(
            output_received.append, summary_callback=summary_received.append
        )

        cb(_build_text_delta_line("line one\nline two"))
        flush()

        assert output_received == ["line one", "line two"]
        assert summary_received == []

    def test_text_and_thinking_interleaved(self) -> None:
        """Interleaved text and thinking routes correctly to their respective callbacks."""
        output_received: list[str] = []
        summary_received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(
            output_received.append, summary_callback=summary_received.append
        )

        cb(_build_text_delta_line("start"))
        cb(_build_thinking_delta_line("thinking..."))
        cb(_build_text_delta_line("end"))
        flush()

        assert output_received == ["start", "end"]
        assert summary_received == ["[thinking] thinking..."]


class TestToolStartEndCallbacks:
    """T011: on_tool_start / on_tool_end callbacks fire on tool_use block boundaries."""

    def test_tool_start_triggers_on_tool_start(self) -> None:
        """content_block_start(tool_use) triggers on_tool_start callback."""
        output_received: list[str] = []
        on_tool_start_called: list[bool] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            output_received.append,
            on_tool_start=lambda: on_tool_start_called.append(True),
        )

        cb(_build_tool_use_start_line("Bash"))

        assert on_tool_start_called == [True]

    def test_tool_end_triggers_on_tool_end(self) -> None:
        """content_block_stop after tool_use start triggers on_tool_end callback."""
        output_received: list[str] = []
        on_tool_end_called: list[bool] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            output_received.append,
            on_tool_end=lambda: on_tool_end_called.append(True),
        )

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_content_block_stop_line())

        assert on_tool_end_called == [True]

    def test_non_tool_block_stop_does_not_trigger_on_tool_end(self) -> None:
        """content_block_stop without preceding tool_use start does not trigger on_tool_end."""
        output_received: list[str] = []
        on_tool_end_called: list[bool] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            output_received.append,
            on_tool_end=lambda: on_tool_end_called.append(True),
        )

        cb(_build_content_block_stop_line())

        assert on_tool_end_called == []

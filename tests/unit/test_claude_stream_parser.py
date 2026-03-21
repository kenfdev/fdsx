"""Unit tests for Claude stream-json NDJSON parser (_make_stream_callback).

Tests verify correct handling of:
- text_delta events → text forwarded to callback and accumulated
- thinking_delta events → thinking text forwarded to callback
- tool_use content_block_start → tool name notification dispatched
- result event → get_result() returns result field text
- malformed JSON lines → silently skipped
- missing result event → fallback to concatenated text_delta content
- non-streaming / unknown event types → silently ignored
"""

import json

from fdsx.providers.claude import (
    ClaudeProvider,
    _CONTENT_TYPE_TOOL_USE,
    _DELTA_TYPE_TEXT,
    _DELTA_TYPE_THINKING,
    _EVENT_CONTENT_BLOCK_DELTA,
    _EVENT_CONTENT_BLOCK_START,
    _EVENT_RESULT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider() -> ClaudeProvider:
    return ClaudeProvider()


def _build_text_delta_line(text: str, index: int = 0) -> str:
    return json.dumps({"type": _EVENT_CONTENT_BLOCK_DELTA, "index": index, "delta": {"type": _DELTA_TYPE_TEXT, "text": text}})


def _build_thinking_delta_line(thinking: str, index: int = 0) -> str:
    return json.dumps({"type": _EVENT_CONTENT_BLOCK_DELTA, "index": index, "delta": {"type": _DELTA_TYPE_THINKING, "thinking": thinking}})


def _build_tool_use_start_line(tool_name: str, index: int = 1) -> str:
    return json.dumps({"type": _EVENT_CONTENT_BLOCK_START, "index": index, "content_block": {"type": _CONTENT_TYPE_TOOL_USE, "id": "tu_001", "name": tool_name}})


def _build_result_line(result_text: str) -> str:
    return json.dumps({"type": _EVENT_RESULT, "subtype": "success", "is_error": False, "result": result_text})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTextDelta:
    """text_delta events dispatch text to callback and accumulate for fallback."""

    def test_text_dispatched_to_callback(self) -> None:
        """Each text_delta fragment is dispatched to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line("Hello"))
        cb(_build_text_delta_line(" world"))

        assert received == ["Hello", " world"]

    def test_empty_text_delta_not_dispatched(self) -> None:
        """An empty text_delta string is not forwarded to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line(""))

        assert received == []

    def test_text_accumulated_for_fallback(self) -> None:
        """text_delta fragments are concatenated when result event is absent."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_text_delta_line("foo"))
        cb(_build_text_delta_line("bar"))

        assert get_result() == "foobar"

    def test_empty_text_delta_not_accumulated_in_fallback(self) -> None:
        """Empty text_delta strings are not accumulated into text_parts (boundary-check)."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_text_delta_line(""))
        cb(_build_text_delta_line(""))
        # Only empty deltas — no result event — fallback should return None, not ""
        assert get_result() is None


class TestThinkingDelta:
    """thinking_delta events dispatch thinking text to callback."""

    def test_thinking_dispatched_to_callback(self) -> None:
        """Non-empty thinking text is forwarded to output_callback with [thinking] prefix."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_thinking_delta_line("Let me think..."))

        assert received == ["[thinking] Let me think..."]

    def test_empty_thinking_not_dispatched(self) -> None:
        """An empty thinking string is not forwarded to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_thinking_delta_line(""))

        assert received == []

    def test_thinking_does_not_contribute_to_fallback(self) -> None:
        """thinking_delta content is NOT included in the fallback text accumulation."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_thinking_delta_line("internal reasoning"))
        # No text_delta lines, no result event → get_result should return None
        assert get_result() is None


class TestToolUseContentBlockStart:
    """tool_use content_block_start events dispatch a [tool: name] notification."""

    def test_tool_name_dispatched(self) -> None:
        """Tool name is dispatched to output_callback as '[tool: <name>]'."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))

        assert received == ["[tool: Bash]"]

    def test_unknown_tool_name_falls_back(self) -> None:
        """Missing tool name in content_block falls back to 'unknown'."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        line = json.dumps({"type": _EVENT_CONTENT_BLOCK_START, "index": 0, "content_block": {"type": _CONTENT_TYPE_TOOL_USE}})
        cb(line)

        assert received == ["[tool: unknown]"]

    def test_non_tool_use_content_block_start_ignored(self) -> None:
        """content_block_start with type != tool_use is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        line = json.dumps({"type": _EVENT_CONTENT_BLOCK_START, "index": 0, "content_block": {"type": "text", "text": ""}})
        cb(line)

        assert received == []


class TestResultEvent:
    """result event with subtype=success sets the final stdout."""

    def test_result_text_returned_by_get_result(self) -> None:
        """get_result() returns the result field from the result event."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_result_line("Final answer text"))

        assert get_result() == "Final answer text"

    def test_result_takes_precedence_over_fallback(self) -> None:
        """result event text takes precedence over accumulated text_delta content."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_text_delta_line("delta text"))
        cb(_build_result_line("canonical result"))

        assert get_result() == "canonical result"

    def test_result_with_error_subtype_still_captured(self) -> None:
        """result event with error subtype still captures text (exit_code conveys error)."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        line = json.dumps({"type": _EVENT_RESULT, "subtype": "error", "result": "error message text"})
        cb(line)

        assert get_result() == "error message text"

    def test_empty_result_field(self) -> None:
        """result event with empty string result field returns empty string."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_result_line(""))

        assert get_result() == ""


class TestMalformedJsonSkip:
    """Malformed JSON lines are skipped with a warning logged."""

    def test_malformed_line_skipped(self) -> None:
        """A line that is not valid JSON is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(received.append)

        cb("not valid json {{{")

        assert received == []
        assert get_result() is None

    def test_malformed_line_logs_warning(self) -> None:
        """A malformed JSON line causes logger.warning to be called (FR-2.8)."""
        import logging
        from unittest.mock import patch

        provider = _make_provider()
        cb, _ = provider._make_stream_callback(lambda _: None)

        with patch("fdsx.providers.claude.logger") as mock_logger:
            cb("not valid json {{{")
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0]
            assert "Malformed JSON" in call_args[0]

    def test_processing_continues_after_malformed_line(self) -> None:
        """Valid lines after a malformed line are still processed."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(received.append)

        cb("not valid json")
        cb(_build_text_delta_line("good"))

        assert received == ["good"]

    def test_empty_line_skipped(self) -> None:
        """Empty and whitespace-only lines are silently skipped."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb("")
        cb("   ")
        cb("\t\n")

        assert received == []


class TestMissingResultFallback:
    """When result event is absent, get_result falls back to accumulated text_delta."""

    def test_no_events_returns_none(self) -> None:
        """With no events processed, get_result() returns None."""
        provider = _make_provider()
        _, get_result = provider._make_stream_callback(lambda _: None)

        assert get_result() is None

    def test_fallback_to_text_delta_accumulation(self) -> None:
        """Accumulated text_delta fragments are returned when result event is absent."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_text_delta_line("Hello"))
        cb(_build_text_delta_line("! "))
        cb(_build_text_delta_line("World"))
        # No result event

        assert get_result() == "Hello! World"


class TestNonStreamingEventIgnore:
    """Unknown or non-streaming event types are silently ignored."""

    def test_system_event_ignored(self) -> None:
        """system/init events produce no callback calls and no result."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(received.append)

        line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc"})
        cb(line)

        assert received == []
        assert get_result() is None

    def test_message_start_ignored(self) -> None:
        """message_start events are silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        line = json.dumps({"type": "message_start", "message": {}})
        cb(line)

        assert received == []

    def test_message_stop_ignored(self) -> None:
        """message_stop events are silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(json.dumps({"type": "message_stop"}))

        assert received == []

    def test_content_block_stop_ignored(self) -> None:
        """content_block_stop events are silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(json.dumps({"type": "content_block_stop", "index": 0}))

        assert received == []

    def test_unknown_event_type_ignored(self) -> None:
        """Completely unknown event types are silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(json.dumps({"type": "some_future_event", "data": "stuff"}))

        assert received == []

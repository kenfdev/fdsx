"""Unit tests for Cursor stream-json NDJSON parser (_make_stream_callback).

Tests verify correct handling of:
- system/init event → debug log only, nothing emitted
- assistant delta with text parts → text dispatched and accumulated
- assistant delta with non-text parts → DEBUG once, nothing emitted
- assistant buffered flush (has model_call_id) → skipped entirely
- tool_call/started → [tool: <key>] to summary_callback, on_tool_start called
- tool_call/completed → on_tool_end called
- result → completion_event set
- unknown event types → DEBUG + skip
- malformed JSON lines → WARNING + skip
"""

import json
import threading
from unittest.mock import patch

from fdsx.providers.cursor import CursorProvider


def _make_provider() -> CursorProvider:
    return CursorProvider()


def _build_system_init_line(model: str = "cursor-fast") -> str:
    return json.dumps({"type": "system", "subtype": "init", "model": model})


def _build_assistant_text_line(text: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


def _build_assistant_non_text_line(tool_id: str = "x") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": tool_id}]},
        }
    )


def _build_assistant_buffered_flush_line(model_call_id: str = "mc_123") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "model_call_id": model_call_id,
            "message": {"content": [{"type": "text", "text": "buffered text"}]},
        }
    )


def _build_tool_call_started_line(tool_key: str = "writeToolCall") -> str:
    return json.dumps({"type": "tool_call", "subtype": "started", "toolKey": tool_key})


def _build_tool_call_completed_line(tool_key: str = "writeToolCall") -> str:
    return json.dumps(
        {"type": "tool_call", "subtype": "completed", "toolKey": tool_key}
    )


def _build_result_line(status: str = "success") -> str:
    return json.dumps({"type": "result", "status": status})


def _build_unknown_event_line() -> str:
    return json.dumps({"type": "future_event"})


class TestSystemInitEvent:
    """system/init event is logged at debug level, nothing emitted to output_callback."""

    def test_system_init_not_dispatched(self) -> None:
        """system/init event produces no output to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_system_init_line())
        flush()

        assert received == []

    def test_system_init_logs_debug(self) -> None:
        """system/init event causes logger.debug to be called."""
        provider = _make_provider()
        with patch("fdsx.providers.cursor.logger") as mock_logger:
            cb, _, _ = provider._make_stream_callback(lambda _: None)
            cb(_build_system_init_line())
            mock_logger.debug.assert_called()


class TestAssistantTextDelta:
    """assistant delta events with text parts dispatch text and accumulate."""

    def test_assistant_text_dispatched_to_callback(self) -> None:
        """assistant text delta is forwarded to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_assistant_text_line("Hello"))
        flush()

        assert received == ["Hello"]

    def test_assistant_text_accumulated_for_fallback(self) -> None:
        """assistant text parts are concatenated in text_parts for fallback."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_assistant_text_line("Hello"))
        cb(_build_assistant_text_line(" world"))

        assert get_result() == "Hello world"

    def test_multiple_text_parts_in_one_event(self) -> None:
        """Multiple text parts in one assistant event are concatenated."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result, flush = provider._make_stream_callback(received.append)

        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "part1"},
                        {"type": "text", "text": " part2"},
                    ]
                },
            }
        )
        cb(line)
        flush()

        assert get_result() == "part1 part2"


class TestAssistantNonTextParts:
    """assistant delta with non-text content parts → DEBUG once, no output_callback."""

    def test_non_text_part_not_dispatched(self) -> None:
        """Non-text content part produces no output to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_assistant_non_text_line())
        flush()

        assert received == []

    def test_non_text_part_logs_debug_once(self) -> None:
        """Multiple non-text parts still log debug exactly once per _make_stream_callback call."""
        provider = _make_provider()
        with patch("fdsx.providers.cursor.logger") as mock_logger:
            cb, _, _ = provider._make_stream_callback(lambda _: None)
            cb(_build_assistant_non_text_line("id-1"))
            cb(_build_assistant_non_text_line("id-2"))
            # Exactly one debug call total for non-text content,
            # regardless of how many non-text events arrive.
            assert mock_logger.debug.call_count == 1


class TestAssistantBufferedFlush:
    """assistant events with model_call_id (buffered flush) are skipped entirely."""

    def test_buffered_flush_not_dispatched(self) -> None:
        """assistant event with model_call_id produces no output."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result, flush = provider._make_stream_callback(received.append)

        cb(_build_assistant_buffered_flush_line())
        flush()

        assert received == []
        assert get_result() is None


class TestToolCallStarted:
    """tool_call/started events emit [tool: <key>] and call on_tool_start."""

    def test_tool_call_started_summary_dispatched(self) -> None:
        """tool_call/started emits '[tool: <toolKey>]' to summary_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            lambda _: None, summary_callback=received.append
        )

        cb(_build_tool_call_started_line("writeToolCall"))

        assert "[tool: writeToolCall]" in received

    def test_tool_call_started_on_tool_start_called(self) -> None:
        """tool_call/started calls the on_tool_start hook."""
        calls: list[bool] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            lambda _: None, on_tool_start=lambda: calls.append(True)
        )

        cb(_build_tool_call_started_line())

        assert calls == [True]

    def test_tool_call_started_fallback_to_output_callback(self) -> None:
        """When no summary_callback, [tool: <key>] falls back to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_call_started_line("readTool"))

        assert "[tool: readTool]" in received


class TestToolCallCompleted:
    """tool_call/completed events call the on_tool_end hook."""

    def test_on_tool_end_called(self) -> None:
        """tool_call/completed calls the on_tool_end hook."""
        calls: list[bool] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            lambda _: None, on_tool_end=lambda: calls.append(True)
        )

        cb(_build_tool_call_completed_line())

        assert calls == [True]


class TestResultEvent:
    """result event sets completion_event."""

    def test_result_signals_completion(self) -> None:
        """result event sets the completion_event."""
        completion_event = threading.Event()
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            lambda _: None, completion_event=completion_event
        )

        cb(_build_result_line())

        assert completion_event.is_set()

    def test_result_no_output_dispatched(self) -> None:
        """result event produces no output to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_result_line())
        flush()

        assert received == []


class TestDuplicateFinalMessage:
    """cursor agent sends a 'final complete' assistant event that repeats all streaming text."""

    def test_duplicate_final_event_not_redisplayed(self) -> None:
        """Second assistant event with full accumulated text is silently skipped."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result, flush = provider._make_stream_callback(received.append)

        cb(_build_assistant_text_line("Hello world"))
        cb(_build_assistant_text_line("Hello world"))  # cursor's final complete repeat
        flush()

        assert received == ["Hello world"]
        assert get_result() == "Hello world"

    def test_duplicate_multi_chunk_final_event_not_redisplayed(self) -> None:
        """Final event repeating full streamed text is skipped after multi-chunk streaming."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result, flush = provider._make_stream_callback(received.append)

        cb(_build_assistant_text_line("Hello"))
        cb(_build_assistant_text_line(" world"))
        # cursor sends the full accumulated text as the final assistant event
        full = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Hello world"}]},
            }
        )
        cb(full)
        flush()

        assert "".join(received) == "Hello world"
        assert get_result() == "Hello world"


class TestGetResult:
    """get_result() returns accumulated text_parts as fallback stdout."""

    def test_get_result_returns_text_parts(self) -> None:
        """get_result() returns concatenated text_parts after streaming."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_assistant_text_line("Hello"))
        cb(_build_assistant_text_line(" world"))
        cb(_build_result_line())

        assert get_result() == "Hello world"

    def test_get_result_returns_none_when_empty(self) -> None:
        """get_result() returns None when no text_parts accumulated."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_result_line())

        assert get_result() is None


class TestUnknownEvent:
    """Unknown event types are silently skipped with a debug log."""

    def test_unknown_event_not_dispatched(self) -> None:
        """Unknown event type produces no output to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_unknown_event_line())
        flush()

        assert received == []

    def test_unknown_event_logs_debug(self) -> None:
        """Unknown event type causes logger.debug to be called."""
        provider = _make_provider()
        with patch("fdsx.providers.cursor.logger") as mock_logger:
            cb, _, _ = provider._make_stream_callback(lambda _: None)
            cb(_build_unknown_event_line())
            mock_logger.debug.assert_called()


class TestMalformedJsonSkip:
    """Malformed JSON lines are skipped with a warning logged."""

    def test_malformed_line_skipped(self) -> None:
        """A line that is not valid JSON is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result, flush = provider._make_stream_callback(received.append)

        cb("not valid json {{{")
        flush()

        assert received == []
        assert get_result() is None

    def test_malformed_line_logs_warning(self) -> None:
        """A malformed JSON line causes logger.warning to be called."""
        provider = _make_provider()
        with patch("fdsx.providers.cursor.logger") as mock_logger:
            cb, _, _ = provider._make_stream_callback(lambda _: None)
            cb("not valid json {{{")
            mock_logger.warning.assert_called_once()
            # Do NOT assert on the warning message text — only behavior matters


class TestEmptyLineSkipped:
    """Empty and whitespace-only lines are silently skipped."""

    def test_empty_line_produces_no_output(self) -> None:
        """An empty string line is silently ignored."""
        received: list[str] = []
        completion_event = threading.Event()
        provider = _make_provider()
        cb, get_result, flush = provider._make_stream_callback(
            received.append, completion_event=completion_event
        )

        cb("")
        flush()

        assert received == []
        assert not completion_event.is_set()
        assert get_result() is None

    def test_whitespace_only_line_produces_no_output(self) -> None:
        """A whitespace-only line is silently ignored."""
        received: list[str] = []
        completion_event = threading.Event()
        provider = _make_provider()
        cb, get_result, flush = provider._make_stream_callback(
            received.append, completion_event=completion_event
        )

        cb("   \t  ")
        flush()

        assert received == []
        assert not completion_event.is_set()
        assert get_result() is None

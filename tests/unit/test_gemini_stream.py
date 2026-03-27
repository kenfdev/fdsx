"""Unit tests for Gemini stream-json NDJSON parser (_make_stream_callback).

Tests verify correct handling of:
- init event → debug log only, nothing emitted
- message with role=user → silently skipped
- message with role=assistant and delta=true → text dispatched and accumulated
- tool_use → [tool: name] notification, on_tool_start hook called
- tool_result → on_tool_end hook called
- error → error message dispatched via summary_callback
- result → completion_event set, buffer flushed
- malformed JSON lines → silently skipped with warning
- get_result() → returns accumulated text_parts (no result field in Gemini result event)
- line buffering: fragments accumulated, emitted on newline or flush
"""

import json
import threading

from fdsx.providers.gemini import GeminiProvider


def _make_provider() -> GeminiProvider:
    return GeminiProvider()


def _build_init_line(
    session_id: str = "test-session-123", model: str = "gemini-2.0-flash-exp"
) -> str:
    return json.dumps(
        {
            "type": "init",
            "timestamp": "2026-03-27T10:00:00Z",
            "session_id": session_id,
            "model": model,
        }
    )


def _build_message_line(role: str, content: str = "", delta: bool = False) -> str:
    event: dict[str, object] = {
        "type": "message",
        "timestamp": "2026-03-27T10:00:00Z",
        "role": role,
    }
    if delta:
        event["delta"] = True
    if content:
        event["content"] = content
    return json.dumps(event)


def _build_tool_use_line(tool_name: str = "Read", tool_id: str = "read-123") -> str:
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": "2026-03-27T10:00:00Z",
            "tool_name": tool_name,
            "tool_id": tool_id,
            "parameters": {},
        }
    )


def _build_tool_result_line(
    tool_id: str = "read-123", status: str = "success", output: str = "file content"
) -> str:
    event: dict[str, object] = {
        "type": "tool_result",
        "timestamp": "2026-03-27T10:00:00Z",
        "tool_id": tool_id,
        "status": status,
    }
    if status == "success":
        event["output"] = output
    else:
        event["error"] = {"type": "TOOL_EXECUTION_ERROR", "message": "failed"}
    return json.dumps(event)


def _build_error_line(message: str, severity: str = "warning") -> str:
    return json.dumps(
        {
            "type": "error",
            "timestamp": "2026-03-27T10:00:00Z",
            "severity": severity,
            "message": message,
        }
    )


def _build_result_line(status: str = "success") -> str:
    event: dict[str, object] = {
        "type": "result",
        "timestamp": "2026-03-27T10:00:00Z",
        "status": status,
        "stats": {
            "total_tokens": 100,
            "input_tokens": 50,
            "output_tokens": 50,
            "duration_ms": 1200,
            "tool_calls": 0,
            "models": {},
        },
    }
    if status == "error":
        event["error"] = {"type": "UNKNOWN_ERROR", "message": "something went wrong"}
    return json.dumps(event)


class TestInitEvent:
    """init event is logged at debug level, nothing emitted to output_callback."""

    def test_init_event_not_dispatched(self) -> None:
        """init event produces no output to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_init_line())
        flush()

        assert received == []


class TestUserMessage:
    """user message events are silently skipped."""

    def test_user_message_skipped(self) -> None:
        """message with role=user produces no output."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_message_line(role="user", content="Hello world"))
        flush()

        assert received == []


class TestAssistantMessage:
    """assistant message with delta=true events dispatch text and accumulate."""

    def test_assistant_delta_dispatched_to_callback(self) -> None:
        """assistant message with delta=true is forwarded to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_message_line(role="assistant", content="Hello", delta=True))
        flush()

        assert received == ["Hello"]

    def test_assistant_delta_accumulated_for_fallback(self) -> None:
        """assistant delta content is concatenated in text_parts for fallback."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_message_line(role="assistant", content="Hello", delta=True))
        cb(_build_message_line(role="assistant", content=" world", delta=True))

        assert get_result() == "Hello world"

    def test_empty_content_not_dispatched(self) -> None:
        """assistant message with empty content produces no output."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_message_line(role="assistant", content="", delta=True))
        flush()

        assert received == []

    def test_line_buffering_with_newline(self) -> None:
        """Text fragments containing newlines are split and emitted as separate lines."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_message_line(role="assistant", content="Hello\nworld", delta=True))
        flush()

        assert received == ["Hello", "world"]


class TestToolUse:
    """tool_use events emit [tool: name] notification and call on_tool_start hook."""

    def test_tool_name_dispatched(self) -> None:
        """tool_use emits '[tool: <name>]' to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_line(tool_name="Bash"))

        assert received == ["[tool: Bash]"]

    def test_on_tool_start_called(self) -> None:
        """tool_use calls the on_tool_start hook."""
        calls: list[bool] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            lambda _: None, on_tool_start=lambda: calls.append(True)
        )

        cb(_build_tool_use_line(tool_name="Read"))

        assert calls == [True]

    def test_tool_use_flushes_buffer(self) -> None:
        """tool_use flushes any pending text before emitting."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_message_line(role="assistant", content="before tool", delta=True))
        cb(_build_tool_use_line(tool_name="Bash"))

        assert received == ["before tool", "[tool: Bash]"]


class TestToolResult:
    """tool_result events call the on_tool_end hook."""

    def test_on_tool_end_called(self) -> None:
        """tool_result calls the on_tool_end hook."""
        calls: list[bool] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            lambda _: None, on_tool_end=lambda: calls.append(True)
        )

        cb(_build_tool_result_line())

        assert calls == [True]


class TestErrorEvent:
    """error events dispatch the error message via summary_callback or output_callback."""

    def test_error_message_dispatched(self) -> None:
        """error event emits the error message."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_error_line("Loop detected, stopping execution"))

        assert received == ["Loop detected, stopping execution"]

    def test_error_flushes_buffer(self) -> None:
        """error event flushes any pending text before emitting error message."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_message_line(role="assistant", content="before error", delta=True))
        cb(_build_error_line("Max turns exceeded"))
        flush()

        assert received == ["before error", "Max turns exceeded"]


class TestResultEvent:
    """result event sets completion_event and flushes buffer."""

    def test_result_signals_completion(self) -> None:
        """result event sets the completion_event."""
        completion_event = threading.Event()
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(
            lambda _: None, completion_event=completion_event
        )

        cb(_build_result_line())

        assert completion_event.is_set()

    def test_result_flushes_buffer(self) -> None:
        """result event flushes any pending text."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_message_line(role="assistant", content="final answer", delta=True))
        cb(_build_result_line())

        assert received == ["final answer"]


class TestGetResult:
    """get_result() returns accumulated text_parts (Gemini has no result text field)."""

    def test_get_result_returns_text_parts(self) -> None:
        """get_result() returns concatenated text_parts when result event has no text."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_message_line(role="assistant", content="Hello", delta=True))
        cb(_build_message_line(role="assistant", content=" world", delta=True))
        cb(_build_result_line())

        assert get_result() == "Hello world"

    def test_get_result_returns_none_when_empty(self) -> None:
        """get_result() returns None when no text_parts accumulated."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_result_line())

        assert get_result() is None


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
        from unittest.mock import patch

        provider = _make_provider()

        with patch("fdsx.providers.gemini.logger") as mock_logger:
            cb, _, _ = provider._make_stream_callback(lambda _: None)
            cb("not valid json {{{")
            mock_logger.warning.assert_called_once()
            assert "Malformed JSON line skipped" in mock_logger.warning.call_args[0][0]


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

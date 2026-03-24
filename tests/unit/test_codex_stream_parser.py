"""Unit tests for Codex --json JSONL parser (_make_stream_callback).

Tests verify correct handling of:
- agent_message item.completed → text forwarded to callback and accumulated
- reasoning item.completed → thinking text forwarded to callback with [thinking] prefix
- command_execution item.started → [tool: {command}] dispatched to callback
- file_change item.started → [tool: file_change] dispatched to callback
- mcp_tool_call item.started → [tool: {name}] dispatched to callback
- turn.failed event → warning logged, no callback
- malformed JSON lines → silently skipped with warning
- multiple agent_message events → concatenated in get_result()
- partial collection on crash → get_result() returns partial content
"""

import json
import threading

from fdsx.providers.codex import (
    CodexProvider,
    _EVENT_ERROR,
    _EVENT_ITEM_COMPLETED,
    _EVENT_ITEM_STARTED,
    _EVENT_TURN_FAILED,
    _ITEM_TYPE_AGENT_MESSAGE,
    _ITEM_TYPE_COMMAND_EXECUTION,
    _ITEM_TYPE_FILE_CHANGE,
    _ITEM_TYPE_MCP_TOOL_CALL,
    _ITEM_TYPE_REASONING,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_provider() -> CodexProvider:
    return CodexProvider()


def _build_item_started(item_type: str, **kwargs: object) -> str:
    item: dict[str, object] = {"id": "item_001", "type": item_type}
    item.update(kwargs)
    return json.dumps({"type": _EVENT_ITEM_STARTED, "item": item})


def _build_item_completed(item_type: str, **kwargs: object) -> str:
    item: dict[str, object] = {"id": "item_001", "type": item_type}
    item.update(kwargs)
    return json.dumps({"type": _EVENT_ITEM_COMPLETED, "item": item})


def _build_turn_failed(error: str = "Something went wrong") -> str:
    return json.dumps({"type": _EVENT_TURN_FAILED, "error": error})


# ---------------------------------------------------------------------------
# Tests: agent_message
# ---------------------------------------------------------------------------


class TestAgentMessage:
    """item.completed agent_message events dispatch text to callback and accumulate."""

    def test_text_dispatched_to_callback(self) -> None:
        """agent_message text is forwarded to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="Hello!"))

        assert received == ["Hello!"]

    def test_empty_text_not_dispatched(self) -> None:
        """An empty agent_message text is not forwarded to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text=""))

        assert received == []

    def test_text_accumulated_for_result(self) -> None:
        """agent_message texts are joined with newline separator in get_result()."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="Hello! "))
        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="World"))

        assert get_result() == "Hello! \nWorld"

    def test_no_agent_message_returns_none(self) -> None:
        """With no agent_message events, get_result() returns None."""
        provider = _make_provider()
        _, get_result = provider._make_stream_callback(lambda _: None)

        assert get_result() is None

    def test_agent_message_started_ignored(self) -> None:
        """item.started for agent_message is silently ignored (text arrives on completed)."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(received.append)

        cb(_build_item_started(_ITEM_TYPE_AGENT_MESSAGE, text=""))

        assert received == []
        assert get_result() is None


# ---------------------------------------------------------------------------
# Tests: reasoning
# ---------------------------------------------------------------------------


class TestReasoning:
    """item.completed reasoning events dispatch thinking text to callback."""

    def test_thinking_dispatched_with_prefix(self) -> None:
        """Non-empty reasoning text is forwarded with [thinking] prefix."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_item_completed(_ITEM_TYPE_REASONING, text="Let me think..."))

        assert received == ["[thinking] Let me think..."]

    def test_empty_reasoning_not_dispatched(self) -> None:
        """An empty reasoning text is not forwarded to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_item_completed(_ITEM_TYPE_REASONING, text=""))

        assert received == []

    def test_reasoning_does_not_contribute_to_result(self) -> None:
        """Reasoning text is NOT included in get_result() accumulation."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_item_completed(_ITEM_TYPE_REASONING, text="internal reasoning"))
        # No agent_message events → get_result should return None
        assert get_result() is None


# ---------------------------------------------------------------------------
# Tests: command_execution
# ---------------------------------------------------------------------------


class TestCommandExecution:
    """item.started command_execution events dispatch [tool: {command}] to callback."""

    def test_command_dispatched(self) -> None:
        """Command is dispatched to output_callback as '[tool: <command>]'."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_item_started(_ITEM_TYPE_COMMAND_EXECUTION, command="echo hello"))

        assert received == ["[tool: echo hello]"]

    def test_missing_command_falls_back_to_unknown(self) -> None:
        """Missing command field falls back to 'unknown'."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        line = json.dumps(
            {
                "type": _EVENT_ITEM_STARTED,
                "item": {"id": "x", "type": _ITEM_TYPE_COMMAND_EXECUTION},
            }
        )
        cb(line)

        assert received == ["[tool: unknown]"]

    def test_command_execution_completed_ignored(self) -> None:
        """item.completed for command_execution is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(
            _build_item_completed(
                _ITEM_TYPE_COMMAND_EXECUTION, command="echo hello", output="hello"
            )
        )

        assert received == []


# ---------------------------------------------------------------------------
# Tests: file_change
# ---------------------------------------------------------------------------


class TestFileChange:
    """item.started file_change events dispatch [tool: file_change] to callback."""

    def test_file_change_dispatched(self) -> None:
        """File change event dispatches '[tool: file_change]' to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_item_started(_ITEM_TYPE_FILE_CHANGE, path="src/main.py"))

        assert received == ["[tool: file_change]"]

    def test_file_change_completed_ignored(self) -> None:
        """item.completed for file_change is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_item_completed(_ITEM_TYPE_FILE_CHANGE, path="src/main.py"))

        assert received == []


# ---------------------------------------------------------------------------
# Tests: mcp_tool_call
# ---------------------------------------------------------------------------


class TestMcpToolCall:
    """item.started mcp_tool_call events dispatch [tool: {name}] to callback."""

    def test_tool_name_dispatched(self) -> None:
        """MCP tool name is dispatched as '[tool: <name>]'."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_item_started(_ITEM_TYPE_MCP_TOOL_CALL, name="web_search"))

        assert received == ["[tool: web_search]"]

    def test_missing_name_falls_back_to_unknown(self) -> None:
        """Missing name field falls back to 'unknown'."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        line = json.dumps(
            {
                "type": _EVENT_ITEM_STARTED,
                "item": {"id": "x", "type": _ITEM_TYPE_MCP_TOOL_CALL},
            }
        )
        cb(line)

        assert received == ["[tool: unknown]"]

    def test_mcp_tool_call_completed_ignored(self) -> None:
        """item.completed for mcp_tool_call is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(
            _build_item_completed(
                _ITEM_TYPE_MCP_TOOL_CALL, name="web_search", result=""
            )
        )

        assert received == []


# ---------------------------------------------------------------------------
# Tests: turn.failed
# ---------------------------------------------------------------------------


class TestTurnFailed:
    """turn.failed events log a warning and do not dispatch to callback."""

    def test_turn_failed_no_callback(self) -> None:
        """turn.failed does not dispatch any text to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb(_build_turn_failed("rate limit exceeded"))

        assert received == []

    def test_turn_failed_logs_warning(self) -> None:
        """turn.failed causes logger.warning to be called."""
        from unittest.mock import patch

        provider = _make_provider()
        cb, _ = provider._make_stream_callback(lambda _: None)

        with patch("fdsx.providers.codex.logger") as mock_logger:
            cb(_build_turn_failed("something went wrong"))
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0]
            assert "turn.failed" in call_args[0]

    def test_turn_failed_partial_content_still_available(self) -> None:
        """After turn.failed, previously accumulated agent_message text is still returned."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="partial"))
        cb(_build_turn_failed("crash"))

        assert get_result() == "partial"


# ---------------------------------------------------------------------------
# Tests: malformed JSON
# ---------------------------------------------------------------------------


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
        """A malformed JSON line causes logger.warning to be called."""
        from unittest.mock import patch

        provider = _make_provider()
        cb, _ = provider._make_stream_callback(lambda _: None)

        with patch("fdsx.providers.codex.logger") as mock_logger:
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
        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="good"))

        assert received == ["good"]
        assert get_result() == "good"

    def test_empty_line_skipped(self) -> None:
        """Empty and whitespace-only lines are silently skipped."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        cb("")
        cb("   ")
        cb("\t\n")

        assert received == []


# ---------------------------------------------------------------------------
# Tests: multiple agent_message concatenation
# ---------------------------------------------------------------------------


class TestMultipleAgentMessageConcatenation:
    """Multiple agent_message completed events are concatenated in order."""

    def test_three_messages_concatenated(self) -> None:
        """Three agent_message blocks are joined with newline separator."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="foo"))
        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="bar"))
        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="baz"))

        assert get_result() == "foo\nbar\nbaz"

    def test_order_preserved(self) -> None:
        """Concatenation order matches event arrival order."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(received.append)

        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="first "))
        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="second"))

        assert received == ["first ", "second"]
        assert get_result() == "first \nsecond"


# ---------------------------------------------------------------------------
# Tests: partial collection on crash
# ---------------------------------------------------------------------------


class TestPartialCollection:
    """get_result() returns accumulated agent_message content even without all events."""

    def test_partial_result_before_all_events(self) -> None:
        """If streaming stops early, collected agent_message content is still returned."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="partial answer"))
        # Simulate no further events (process crash)

        assert get_result() == "partial answer"

    def test_no_events_returns_none(self) -> None:
        """With no events at all, get_result() returns None."""
        provider = _make_provider()
        _, get_result = provider._make_stream_callback(lambda _: None)

        assert get_result() is None


# ---------------------------------------------------------------------------
# Tests: unknown event types ignored
# ---------------------------------------------------------------------------


class TestUnknownEventsIgnored:
    """Unknown event types and item types are silently ignored."""

    def test_unknown_top_level_event_ignored(self) -> None:
        """Completely unknown top-level event types produce no callback calls."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(received.append)

        cb(json.dumps({"type": "some_future_event", "data": "stuff"}))

        assert received == []
        assert get_result() is None

    def test_unknown_item_type_in_started_ignored(self) -> None:
        """item.started with an unknown item type is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        line = json.dumps(
            {"type": _EVENT_ITEM_STARTED, "item": {"id": "x", "type": "future_item"}}
        )
        cb(line)

        assert received == []

    def test_unknown_item_type_in_completed_ignored(self) -> None:
        """item.completed with an unknown item type is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(received.append)

        line = json.dumps(
            {"type": _EVENT_ITEM_COMPLETED, "item": {"id": "x", "type": "future_item"}}
        )
        cb(line)

        assert received == []
        assert get_result() is None


# ---------------------------------------------------------------------------
# Tests: error event
# ---------------------------------------------------------------------------


class TestErrorEvent:
    """error events log a warning and do not dispatch to callback."""

    def test_error_event_no_callback(self) -> None:
        """error event does not dispatch any text to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(received.append)

        line = json.dumps({"type": _EVENT_ERROR, "message": "something went wrong"})
        cb(line)

        assert received == []

    def test_error_event_logs_warning(self) -> None:
        """error event causes logger.warning to be called."""
        from unittest.mock import patch

        provider = _make_provider()
        cb, _ = provider._make_stream_callback(lambda _: None)

        with patch("fdsx.providers.codex.logger") as mock_logger:
            line = json.dumps({"type": _EVENT_ERROR, "message": "quota exceeded"})
            cb(line)
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0]
            assert "error" in call_args[0].lower()

    def test_error_event_partial_content_still_available(self) -> None:
        """After error event, previously accumulated agent_message text is still returned."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None)

        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="partial"))
        cb(json.dumps({"type": _EVENT_ERROR, "message": "network error"}))

        assert get_result() == "partial"


# ---------------------------------------------------------------------------
# Tests: completion_event
# ---------------------------------------------------------------------------


class TestCompletionEvent:
    """completion_event is set only on terminal events."""

    def test_completion_event_set_on_agent_message_completed(self) -> None:
        """item.completed + agent_message sets completion_event."""
        event = threading.Event()
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(lambda _: None, completion_event=event)

        assert not event.is_set()
        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="done"))
        assert event.is_set()

    def test_completion_event_set_on_turn_failed(self) -> None:
        """turn.failed event sets completion_event."""
        event = threading.Event()
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(lambda _: None, completion_event=event)

        assert not event.is_set()
        cb(_build_turn_failed("rate limit"))
        assert event.is_set()

    def test_completion_event_set_on_error(self) -> None:
        """error event sets completion_event."""
        event = threading.Event()
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(lambda _: None, completion_event=event)

        assert not event.is_set()
        cb(json.dumps({"type": _EVENT_ERROR, "message": "quota exceeded"}))
        assert event.is_set()

    def test_completion_event_not_set_on_non_terminal(self) -> None:
        """item.started and item.completed + reasoning do NOT set completion_event."""
        event = threading.Event()
        provider = _make_provider()
        cb, _ = provider._make_stream_callback(lambda _: None, completion_event=event)

        # item.started for agent_message — not terminal
        cb(_build_item_started(_ITEM_TYPE_AGENT_MESSAGE))
        assert not event.is_set()

        # item.completed + reasoning — not terminal
        cb(_build_item_completed(_ITEM_TYPE_REASONING, text="thinking"))
        assert not event.is_set()

    def test_completion_event_none_does_not_raise_on_terminal_events(self) -> None:
        """When completion_event=None, terminal events are handled without errors."""
        provider = _make_provider()
        cb, get_result = provider._make_stream_callback(lambda _: None, completion_event=None)

        # Should not raise
        cb(_build_item_completed(_ITEM_TYPE_AGENT_MESSAGE, text="result"))
        cb(_build_turn_failed("error"))
        cb(json.dumps({"type": _EVENT_ERROR, "message": "fail"}))

        assert get_result() == "result"

"""Unit tests for Claude stream-json NDJSON parser (_make_stream_callback).

Tests verify correct handling of:
- text_delta events → text buffered and emitted as lines
- thinking_delta events → thinking text buffered with [thinking] prefix
- tool_use content_block_start → tool name notification dispatched
- result event → get_result() returns result field text
- malformed JSON lines → silently skipped
- missing result event → fallback to concatenated text_delta content
- non-streaming / unknown event types → silently ignored
- line buffering: fragments accumulated, emitted on newline or content_block_stop
- stream_event envelope unwrapping
"""

import json

from fdsx.providers.claude import (
    _CONTENT_TYPE_TOOL_USE,
    _DELTA_TYPE_TEXT,
    _DELTA_TYPE_THINKING,
    _EVENT_CONTENT_BLOCK_DELTA,
    _EVENT_CONTENT_BLOCK_START,
    _EVENT_CONTENT_BLOCK_STOP,
    _EVENT_RESULT,
    ClaudeProvider,
    _format_tool_input_summary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    return json.dumps({"type": _EVENT_CONTENT_BLOCK_STOP, "index": index})


def _build_input_json_delta_line(partial_json: str, index: int = 1) -> str:
    return json.dumps(
        {
            "type": _EVENT_CONTENT_BLOCK_DELTA,
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial_json},
        }
    )


def _build_result_line(result_text: str) -> str:
    return json.dumps(
        {
            "type": _EVENT_RESULT,
            "subtype": "success",
            "is_error": False,
            "result": result_text,
        }
    )


def _wrap_in_stream_event(inner_json: str) -> str:
    """Wrap a raw event JSON string in the Claude CLI stream_event envelope."""
    return json.dumps({"type": "stream_event", "event": json.loads(inner_json)})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTextDelta:
    """text_delta events dispatch text to callback and accumulate for fallback."""

    def test_text_dispatched_to_callback(self) -> None:
        """text_delta fragments are buffered and emitted as one line on flush."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line("Hello"))
        cb(_build_text_delta_line(" world"))
        flush()

        assert received == ["Hello world"]

    def test_empty_text_delta_not_dispatched(self) -> None:
        """An empty text_delta string produces no output even after flush."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line(""))
        flush()

        assert received == []

    def test_text_accumulated_for_fallback(self) -> None:
        """text_delta fragments are concatenated when result event is absent."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_text_delta_line("foo"))
        cb(_build_text_delta_line("bar"))

        assert get_result() == "foobar"

    def test_empty_text_delta_not_accumulated_in_fallback(self) -> None:
        """Empty text_delta strings are not accumulated into text_parts (boundary-check)."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_text_delta_line(""))
        cb(_build_text_delta_line(""))
        # Only empty deltas — no result event — fallback should return None, not ""
        assert get_result() is None


class TestThinkingDelta:
    """thinking_delta events dispatch thinking text to callback."""

    def test_thinking_dispatched_to_callback(self) -> None:
        """Non-empty thinking text is forwarded to output_callback with [thinking] prefix on flush."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_thinking_delta_line("Let me think..."))
        flush()

        assert received == ["[thinking] Let me think..."]

    def test_empty_thinking_not_dispatched(self) -> None:
        """An empty thinking string is not forwarded to output_callback."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_thinking_delta_line(""))
        flush()

        assert received == []

    def test_thinking_does_not_contribute_to_fallback(self) -> None:
        """thinking_delta content is NOT included in the fallback text accumulation."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_thinking_delta_line("internal reasoning"))
        # No text_delta lines, no result event → get_result should return None
        assert get_result() is None


class TestToolUseContentBlockStart:
    """tool_use content_block_start events do not emit immediately; emission is deferred to content_block_stop."""

    def test_tool_start_emits_nothing(self) -> None:
        """Tool start alone produces no callback output."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))

        assert received == []

    def test_tool_stop_with_no_input_json_emits_fallback(self) -> None:
        """content_block_stop with no input_json_delta emits [tool: <name>]."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_content_block_stop_line())

        assert received == ["[tool: Bash]"]

    def test_unknown_tool_name_falls_back(self) -> None:
        """Missing tool name in content_block falls back to 'unknown'."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        line = json.dumps(
            {
                "type": _EVENT_CONTENT_BLOCK_START,
                "index": 0,
                "content_block": {"type": _CONTENT_TYPE_TOOL_USE},
            }
        )
        cb(line)
        cb(_build_content_block_stop_line())

        assert received == ["[tool: unknown]"]

    def test_non_tool_use_content_block_start_ignored(self) -> None:
        """content_block_start with type != tool_use is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        line = json.dumps(
            {
                "type": _EVENT_CONTENT_BLOCK_START,
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        )
        cb(line)

        assert received == []


class TestResultEvent:
    """result event with subtype=success sets the final stdout."""

    def test_result_text_returned_by_get_result(self) -> None:
        """get_result() returns the result field from the result event."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_result_line("Final answer text"))

        assert get_result() == "Final answer text"

    def test_text_parts_take_precedence_over_result(self) -> None:
        """Accumulated text_delta content takes precedence over result event.

        The result event's "result" field only contains the last text block,
        so in agentic responses earlier text blocks (with routing tags) would
        be lost if result took precedence.
        """
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_text_delta_line("delta text"))
        cb(_build_result_line("canonical result"))

        assert get_result() == "delta text"

    def test_result_with_error_subtype_still_captured(self) -> None:
        """result event with error subtype still captures text (exit_code conveys error)."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        line = json.dumps(
            {"type": _EVENT_RESULT, "subtype": "error", "result": "error message text"}
        )
        cb(line)

        assert get_result() == "error message text"

    def test_empty_result_field(self) -> None:
        """result event with empty string result field and no text_parts returns empty string."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_result_line(""))

        assert get_result() == ""

    def test_empty_result_with_text_parts_falls_back(self) -> None:
        """result event with empty result falls back to text_parts when available."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_text_delta_line("Hello world\n[STEP:1]"))
        cb(_build_result_line(""))

        assert get_result() == "Hello world\n[STEP:1]"


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
        """A malformed JSON line causes logger.warning to be called (FR-2.8)."""
        from unittest.mock import patch

        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(lambda _: None)

        with patch("fdsx.providers.claude.logger") as mock_logger:
            cb("not valid json {{{")
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0]
            assert "Malformed JSON" in call_args[0]

    def test_processing_continues_after_malformed_line(self) -> None:
        """Valid lines after a malformed line are still processed."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb("not valid json")
        cb(_build_text_delta_line("good"))
        flush()

        assert received == ["good"]

    def test_empty_line_skipped(self) -> None:
        """Empty and whitespace-only lines are silently skipped."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb("")
        cb("   ")
        cb("\t\n")
        flush()

        assert received == []


class TestMissingResultFallback:
    """When result event is absent, get_result falls back to accumulated text_delta."""

    def test_no_events_returns_none(self) -> None:
        """With no events processed, get_result() returns None."""
        provider = _make_provider()
        _, get_result, _ = provider._make_stream_callback(lambda _: None)

        assert get_result() is None

    def test_fallback_to_text_delta_accumulation(self) -> None:
        """Accumulated text_delta fragments are returned when result event is absent."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

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
        cb, get_result, flush = provider._make_stream_callback(received.append)

        line = json.dumps({"type": "system", "subtype": "init", "session_id": "abc"})
        cb(line)
        flush()

        assert received == []
        assert get_result() is None

    def test_message_start_ignored(self) -> None:
        """message_start events are silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        line = json.dumps({"type": "message_start", "message": {}})
        cb(line)
        flush()

        assert received == []

    def test_message_stop_ignored(self) -> None:
        """message_stop events are silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(json.dumps({"type": "message_stop"}))
        flush()

        assert received == []

    def test_content_block_stop_with_empty_buffer(self) -> None:
        """content_block_stop with no buffered content produces no output."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_content_block_stop_line())

        assert received == []

    def test_unknown_event_type_ignored(self) -> None:
        """Completely unknown event types are silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(json.dumps({"type": "some_future_event", "data": "stuff"}))
        flush()

        assert received == []


# ---------------------------------------------------------------------------
# Line buffering behavior
# ---------------------------------------------------------------------------


class TestLineBuffering:
    """Fragments are buffered and emitted as complete lines."""

    def test_fragments_combined_on_content_block_stop(self) -> None:
        """Multiple fragments without newlines combine into one line on content_block_stop."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line("Hello"))
        cb(_build_text_delta_line(" world"))
        cb(_build_text_delta_line("!"))
        assert received == []  # nothing emitted yet

        cb(_build_content_block_stop_line())
        assert received == ["Hello world!"]

    def test_newlines_split_into_separate_callbacks(self) -> None:
        """Fragments containing newlines emit complete lines immediately."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line("line one\nline two\nline three"))
        flush()

        assert received == ["line one", "line two", "line three"]

    def test_newline_mid_stream_emits_partial(self) -> None:
        """A newline in the middle of streaming emits the first part, buffers the rest."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line("first part"))
        cb(_build_text_delta_line(" end\nsecond"))
        assert received == ["first part end"]

        cb(_build_text_delta_line(" part"))
        flush()
        assert received == ["first part end", "second part"]

    def test_thinking_fragments_combined(self) -> None:
        """Thinking fragments are combined with [thinking] prefix on flush."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_thinking_delta_line("The"))
        cb(_build_thinking_delta_line(" user wants"))
        cb(_build_thinking_delta_line(" to update docs"))
        flush()

        assert received == ["[thinking] The user wants to update docs"]

    def test_thinking_to_text_transition_flushes(self) -> None:
        """Switching from thinking to text flushes the thinking buffer first."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_thinking_delta_line("reasoning"))
        assert received == []

        cb(_build_text_delta_line("visible output"))
        # Thinking should have been flushed by the type transition
        assert received == ["[thinking] reasoning"]

        flush()
        assert received == ["[thinking] reasoning", "visible output"]

    def test_text_to_thinking_transition_flushes(self) -> None:
        """Switching from text to thinking flushes the text buffer first."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line("some text"))
        cb(_build_thinking_delta_line("now thinking"))
        assert received == ["some text"]

        flush()
        assert received == ["some text", "[thinking] now thinking"]

    def test_tool_use_flushes_buffer(self) -> None:
        """tool_use content_block_start flushes any buffered text first; tool line comes on stop."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line("before tool"))
        cb(_build_tool_use_start_line("Read"))
        assert received == ["before tool"]

        cb(_build_content_block_stop_line())
        assert received == ["before tool", "[tool: Read]"]

    def test_result_flushes_buffer(self) -> None:
        """result event flushes any remaining buffered text."""
        received: list[str] = []
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line("trailing text"))
        cb(_build_result_line("final"))

        assert received == ["trailing text"]
        assert get_result() == "trailing text"

    def test_flush_is_idempotent(self) -> None:
        """Calling flush() multiple times does not duplicate output."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_build_text_delta_line("hello"))
        flush()
        flush()
        flush()

        assert received == ["hello"]


# ---------------------------------------------------------------------------
# stream_event envelope unwrapping (Claude CLI wraps API events)
# ---------------------------------------------------------------------------


class TestStreamEventEnvelope:
    """Claude CLI wraps API-style events in {"type":"stream_event","event":{...}}.

    The parser must unwrap this envelope to extract the inner event.
    """

    def test_text_delta_in_envelope(self) -> None:
        """text_delta inside stream_event envelope is dispatched correctly."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_wrap_in_stream_event(_build_text_delta_line("Hello")))
        flush()

        assert received == ["Hello"]

    def test_thinking_delta_in_envelope(self) -> None:
        """thinking_delta inside stream_event envelope is dispatched correctly."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(_wrap_in_stream_event(_build_thinking_delta_line("reasoning...")))
        flush()

        assert received == ["[thinking] reasoning..."]

    def test_tool_use_in_envelope(self) -> None:
        """tool_use content_block_start inside stream_event envelope emits on stop."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_wrap_in_stream_event(_build_tool_use_start_line("Edit")))
        assert received == []

        cb(_wrap_in_stream_event(_build_content_block_stop_line()))
        assert received == ["[tool: Edit]"]

    def test_text_accumulated_from_envelope(self) -> None:
        """text_delta fragments from enveloped events accumulate for fallback."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_wrap_in_stream_event(_build_text_delta_line("foo")))
        cb(_wrap_in_stream_event(_build_text_delta_line("bar")))

        assert get_result() == "foobar"

    def test_content_block_stop_in_envelope_flushes(self) -> None:
        """content_block_stop inside stream_event envelope flushes buffer."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_wrap_in_stream_event(_build_text_delta_line("buffered")))
        assert received == []

        cb(_wrap_in_stream_event(_build_content_block_stop_line()))
        assert received == ["buffered"]

    def test_empty_inner_event_ignored(self) -> None:
        """stream_event with missing/empty inner event is silently ignored."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, flush = provider._make_stream_callback(received.append)

        cb(json.dumps({"type": "stream_event"}))
        cb(json.dumps({"type": "stream_event", "event": {}}))
        flush()

        assert received == []


class TestFormatToolInputSummary:
    """Unit tests for _format_tool_input_summary helper."""

    def test_command_key_returns_command_value(self) -> None:
        assert (
            _format_tool_input_summary("Bash", {"command": "ls /workspace"})
            == "ls /workspace"
        )

    def test_file_path_key_returns_file_path_value(self) -> None:
        assert (
            _format_tool_input_summary("Read", {"file_path": "/tmp/test.txt"})
            == "/tmp/test.txt"
        )

    def test_description_key_returns_description_value(self) -> None:
        assert (
            _format_tool_input_summary("Edit", {"description": "update config"})
            == "update config"
        )

    def test_query_key_returns_query_value(self) -> None:
        assert (
            _format_tool_input_summary("WebSearch", {"query": "python教程"})
            == "python教程"
        )

    def test_pattern_key_returns_pattern_value(self) -> None:
        assert _format_tool_input_summary("Grep", {"pattern": "def foo"}) == "def foo"

    def test_url_key_returns_url_value(self) -> None:
        assert (
            _format_tool_input_summary("WebFetch", {"url": "https://example.com"})
            == "https://example.com"
        )

    def test_skill_key_returns_skill_value(self) -> None:
        assert (
            _format_tool_input_summary("UseSkill", {"skill": "my-skill"}) == "my-skill"
        )

    def test_prompt_key_returns_prompt_value(self) -> None:
        assert (
            _format_tool_input_summary("Agent", {"prompt": "do something"})
            == "do something"
        )

    def test_priority_order_command_first(self) -> None:
        input_json = {"command": "ls", "file_path": "/tmp", "description": "desc"}
        assert _format_tool_input_summary("Bash", input_json) == "ls"

    def test_priority_order_file_path_second(self) -> None:
        input_json = {"file_path": "/tmp", "description": "desc"}
        assert _format_tool_input_summary("Read", input_json) == "/tmp"

    def test_truncation_at_120_chars(self) -> None:
        long_value = "x" * 200
        result = _format_tool_input_summary("Bash", {"command": long_value})
        assert len(result) == 121
        assert result.endswith("\u2026")
        assert result == "x" * 120 + "\u2026"

    def test_120_chars_no_truncation(self) -> None:
        value_120 = "x" * 120
        result = _format_tool_input_summary("Bash", {"command": value_120})
        assert result == value_120
        assert not result.endswith("\u2026")

    def test_empty_dict_returns_empty_string(self) -> None:
        assert _format_tool_input_summary("Bash", {}) == ""

    def test_non_string_values_skipped(self) -> None:
        assert _format_tool_input_summary("Bash", {"command": 123}) == ""

    def test_empty_string_value_skipped(self) -> None:
        assert _format_tool_input_summary("Bash", {"command": ""}) == ""


class TestInputJsonDeltaAccumulation:
    """Tests for input_json_delta accumulation and formatted tool summary on content_block_stop."""

    def test_full_cycle_with_command_summary(self) -> None:
        """Full tool use cycle produces [Bash] ls /workspace on stop."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_input_json_delta_line('{"command": "ls /workspace"}'))
        cb(_build_content_block_stop_line())

        assert received == ["[Bash] ls /workspace"]

    def test_full_cycle_with_file_path_summary(self) -> None:
        """Full tool use cycle with file_path produces [Read] /path/to/file on stop."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Read"))
        cb(_build_input_json_delta_line('{"file_path": "/path/to/file.txt"}'))
        cb(_build_content_block_stop_line())

        assert received == ["[Read] /path/to/file.txt"]

    def test_missing_input_json_falls_back_to_tool_format(self) -> None:
        """No input_json_delta produces [tool: Bash] fallback on stop."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_content_block_stop_line())

        assert received == ["[tool: Bash]"]

    def test_malformed_json_falls_back_to_tool_format(self) -> None:
        """Malformed input_json produces [tool: Bash] fallback on stop."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_input_json_delta_line('{"command": "ls'))
        cb(_build_content_block_stop_line())

        assert received == ["[tool: Bash]"]

    def test_multiple_input_json_deltas_accumulated(self) -> None:
        """Multiple input_json_delta fragments are joined before parsing."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_input_json_delta_line('{"command": "ls '))
        cb(_build_input_json_delta_line('/workspace"}'))
        cb(_build_content_block_stop_line())

        assert received == ["[Bash] ls /workspace"]

    def test_input_json_not_in_result(self) -> None:
        """input_json_delta content is NOT included in get_result() text_parts."""
        provider = _make_provider()
        cb, get_result, _ = provider._make_stream_callback(lambda _: None)

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_input_json_delta_line('{"command": "ls"}'))
        cb(_build_text_delta_line("final output"))
        cb(_build_content_block_stop_line())
        cb(_build_result_line("result event"))

        assert get_result() == "final output"

    def test_non_dict_json_array_falls_back_to_tool_format(self) -> None:
        """Valid JSON that is not a dict (e.g. []) falls back to [tool: Bash]."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_input_json_delta_line("[]"))
        cb(_build_content_block_stop_line())

        assert received == ["[tool: Bash]"]

    def test_non_dict_json_string_falls_back_to_tool_format(self) -> None:
        """Valid JSON that is a string falls back to [tool: Bash]."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_input_json_delta_line('"just a string"'))
        cb(_build_content_block_stop_line())

        assert received == ["[tool: Bash]"]

    def test_input_json_with_no_useful_keys_falls_back(self) -> None:
        """input_json with no useful keys falls back to [tool: X] format."""
        received: list[str] = []
        provider = _make_provider()
        cb, _, _ = provider._make_stream_callback(received.append)

        cb(_build_tool_use_start_line("Bash"))
        cb(_build_input_json_delta_line('{"other_key": "value"}'))
        cb(_build_content_block_stop_line())

        assert received == ["[tool: Bash]"]

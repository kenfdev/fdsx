import json
import logging
import subprocess
import threading
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderBase,
    ProviderResult,
    _run_subprocess,
    add_schema_update_guidance,
    serialize_output_schema,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stream-JSON format constants
# ---------------------------------------------------------------------------

# CLI flags added to enable stream-json output when output_callback is provided
_STREAM_FORMAT_FLAGS = [
    "--output-format",
    "stream-json",
    "--verbose",
    "--include-partial-messages",
]

# NDJSON event type strings
_EVENT_CONTENT_BLOCK_START = "content_block_start"
_EVENT_CONTENT_BLOCK_DELTA = "content_block_delta"
_EVENT_CONTENT_BLOCK_STOP = "content_block_stop"
_EVENT_RESULT = "result"

# Delta type strings within content_block_delta events
_DELTA_TYPE_TEXT = "text_delta"
_DELTA_TYPE_THINKING = "thinking_delta"
_DELTA_TYPE_INPUT_JSON = "input_json_delta"

# Content block type for tool use
_CONTENT_TYPE_TOOL_USE = "tool_use"

_SUMMARY_KEY_PRIORITY = [
    "command",
    "file_path",
    "description",
    "query",
    "pattern",
    "url",
    "skill",
    "prompt",
]
_SUMMARY_MAX_LENGTH = 120


def _format_tool_input_summary(tool_name: str, input_json: dict[str, object]) -> str:
    for key in _SUMMARY_KEY_PRIORITY:
        value = input_json.get(key)
        if isinstance(value, str) and value:
            if len(value) > _SUMMARY_MAX_LENGTH:
                return value[:_SUMMARY_MAX_LENGTH] + "\u2026"
            return value
    return ""


class ClaudeOptions(BaseModel):
    """Options for the Claude CLI provider."""

    model_config = ConfigDict(extra="forbid")

    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    permission_mode: (
        Literal[
            "default", "acceptEdits", "bypassPermissions", "dontAsk", "plan", "auto"
        ]
        | None
    ) = None
    dangerously_skip_permissions: bool = False
    allowed_tools: list[str] = []
    disallowed_tools: list[str] = []
    inactivity_timeout: int | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None

    def to_cli_flags(self) -> list[str]:
        """Translate options to Claude CLI flags."""
        flags: list[str] = []
        if self.effort is not None:
            flags.extend(["--effort", self.effort])
        if self.permission_mode is not None:
            flags.extend(["--permission-mode", self.permission_mode])
        if self.dangerously_skip_permissions:
            flags.append("--dangerously-skip-permissions")
        for tool in self.allowed_tools:
            flags.extend(["--allowedTools", tool])
        for tool in self.disallowed_tools:
            flags.extend(["--disallowedTools", tool])
        if self.system_prompt is not None:
            flags.extend(["--system-prompt", self.system_prompt])
        if self.append_system_prompt is not None:
            flags.extend(["--append-system-prompt", self.append_system_prompt])
        return flags


class ClaudeProvider(ProviderBase):
    """Claude provider - executes Claude CLI."""

    def __init__(self, options: ClaudeOptions | None = None) -> None:
        self.options: ClaudeOptions = (
            options if options is not None else ClaudeOptions()
        )

    def _make_stream_callback(
        self,
        output_callback: Callable[[str], None],
        completion_event: threading.Event | None = None,
        summary_callback: Callable[[str], None] | None = None,
        on_tool_start: Callable[[], None] | None = None,
        on_tool_end: Callable[[], None] | None = None,
        final_message_callback: Callable[[str], None] | None = None,
    ) -> tuple[Callable[[str], None], Callable[[], str | None], Callable[[], None]]:
        """Create a streaming callback that parses stream-json NDJSON lines.

        Wraps ``output_callback`` so that human-readable text extracted from
        Claude's ``stream-json`` NDJSON events is forwarded to the caller while
        the raw JSON lines are silently consumed.  Fragments are buffered and
        emitted as complete lines (on newline boundaries or content block end).

        Returns a ``(stream_callback, get_result, flush)`` tuple:
        - ``stream_callback``: parses each JSON line and dispatches text to
          ``output_callback``. Malformed JSON lines are skipped with a warning
          logged via ``logger.warning``.
        - ``get_result``: returns the final stdout string after streaming is
          complete. Uses the ``result`` event's ``result`` field when available,
          falling back to concatenated ``text_delta`` content on crash/missing
          result.
        - ``flush``: emits any remaining buffered text. Call after streaming ends.
        - ``final_message_callback``: receives the result event's final text block.
        """
        text_parts: list[str] = []
        # Single-element list so the inner closure can rebind the value.
        final_result: list[str | None] = [None]

        # Line buffer: accumulates text/thinking fragments, emits on '\n' or flush.
        _buffer: list[str] = []
        # Tracks current buffer content type: "text", "thinking", or None.
        _buffer_type: list[str | None] = [None]
        # Tracks whether we are currently inside a tool_use block.
        _in_tool_use: list[bool] = [False]
        _tool_name: list[str | None] = [None]
        _tool_input_parts: list[str] = []

        def _flush_buffer() -> None:
            """Emit buffered content as complete lines via output_callback."""
            if not _buffer:
                return
            joined = "".join(_buffer)
            _buffer.clear()
            buf_type = _buffer_type[0]
            _buffer_type[0] = None
            if not joined:
                return
            # Split on newlines; emit each complete line.
            lines = joined.split("\n")
            cb = summary_callback if summary_callback else output_callback
            for line in lines:
                if not line:
                    continue
                if buf_type == "thinking":
                    cb(f"[thinking] {line}")
                else:
                    output_callback(line)

        def _append_and_emit(fragment: str, buf_type: str) -> None:
            """Append a fragment to the buffer, flushing type transitions and newlines."""
            # Type transition → flush previous buffer first.
            if _buffer_type[0] is not None and _buffer_type[0] != buf_type:
                _flush_buffer()
            _buffer_type[0] = buf_type
            _buffer.append(fragment)
            # If the fragment contains newlines, flush complete lines now.
            if "\n" in fragment:
                joined = "".join(_buffer)
                parts = joined.split("\n")
                # Last element is the incomplete trailing portion — keep in buffer.
                _buffer.clear()
                _buffer.append(parts[-1])
                for line in parts[:-1]:
                    if not line:
                        continue
                    cb = summary_callback if summary_callback else output_callback
                    if buf_type == "thinking":
                        cb(f"[thinking] {line}")
                    else:
                        output_callback(line)

        def stream_callback(line: str) -> None:
            if not line.strip():
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Malformed JSON line skipped: %s", line)
                return

            # Unwrap stream_event envelope from Claude CLI stream-json format
            if event.get("type") == "stream_event":
                event = event.get("event", {})

            event_type = event.get("type")

            if event_type == _EVENT_CONTENT_BLOCK_START:
                content_block = event.get("content_block", {})
                if content_block.get("type") == _CONTENT_TYPE_TOOL_USE:
                    _in_tool_use[0] = True
                    _tool_name[0] = content_block.get("name", "unknown")
                    _tool_input_parts.clear()
                    if on_tool_start is not None:
                        on_tool_start()
                    _flush_buffer()

            elif event_type == _EVENT_CONTENT_BLOCK_DELTA:
                delta = event.get("delta", {})
                delta_type = delta.get("type")
                if delta_type == _DELTA_TYPE_TEXT:
                    text = delta.get("text", "")
                    if text:
                        text_parts.append(text)
                        _append_and_emit(text, "text")
                elif delta_type == _DELTA_TYPE_THINKING:
                    thinking = delta.get("thinking", "")
                    if thinking:
                        _append_and_emit(thinking, "thinking")
                elif delta_type == _DELTA_TYPE_INPUT_JSON:
                    partial = delta.get("partial_json", "")
                    if partial:
                        _tool_input_parts.append(partial)

            elif event_type == _EVENT_CONTENT_BLOCK_STOP:
                if _in_tool_use[0]:
                    tool_name = _tool_name[0] or "unknown"
                    cb = summary_callback if summary_callback else output_callback
                    joined_input = "".join(_tool_input_parts)
                    if joined_input:
                        try:
                            parsed = json.loads(joined_input)
                            if not isinstance(parsed, dict):
                                cb(f"[tool: {tool_name}]")
                            else:
                                summary = _format_tool_input_summary(tool_name, parsed)
                                if summary:
                                    cb(f"[{tool_name}] {summary}")
                                else:
                                    cb(f"[tool: {tool_name}]")
                        except json.JSONDecodeError:
                            cb(f"[tool: {tool_name}]")
                    else:
                        cb(f"[tool: {tool_name}]")
                    _tool_name[0] = None
                    _tool_input_parts.clear()
                    _in_tool_use[0] = False
                    if on_tool_end is not None:
                        on_tool_end()
                _flush_buffer()

            elif event_type == _EVENT_RESULT:
                _flush_buffer()
                structured_result = event.get("structured_output")
                if structured_result is not None:
                    final_result[0] = json.dumps(structured_result)
                else:
                    final_result[0] = event.get("result", "")
                if final_message_callback is not None:
                    final_message_callback(final_result[0] or "")
                if completion_event is not None:
                    completion_event.set()

        def flush() -> None:
            """Flush any remaining buffered text after streaming ends."""
            _flush_buffer()

        def get_result() -> str | None:
            # Prefer accumulated text_delta content because the result event's
            # "result" field only contains the *last* text block.  In agentic
            # responses where text → tool_use → text, earlier text blocks
            # (which may contain routing tags like [STEP:1]) are missing from
            # the result field but present in text_parts.
            if text_parts:
                return "".join(text_parts)
            result_text = final_result[0]
            if result_text is not None:
                return result_text
            return None

        return stream_callback, get_result, flush

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        command: str | None = None,
        output_callback: Callable[[str], None] | None = None,
        stderr_callback: Callable[[str], None] | None = None,
        on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
        summary_callback: Callable[[str], None] | None = None,
        output_schema: Any | None = None,
    ) -> ProviderResult:
        """Execute Claude CLI with a prompt.

        Args:
            prompt: The prompt to send to Claude
            model: Model name (e.g., opus, sonnet)
            timeout: Timeout in seconds
            command: Ignored for claude provider
            output_callback: Optional callback for streaming stdout lines.
                When provided, ``--output-format stream-json --verbose
                --include-partial-messages`` is appended to the CLI invocation
                and ``ProviderResult.stdout`` is populated from the ``result``
                event (falling back to concatenated ``text_delta`` content).
                ``ProviderResult.final_message`` contains the result event's
                final text block.
            stderr_callback: Optional callback for streaming stderr lines
            on_process_start: Optional callback invoked after Popen creation
            summary_callback: Optional callback for summary lines ([tool: X],
                [thinking] ...) that should be visible even in quiet mode.

        Returns:
            ProviderResult with exit code and output
        """
        use_stdin = len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD
        if use_stdin:
            args = ["claude", "-p", "-"]
            stdin_data: str | None = prompt
        else:
            args = ["claude", "-p", prompt]
            stdin_data = None
        if model:
            args.extend(["--model", model])
        args.extend(self.options.to_cli_flags())
        if output_schema is not None:
            args.extend(["--json-schema", serialize_output_schema(output_schema)])

        effective_inactivity = (
            self.options.inactivity_timeout
            if self.options.inactivity_timeout is not None
            else DEFAULT_INACTIVITY_TIMEOUT
        )
        effective_timeout = (
            timeout if timeout is not None else DEFAULT_EXECUTION_TIMEOUT
        )

        if output_callback is not None:
            args.extend(_STREAM_FORMAT_FLAGS)
            completion_event = threading.Event()
            suspend_fn: list[Callable[[], None] | None] = [None]
            resume_fn: list[Callable[[], None] | None] = [None]
            final_message: list[str | None] = [None]

            def capture_final_message(message: str) -> None:
                final_message[0] = message

            def on_inactivity_hooks(
                suspend: Callable[[], None], resume: Callable[[], None]
            ) -> None:
                suspend_fn[0] = suspend
                resume_fn[0] = resume

            stream_callback, get_result, flush = self._make_stream_callback(
                output_callback,
                completion_event,
                summary_callback,
                on_tool_start=lambda: (
                    suspend_fn[0]() if suspend_fn[0] is not None else None
                ),
                on_tool_end=lambda: (
                    resume_fn[0]() if resume_fn[0] is not None else None
                ),
                final_message_callback=capture_final_message,
            )
            result = _run_subprocess(
                args=args,
                timeout=effective_timeout,
                output_callback=stream_callback,
                stderr_callback=stderr_callback,
                stdin_data=stdin_data,
                completion_event=completion_event,
                inactivity_timeout=effective_inactivity,
                on_process_start=on_process_start,
                on_inactivity_hooks=on_inactivity_hooks,
            )
            if output_schema is not None:
                result = add_schema_update_guidance(
                    result, provider_name="Claude", schema_flag="--json-schema"
                )
            flush()
            parsed_stdout = get_result()
            if parsed_stdout is not None:
                return ProviderResult(
                    exit_code=result.exit_code,
                    stdout=parsed_stdout,
                    stderr=result.stderr,
                    final_message=final_message[0],
                )
            return result

        result = _run_subprocess(
            args=args,
            timeout=effective_timeout,
            output_callback=output_callback,
            stderr_callback=stderr_callback,
            stdin_data=stdin_data,
            inactivity_timeout=effective_inactivity,
            on_process_start=on_process_start,
        )
        if output_schema is not None:
            result = add_schema_update_guidance(
                result, provider_name="Claude", schema_flag="--json-schema"
            )
        return result

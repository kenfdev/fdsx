import json
import logging
import subprocess
import threading
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderBase,
    ProviderResult,
    _run_subprocess,
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

# Content block type for tool use
_CONTENT_TYPE_TOOL_USE = "tool_use"


class ClaudeOptions(BaseModel):
    """Options for the Claude CLI provider."""

    model_config = ConfigDict(extra="forbid")

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

    def to_cli_flags(self) -> list[str]:
        """Translate options to Claude CLI flags."""
        flags: list[str] = []
        if self.permission_mode is not None:
            flags.extend(["--permission-mode", self.permission_mode])
        if self.dangerously_skip_permissions:
            flags.append("--dangerously-skip-permissions")
        for tool in self.allowed_tools:
            flags.extend(["--allowedTools", tool])
        for tool in self.disallowed_tools:
            flags.extend(["--disallowedTools", tool])
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
        """
        text_parts: list[str] = []
        # Single-element list so the inner closure can rebind the value.
        final_result: list[str | None] = [None]

        # Line buffer: accumulates text/thinking fragments, emits on '\n' or flush.
        _buffer: list[str] = []
        # Tracks current buffer content type: "text", "thinking", or None.
        _buffer_type: list[str | None] = [None]

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
                    _flush_buffer()
                    tool_name = content_block.get("name", "unknown")
                    cb = summary_callback if summary_callback else output_callback
                    cb(f"[tool: {tool_name}]")

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

            elif event_type == _EVENT_CONTENT_BLOCK_STOP:
                _flush_buffer()

            elif event_type == _EVENT_RESULT:
                _flush_buffer()
                final_result[0] = event.get("result", "")
                if completion_event is not None:
                    completion_event.set()

        def flush() -> None:
            """Flush any remaining buffered text after streaming ends."""
            _flush_buffer()

        def get_result() -> str | None:
            result_text = final_result[0]
            if result_text is not None:
                return result_text
            # Fallback: reconstruct from accumulated text_delta content
            if text_parts:
                return "".join(text_parts)
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

        effective_inactivity = (
            self.options.inactivity_timeout
            if self.options.inactivity_timeout is not None
            else DEFAULT_INACTIVITY_TIMEOUT
        )

        if output_callback is not None:
            args.extend(_STREAM_FORMAT_FLAGS)
            completion_event = threading.Event()
            stream_callback, get_result, flush = self._make_stream_callback(
                output_callback, completion_event, summary_callback
            )
            result = _run_subprocess(
                args=args,
                timeout=timeout,
                output_callback=stream_callback,
                stderr_callback=stderr_callback,
                stdin_data=stdin_data,
                completion_event=completion_event,
                inactivity_timeout=effective_inactivity,
                on_process_start=on_process_start,
            )
            flush()
            parsed_stdout = get_result()
            if parsed_stdout is not None:
                return ProviderResult(
                    exit_code=result.exit_code,
                    stdout=parsed_stdout,
                    stderr=result.stderr,
                )
            return result

        return _run_subprocess(
            args=args,
            timeout=timeout,
            output_callback=output_callback,
            stderr_callback=stderr_callback,
            stdin_data=stdin_data,
            inactivity_timeout=effective_inactivity,
            on_process_start=on_process_start,
        )

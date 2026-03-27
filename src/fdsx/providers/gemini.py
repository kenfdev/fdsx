import json
import logging
import subprocess
import threading
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderBase,
    ProviderResult,
    _run_subprocess,
)

logger = logging.getLogger(__name__)


class GeminiOptions(BaseModel):
    """Options for the Gemini CLI provider."""

    model_config = ConfigDict(extra="forbid")

    approval_mode: Literal["default", "auto_edit", "yolo", "plan"] | None = None
    yolo: bool = False
    sandbox: bool = False
    include_directories: list[str] = []
    extensions: list[str] = []
    policy: list[str] = []
    inactivity_timeout: int | None = None

    def to_cli_flags(self) -> list[str]:
        """Translate options to Gemini CLI flags."""
        flags: list[str] = []
        if self.yolo:
            flags.append("--yolo")
        elif self.approval_mode is not None:
            flags.extend(["--approval-mode", self.approval_mode])
        if self.sandbox:
            flags.append("--sandbox")
        if self.include_directories:
            flags.extend(["--include-directories", ",".join(self.include_directories)])
        if self.extensions:
            flags.extend(["--extensions", ",".join(self.extensions)])
        for p in self.policy:
            flags.extend(["--policy", p])
        return flags


class GeminiProvider(ProviderBase):
    """Gemini provider - executes Gemini CLI."""

    def __init__(self, options: GeminiOptions | None = None) -> None:
        self.options: GeminiOptions = (
            options if options is not None else GeminiOptions()
        )

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
        """Execute Gemini CLI with a prompt.

        Args:
            prompt: The prompt to send to Gemini
            model: Model name
            timeout: Timeout in seconds
            command: Ignored for gemini provider
            output_callback: Optional callback for streaming stdout lines.
            stderr_callback: Optional callback for streaming stderr lines.
            on_process_start: Optional callback invoked after Popen creation.
            summary_callback: Optional callback for summary lines (ignored for Gemini).

        Returns:
            ProviderResult with exit code and output
        """
        use_stdin = len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD
        args = ["gemini", "-p"]
        if use_stdin:
            args.append("-")
            stdin_data: str | None = prompt
        else:
            args.append(prompt)
            stdin_data = None
        if model:
            args.extend(["--model", model])
        args.extend(self.options.to_cli_flags())

        effective_inactivity = (
            self.options.inactivity_timeout
            if self.options.inactivity_timeout is not None
            else DEFAULT_INACTIVITY_TIMEOUT
        )
        effective_timeout = (
            timeout if timeout is not None else DEFAULT_EXECUTION_TIMEOUT
        )

        if output_callback is not None:
            args.extend(["--output-format", "stream-json"])
            completion_event = threading.Event()
            suspend_fn: list[Callable[[], None] | None] = [None]
            resume_fn: list[Callable[[], None] | None] = [None]

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
            timeout=effective_timeout,
            output_callback=output_callback,
            stderr_callback=stderr_callback,
            stdin_data=stdin_data,
            inactivity_timeout=effective_inactivity,
            on_process_start=on_process_start,
        )

    def _make_stream_callback(
        self,
        output_callback: Callable[[str], None],
        completion_event: threading.Event | None = None,
        summary_callback: Callable[[str], None] | None = None,
        on_tool_start: Callable[[], None] | None = None,
        on_tool_end: Callable[[], None] | None = None,
    ) -> tuple[Callable[[str], None], Callable[[], str | None], Callable[[], None]]:
        """Create a streaming callback that parses stream-json NDJSON lines.

        Wraps ``output_callback`` so that human-readable text extracted from
        Gemini's ``stream-json`` NDJSON events is forwarded to the caller while
        the raw JSON lines are silently consumed. Fragments are buffered and
        emitted as complete lines (on newline boundaries or content block end).

        Returns a ``(stream_callback, get_result, flush)`` tuple:
        - ``stream_callback``: parses each JSON line and dispatches text to
          ``output_callback``. Malformed JSON lines are skipped with a warning
          logged via ``logger.warning``.
        - ``get_result``: returns the final stdout string reconstructed from
          accumulated assistant message deltas. Gemini's ``result`` event does
          NOT contain response text.
        - ``flush``: emits any remaining buffered text. Call after streaming ends.
        """
        text_parts: list[str] = []

        _buffer: list[str] = []
        _buffer_type: list[str | None] = [None]

        def _flush_buffer() -> None:
            if not _buffer:
                return
            joined = "".join(_buffer)
            _buffer.clear()
            _buffer_type[0] = None
            if not joined:
                return
            lines = joined.split("\n")
            for line in lines:
                if not line:
                    continue
                output_callback(line)

        def _append_and_emit(fragment: str, buf_type: str) -> None:
            if _buffer_type[0] is not None and _buffer_type[0] != buf_type:
                _flush_buffer()
            _buffer_type[0] = buf_type
            _buffer.append(fragment)
            if "\n" in fragment:
                joined = "".join(_buffer)
                parts = joined.split("\n")
                _buffer.clear()
                _buffer.append(parts[-1])
                for line in parts[:-1]:
                    if not line:
                        continue
                    output_callback(line)

        def stream_callback(line: str) -> None:
            if not line.strip():
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Malformed JSON line skipped: %s", line)
                return

            event_type = event.get("type")

            if event_type == "init":
                session_id = event.get("session_id", "unknown")
                model = event.get("model", "unknown")
                logger.debug(
                    "Gemini session init: session_id=%s model=%s", session_id, model
                )

            elif event_type == "message":
                role = event.get("role")
                if role == "user":
                    return
                if role == "assistant" and event.get("delta"):
                    content = event.get("content", "")
                    if content:
                        text_parts.append(content)
                        _append_and_emit(content, "text")

            elif event_type == "tool_use":
                _flush_buffer()
                tool_name = event.get("tool_name", "unknown")
                cb = summary_callback if summary_callback else output_callback
                cb(f"[tool: {tool_name}]")
                if on_tool_start is not None:
                    on_tool_start()

            elif event_type == "tool_result":
                if on_tool_end is not None:
                    on_tool_end()

            elif event_type == "error":
                _flush_buffer()
                message = event.get("message", "")
                cb = summary_callback if summary_callback else output_callback
                cb(message)

            elif event_type == "result":
                _flush_buffer()
                if completion_event is not None:
                    completion_event.set()

        def flush() -> None:
            _flush_buffer()

        def get_result() -> str | None:
            if text_parts:
                return "".join(text_parts)
            return None

        return stream_callback, get_result, flush

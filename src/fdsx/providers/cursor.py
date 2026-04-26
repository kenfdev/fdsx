import json
import logging
import shutil
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


class CursorProviderError(Exception):
    """Raised when the Cursor provider encounters a domain-level error."""


class CursorOptions(BaseModel):
    """Options for the Cursor CLI provider."""

    model_config = ConfigDict(extra="forbid")

    force: bool = False
    sandbox: Literal["enabled", "disabled"] | None = None
    approve_mcps: bool = False
    inactivity_timeout: int | None = None

    def to_cli_flags(self) -> list[str]:
        """Translate options to Cursor CLI flags."""
        flags: list[str] = []
        if self.force:
            flags.append("--force")
        if self.sandbox is not None:
            flags.extend(["--sandbox", self.sandbox])
        if self.approve_mcps:
            flags.append("--approve-mcps")
        return flags


class CursorProvider(ProviderBase):
    """Cursor provider - executes Cursor agent CLI."""

    def __init__(self, options: CursorOptions | None = None) -> None:
        self.options: CursorOptions = (
            options if options is not None else CursorOptions()
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
        """Execute Cursor agent CLI with a prompt.

        Args:
            prompt: The prompt to send to Cursor agent
            model: Model name
            timeout: Timeout in seconds
            command: Ignored for cursor provider
            output_callback: Optional callback for streaming stdout lines.
            stderr_callback: Optional callback for streaming stderr lines.
            on_process_start: Optional callback invoked after Popen creation.
            summary_callback: Ignored for cursor provider.

        Returns:
            ProviderResult with exit code and output

        Raises:
            CursorProviderError: If the 'agent' binary is not found on PATH.
        """
        if shutil.which("agent") is None:
            raise CursorProviderError(
                "Cursor 'agent' binary not found on PATH. "
                "Ensure Cursor is installed and 'agent' is available."
            )

        use_stdin = len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD
        if use_stdin:
            prompt_arg = "-"
            stdin_data: str | None = prompt
        else:
            prompt_arg = prompt
            stdin_data = None

        args: list[str] = ["agent", "-p", prompt_arg, "--trust"]

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
            args.extend(["--output-format", "stream-json", "--stream-partial-output"])
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
                max_suspend_duration=effective_inactivity,
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
        """Create a streaming callback that parses Cursor stream-json NDJSON lines.

        Returns a ``(stream_callback, get_result, flush)`` tuple:
        - ``stream_callback``: parses each JSON line and dispatches text to
          ``output_callback``. Malformed JSON lines are skipped with a warning.
        - ``get_result``: returns the final stdout string reconstructed from
          accumulated assistant message text parts, or None if none accumulated.
        - ``flush``: emits any remaining buffered text. Call after streaming ends.
        """
        text_parts: list[str] = []
        _non_text_part_seen: list[bool] = [False]
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
            event_subtype = event.get("subtype")

            if event_type == "system" and event_subtype == "init":
                model = event.get("model", "unknown")
                logger.debug("Cursor session init: model=%s", model)

            elif event_type == "assistant":
                if "model_call_id" in event:
                    return
                message = event.get("message", {})
                content_list = message.get("content", [])
                for part in content_list:
                    part_type = part.get("type")
                    if part_type == "text":
                        text = part.get("text", "")
                        text_parts.append(text)
                        if text:
                            _append_and_emit(text, "text")
                    elif part_type == "thinking":
                        _flush_buffer()
                        thinking_text = part.get("thinking", "")
                        cb = (
                            summary_callback
                            if summary_callback is not None
                            else output_callback
                        )
                        cb(f"[thinking] {thinking_text}")
                    else:
                        if not _non_text_part_seen[0]:
                            logger.debug(
                                "Non-text content part skipped: type=%s", part_type
                            )
                            _non_text_part_seen[0] = True

            elif event_type == "tool_call":
                if event_subtype == "started":
                    _flush_buffer()
                    tool_key = event.get("toolKey", "unknown")
                    cb = (
                        summary_callback
                        if summary_callback is not None
                        else output_callback
                    )
                    cb(f"[tool: {tool_key}]")
                    if on_tool_start is not None:
                        on_tool_start()
                elif event_subtype == "completed":
                    if on_tool_end is not None:
                        on_tool_end()

            elif event_type == "result":
                _flush_buffer()
                if completion_event is not None:
                    completion_event.set()

            else:
                logger.debug("Unknown Cursor event type=%s, skipping", event_type)

        def flush() -> None:
            _flush_buffer()

        def get_result() -> str | None:
            if text_parts:
                return "".join(text_parts)
            return None

        return stream_callback, get_result, flush

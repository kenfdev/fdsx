import json
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderBase,
    ProviderError,
    ProviderResult,
    _run_subprocess,
    add_schema_update_guidance,
    serialize_output_schema,
)

logger = structlog.get_logger(__name__)

NonEmptyString = Annotated[str, Field(min_length=1)]


class GrokProviderError(ProviderError):
    """Raised when Grok returns an invalid or incomplete provider response."""


class GrokOptions(BaseModel):
    """Options for the Grok Build CLI provider."""

    model_config = ConfigDict(extra="forbid")

    permission_mode: Literal[
        "default",
        "acceptEdits",
        "auto",
        "dontAsk",
        "bypassPermissions",
        "plan",
    ] = "dontAsk"
    sandbox: str | None = Field(default=None, min_length=1)
    allow: list[NonEmptyString] = []
    deny: list[NonEmptyString] = []
    tools: list[NonEmptyString] = []
    disallowed_tools: list[NonEmptyString] = []
    reasoning_effort: str | None = Field(default=None, min_length=1)
    max_turns: int | None = Field(default=None, gt=0)
    on_max_turns: Literal["fail", "return_partial"] = "fail"
    no_subagents: bool = True
    no_plan: bool = True
    cross_session_memory: Literal["off", "on", "inherit"] = "off"
    disable_web_search: bool = False
    verbatim: bool = True
    cwd: str | None = Field(default=None, min_length=1)
    agent: str | None = Field(default=None, min_length=1)
    agents: dict[str, Any] = {}
    rules: str | None = Field(default=None, min_length=1)
    system_prompt_override: str | None = Field(default=None, min_length=1)
    inactivity_timeout: int | None = None

    @field_validator("cross_session_memory", mode="before")
    @classmethod
    def normalize_yaml_memory_value(cls, value: object) -> object:
        if value is True:
            return "on"
        if value is False:
            return "off"
        return value

    @model_validator(mode="after")
    def validate_compatible_options(self) -> "GrokOptions":
        if self.rules is not None and self.system_prompt_override is not None:
            raise ValueError("rules and system_prompt_override are mutually exclusive")
        if self.agents and self.no_subagents:
            raise ValueError("agents requires no_subagents: false")
        try:
            json.dumps(self.agents)
        except (TypeError, ValueError) as exc:
            raise ValueError("agents must contain only JSON-compatible values") from exc
        return self

    def to_cli_flags(self) -> list[str]:
        """Translate configured behavior into Grok CLI flags."""
        flags = ["--permission-mode", self.permission_mode]
        if self.sandbox is not None:
            flags.extend(["--sandbox", self.sandbox])
        for rule in self.allow:
            flags.extend(["--allow", rule])
        for rule in self.deny:
            flags.extend(["--deny", rule])
        if self.tools:
            flags.extend(["--tools", ",".join(self.tools)])
        if self.disallowed_tools:
            flags.extend(["--disallowed-tools", ",".join(self.disallowed_tools)])
        if self.reasoning_effort is not None:
            flags.extend(["--reasoning-effort", self.reasoning_effort])
        if self.max_turns is not None:
            flags.extend(["--max-turns", str(self.max_turns)])
        if self.no_subagents:
            flags.append("--no-subagents")
        if self.no_plan:
            flags.append("--no-plan")
        if self.cross_session_memory == "off":
            flags.append("--no-memory")
        elif self.cross_session_memory == "on":
            flags.append("--experimental-memory")
        if self.disable_web_search:
            flags.append("--disable-web-search")
        if self.verbatim:
            flags.append("--verbatim")
        if self.cwd is not None:
            flags.extend(["--cwd", self.cwd])
        if self.agent is not None:
            flags.extend(["--agent", self.agent])
        if self.agents:
            try:
                encoded_agents = json.dumps(self.agents, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                logger.error("grok_agents_serialization_failed", error=str(exc))
                raise GrokProviderError(
                    "Grok agents must contain only JSON-compatible values"
                ) from exc
            flags.extend(["--agents", encoded_agents])
        if self.rules is not None:
            flags.extend(["--rules", self.rules])
        if self.system_prompt_override is not None:
            flags.extend(["--system-prompt-override", self.system_prompt_override])
        return flags


@dataclass(frozen=True)
class GrokStreamResult:
    """Normalized result of a Grok streaming JSON event sequence."""

    final_text: str
    structured_output: Any | None
    stop_reason: str | None
    ended: bool


class GrokStreamParser:
    """Parse Grok streaming JSON without mixing progress into task output."""

    def __init__(
        self,
        output_callback: Callable[[str], None] | None = None,
        summary_callback: Callable[[str], None] | None = None,
        on_tool_start: Callable[[], None] | None = None,
        on_tool_end: Callable[[], None] | None = None,
    ) -> None:
        self._output_callback = output_callback
        self._summary_callback = summary_callback or output_callback
        self._on_tool_start = on_tool_start
        self._on_tool_end = on_tool_end
        self._text_parts: list[str] = []
        self._buffer: list[str] = []
        self._buffer_type: Literal["text", "thought"] | None = None
        self._active_tool_ids: set[str] = set()
        self._structured_output: Any | None = None
        self._stop_reason: str | None = None
        self._ended = False

    def _flush_buffer(self) -> None:
        if not self._buffer:
            return
        value = "".join(self._buffer)
        buffer_type = self._buffer_type
        self._buffer.clear()
        self._buffer_type = None
        if not value:
            return
        callback = (
            self._summary_callback
            if buffer_type == "thought"
            else self._output_callback
        )
        if callback is None:
            return
        prefix = "[thinking] " if buffer_type == "thought" else ""
        for line in value.splitlines() or [value]:
            if line:
                callback(f"{prefix}{line}")

    def _append_progress(
        self, fragment: str, fragment_type: Literal["text", "thought"]
    ) -> None:
        if self._buffer_type is not None and self._buffer_type != fragment_type:
            self._flush_buffer()
        self._buffer_type = fragment_type
        self._buffer.append(fragment)
        if "\n" not in fragment:
            return
        joined = "".join(self._buffer)
        complete_lines = joined.split("\n")
        self._buffer.clear()
        self._buffer.append(complete_lines.pop())
        callback = (
            self._summary_callback
            if fragment_type == "thought"
            else self._output_callback
        )
        if callback is None:
            return
        prefix = "[thinking] " if fragment_type == "thought" else ""
        for line in complete_lines:
            if line:
                callback(f"{prefix}{line}")

    def _start_tool(self, update: dict[str, Any]) -> None:
        self._flush_buffer()
        tool_id = update.get("toolCallId") or update.get("id")
        if not isinstance(tool_id, str) or not tool_id:
            logger.error("grok_stream_invalid_tool_call", reason="missing id")
            raise GrokProviderError("Grok tool-call event requires an id")
        if tool_id not in self._active_tool_ids:
            was_idle = not self._active_tool_ids
            self._active_tool_ids.add(tool_id)
            self._text_parts.clear()
            if was_idle and self._on_tool_start is not None:
                self._on_tool_start()
        title = update.get("title") or update.get("name") or "unknown"
        if self._summary_callback is not None:
            self._summary_callback(f"[tool: {title}]")

    def _update_tool(self, update: dict[str, Any]) -> None:
        tool_id = update.get("toolCallId") or update.get("id")
        if not isinstance(tool_id, str) or not tool_id:
            logger.error("grok_stream_invalid_tool_update", reason="missing id")
            raise GrokProviderError("Grok tool-update event requires an id")
        raw_status = update.get("status")
        if not isinstance(raw_status, str) or not raw_status:
            logger.error("grok_stream_invalid_tool_update", reason="missing status")
            raise GrokProviderError("Grok tool-update event requires a status")
        status = raw_status.lower()
        if self._summary_callback is not None:
            self._summary_callback(f"[tool update: {tool_id} {status}]")
        if status not in {"completed", "failed", "cancelled", "canceled"}:
            return
        if tool_id in self._active_tool_ids:
            self._active_tool_ids.remove(tool_id)
            if not self._active_tool_ids and self._on_tool_end is not None:
                self._on_tool_end()

    def feed(self, line: str) -> None:
        """Consume one Grok NDJSON line."""
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.error("grok_stream_invalid_json", line=line)
            raise GrokProviderError("Grok returned malformed streaming JSON") from exc
        if not isinstance(event, dict):
            logger.error("grok_stream_invalid_event", event_type=type(event).__name__)
            raise GrokProviderError("Grok returned a non-object streaming event")

        event_type = event.get("type")
        if event_type == "text":
            data = event.get("data")
            if not isinstance(data, str):
                logger.error("grok_stream_invalid_text", reason="non-string data")
                raise GrokProviderError("Grok text event requires string data")
            self._text_parts.append(data)
            self._append_progress(data, "text")
            return
        if event_type == "thought":
            data = event.get("data")
            if not isinstance(data, str):
                logger.error("grok_stream_invalid_thought", reason="non-string data")
                raise GrokProviderError("Grok thought event requires string data")
            self._append_progress(data, "thought")
            return
        if event_type == "tool_call":
            self._start_tool(event)
            return
        if event_type == "tool_call_update":
            self._update_tool(event)
            return
        if event_type == "end":
            stop_reason = event.get("stopReason")
            if not isinstance(stop_reason, str) or not stop_reason:
                logger.error("grok_stream_invalid_end", reason="missing stopReason")
                raise GrokProviderError("Grok end event requires a stopReason")
            self._flush_buffer()
            self._stop_reason = stop_reason
            self._structured_output = event.get("structuredOutput")
            self._ended = True
            return
        if event_type == "error":
            message = event.get("message") or event.get("data")
            if not isinstance(message, str) or not message:
                logger.error("grok_stream_invalid_error", reason="missing message")
                raise GrokProviderError("Grok error event requires a message")
            logger.error("grok_stream_error", error=message)
            raise GrokProviderError(f"Grok stream error: {message}")

        params = event.get("params")
        update = params.get("update") if isinstance(params, dict) else None
        if not isinstance(update, dict):
            if event.get("method") == "session/update":
                logger.error(
                    "grok_stream_invalid_session_update",
                    reason="missing update object",
                )
                raise GrokProviderError("Grok session update requires an update object")
            return
        update_type = update.get("sessionUpdate")
        content = update.get("content")
        data = content.get("text") if isinstance(content, dict) else None
        if update_type == "agent_message_chunk":
            if not isinstance(data, str):
                logger.error(
                    "grok_stream_invalid_message_chunk",
                    reason="non-string content",
                )
                raise GrokProviderError("Grok message chunk requires string content")
            self._text_parts.append(data)
            self._append_progress(data, "text")
        elif update_type == "agent_thought_chunk":
            if not isinstance(data, str):
                logger.error(
                    "grok_stream_invalid_thought_chunk",
                    reason="non-string content",
                )
                raise GrokProviderError("Grok thought chunk requires string content")
            self._append_progress(data, "thought")
        elif update_type == "tool_call":
            self._start_tool(update)
        elif update_type == "tool_call_update":
            self._update_tool(update)
        elif event_type is not None:
            logger.debug("grok_stream_unknown_event", event_type=str(event_type))

    @property
    def ended(self) -> bool:
        """Whether a terminal Grok event has been consumed."""
        return self._ended

    def finish(self) -> GrokStreamResult:
        """Flush display output and return the normalized stream state."""
        self._flush_buffer()
        return GrokStreamResult(
            final_text="".join(self._text_parts),
            structured_output=self._structured_output,
            stop_reason=self._stop_reason,
            ended=self._ended,
        )


class GrokProvider(ProviderBase):
    """Grok provider backed by the local Grok Build CLI."""

    def __init__(self, options: GrokOptions | None = None) -> None:
        self.options = options if options is not None else GrokOptions()

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
        """Execute one unattended Grok task and normalize its event stream."""
        if model is None:
            raise GrokProviderError("Grok provider requires a model")

        prompt_path: Path | None = None
        try:
            if len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix="fdsx-grok-",
                    suffix=".txt",
                    delete=False,
                ) as prompt_file:
                    prompt_path = Path(prompt_file.name)
                    prompt_file.write(prompt)
                prompt_flags = ["--prompt-file", str(prompt_path)]
            else:
                prompt_flags = ["--single", prompt]
        except (OSError, UnicodeError) as exc:
            if prompt_path is not None:
                try:
                    prompt_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "grok_prompt_file_cleanup_failed",
                        path=str(prompt_path),
                    )
            logger.error("grok_prompt_file_creation_failed", error=str(exc))
            raise GrokProviderError("Failed to prepare the Grok prompt") from exc

        args = ["grok", "--no-auto-update", "--no-ask-user"]
        args.extend(self.options.to_cli_flags())
        if output_schema is not None:
            args.extend(["--json-schema", serialize_output_schema(output_schema)])
        args.extend(
            [
                "--model",
                model,
                "--output-format",
                "streaming-json",
            ]
        )
        args.extend(prompt_flags)

        completion_event = threading.Event()
        suspend_fn: list[Callable[[], None] | None] = [None]
        resume_fn: list[Callable[[], None] | None] = [None]

        parser = GrokStreamParser(
            output_callback=output_callback,
            summary_callback=summary_callback,
            on_tool_start=lambda: (
                suspend_fn[0]() if suspend_fn[0] is not None else None
            ),
            on_tool_end=lambda: resume_fn[0]() if resume_fn[0] is not None else None,
        )
        stream_error: list[GrokProviderError | None] = [None]

        def stream_callback(line: str) -> None:
            try:
                parser.feed(line)
            except GrokProviderError as exc:
                stream_error[0] = exc
                completion_event.set()
            else:
                if parser.ended:
                    completion_event.set()

        def on_inactivity_hooks(
            suspend: Callable[[], None], resume: Callable[[], None]
        ) -> None:
            suspend_fn[0] = suspend
            resume_fn[0] = resume

        effective_inactivity = (
            self.options.inactivity_timeout
            if self.options.inactivity_timeout is not None
            else DEFAULT_INACTIVITY_TIMEOUT
        )
        effective_timeout = (
            timeout if timeout is not None else DEFAULT_EXECUTION_TIMEOUT
        )
        try:
            result = _run_subprocess(
                args=args,
                timeout=effective_timeout,
                output_callback=stream_callback,
                stderr_callback=stderr_callback,
                completion_event=completion_event,
                inactivity_timeout=effective_inactivity,
                on_process_start=on_process_start,
                on_inactivity_hooks=on_inactivity_hooks,
                max_suspend_duration=effective_inactivity,
            )
        finally:
            if prompt_path is not None:
                try:
                    prompt_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "grok_prompt_file_cleanup_failed",
                        path=str(prompt_path),
                    )

        stream_result = parser.finish()
        final_text = stream_result.final_text
        if stream_result.structured_output is not None:
            final_text = json.dumps(stream_result.structured_output)

        missing_cli = (
            result.exit_code != 0
            and "grok" in result.stderr.lower()
            and (
                "no such file or directory" in result.stderr.lower()
                or "not found" in result.stderr.lower()
            )
        )
        if missing_cli:
            logger.error("grok_cli_not_found")
            result = ProviderResult(
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=(
                    "Grok CLI not found on PATH. Install and authenticate Grok "
                    "Build before using provider=grok."
                ),
            )

        if output_schema is not None:
            result = add_schema_update_guidance(
                result, provider_name="Grok", schema_flag="--json-schema"
            )

        if result.exit_code != 0:
            return result

        if stream_error[0] is not None:
            return ProviderResult(exit_code=1, stdout="", stderr=str(stream_error[0]))

        normalized_reason = "".join(
            character.lower()
            for character in (stream_result.stop_reason or "")
            if character.isalnum()
        )
        if "maxturn" in normalized_reason and self.options.on_max_turns == "fail":
            return ProviderResult(
                exit_code=1,
                stdout=final_text,
                stderr="Grok reached the configured maximum number of turns",
                final_message=final_text or None,
            )
        if any(
            marker in normalized_reason
            for marker in ("cancel", "error", "fail", "refusal")
        ):
            reason = stream_result.stop_reason or "error"
            return ProviderResult(
                exit_code=1,
                stdout=final_text,
                stderr=(f"Grok was {reason.lower()} before producing a final result"),
                final_message=final_text or None,
            )
        if not stream_result.ended or not final_text:
            return ProviderResult(
                exit_code=1,
                stdout=final_text,
                stderr="Grok stream ended without a final result",
                final_message=final_text or None,
            )
        return ProviderResult(
            exit_code=0,
            stdout=final_text,
            stderr=result.stderr,
            final_message=final_text,
        )

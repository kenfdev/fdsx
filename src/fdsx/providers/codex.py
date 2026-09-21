import json
import logging
import subprocess  # nosec B404 - process callback type annotations only.
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderBase,
    ProviderResult,
    ProviderSchemaError,
    ProviderSessionError,
    SessionRequest,
    _run_subprocess,
    add_schema_update_guidance,
    serialize_output_schema,
)
from fdsx.providers.privacy import PrivateAwareLogger, PrivateStreamGuard

logger = PrivateAwareLogger(logging.getLogger(__name__))
structured_logger = PrivateAwareLogger(structlog.get_logger(__name__))

# ---------------------------------------------------------------------------
# JSONL streaming format constants
# ---------------------------------------------------------------------------

# CLI flags added to enable JSONL output when output_callback is provided
_STREAM_FORMAT_FLAGS = ["--json"]

# Top-level JSONL event type strings
_EVENT_ITEM_STARTED = "item.started"
_EVENT_ITEM_COMPLETED = "item.completed"
_EVENT_TURN_FAILED = "turn.failed"
_EVENT_ERROR = "error"

# Item type strings within item.started / item.completed events
_ITEM_TYPE_AGENT_MESSAGE = "agent_message"
_ITEM_TYPE_REASONING = "reasoning"
_ITEM_TYPE_COMMAND_EXECUTION = "command_execution"
_ITEM_TYPE_FILE_CHANGE = "file_change"
_ITEM_TYPE_MCP_TOOL_CALL = "mcp_tool_call"


class _CodexSession:
    """Per-invocation metadata, never shared between concurrent children.

    Reader callbacks record protocol failures; domain errors are raised on the
    calling thread. Native diagnostics are deliberately not forwarded or logged.
    """

    def __init__(self, request: SessionRequest) -> None:
        self.request = request
        self.source_id: str | None = None
        self.ids: list[str] = []
        self.malformed = False
        self.failed = False
        self.completed = 0
        if request.source is not None:
            source_id = request.source.get("session_id")
            if request.source.get("provider") != "codex" or not self.valid_id(
                source_id
            ):
                raise self.error("source reference is invalid")
            self.source_id = source_id

    @staticmethod
    def valid_id(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return str(UUID(value)) == value.lower()
        except ValueError:
            return False

    def error(self, reason: str) -> ProviderSessionError:
        structured_logger.error(
            "codex_session_failed", state=self.request.state_name, reason=reason
        )
        return ProviderSessionError(
            f"State '{self.request.state_name}': Codex native session {reason}; "
            "check that the CLI supports persistent exec sessions and exec fork; "
            "for forks, restore missing source/ancestor history in the same Codex storage. "
            "Rerunning the source cannot fix unsupported CLI features. "
            "No fresh-session fallback was attempted."
        )

    def wrap(self, callback: Callable[[str], None]) -> Callable[[str], None]:
        def consume(line: str) -> None:
            if not line.strip():
                return
            try:
                event = json.loads(line)
            except (ValueError, RecursionError):
                self.malformed = True
                return
            if not isinstance(event, dict):
                self.malformed = True
                return
            kind = event.get("type")
            if kind == "thread.started":
                value = event.get("thread_id")
                if not self.valid_id(value):
                    self.malformed = True
                else:
                    self.ids.append(str(value))
            elif kind in (_EVENT_ERROR, _EVENT_TURN_FAILED):
                self.failed = True
            elif kind == "turn.completed":
                self.completed += 1
            elif kind in (_EVENT_ITEM_STARTED, _EVENT_ITEM_COMPLETED):
                item = event.get("item")
                if not isinstance(item, dict) or any(
                    key in item and not isinstance(item[key], str)
                    for key in ("type", "text", "command", "name")
                ):
                    self.malformed = True
                    return
                callback(line)

        return consume

    def finish(
        self, result: ProviderResult, output: str | None, final_message: str | None
    ) -> ProviderResult:
        if result.exit_code != 0 or self.failed:
            return ProviderResult(
                result.exit_code or 1,
                "",
                str(
                    self.error(
                        "execution failed (history or CLI options may be incompatible)"
                    )
                ),
            )
        if self.malformed or len(self.ids) != 1 or self.completed != 1:
            raise self.error("completion metadata is missing, malformed, or ambiguous")
        child = self.ids[0]
        if self.source_id is not None and child.lower() == self.source_id.lower():
            raise self.error("child reference reuses the source")
        if output is None:
            raise self.error("completion output is missing")
        return ProviderResult(
            0,
            output,
            "",
            final_message=final_message,
            session_reference={"provider": "codex", "session_id": child},
        )


class CodexOptions(BaseModel):
    """Options for the Codex CLI provider."""

    model_config = ConfigDict(extra="forbid")

    reasoning_effort: (
        Literal["low", "medium", "high", "xhigh", "max", "ultra"] | None
    ) = None
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | None = None
    approval_policy: Literal["untrusted", "on-request", "never"] | None = None
    developer_instructions: str | None = None
    agents_enabled: bool | None = None
    full_auto: bool = False
    dangerously_bypass_approvals_and_sandbox: bool = False
    inactivity_timeout: int | None = None

    def to_cli_flags(self) -> list[str]:
        """Translate options to Codex CLI flags."""
        flags: list[str] = []
        if self.reasoning_effort is not None:
            flags.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
        if self.sandbox is not None:
            flags.extend(["--sandbox", self.sandbox])
        if self.approval_policy is not None:
            flags.extend(["-c", f'approval_policy="{self.approval_policy}"'])
        if self.developer_instructions is not None:
            # JSON strings are valid TOML basic strings and safely preserve quotes,
            # newlines, backslashes, and Unicode in Codex's key=value override.
            encoded = json.dumps(self.developer_instructions, ensure_ascii=False)
            flags.extend(["-c", f"developer_instructions={encoded}"])
        if self.agents_enabled is not None:
            enabled = str(self.agents_enabled).lower()
            flags.extend(["-c", f"agents.enabled={enabled}"])
        if self.full_auto:
            flags.append("--full-auto")
        if self.dangerously_bypass_approvals_and_sandbox:
            flags.append("--dangerously-bypass-approvals-and-sandbox")
        return flags


class CodexProvider(ProviderBase):
    """Codex provider - executes Codex CLI."""

    def __init__(self, options: CodexOptions | None = None) -> None:
        self.options: CodexOptions = options if options is not None else CodexOptions()

    def execute_with_session(
        self, request: SessionRequest, **kwargs: Any
    ) -> ProviderResult:
        return self.execute(session_request=request, **kwargs)

    def _make_stream_callback(
        self,
        output_callback: Callable[[str], None],
        final_message_callback: Callable[[str], None] | None = None,
        error_callback: Callable[[str], None] | None = None,
    ) -> tuple[Callable[[str], None], Callable[[], str | None]]:
        """Create a streaming callback that parses Codex ``--json`` JSONL lines.

        Wraps ``output_callback`` so that human-readable content extracted from
        Codex's JSONL events is forwarded to the caller while the raw JSON lines
        are silently consumed.

        Returns a ``(stream_callback, get_result)`` tuple:
        - ``stream_callback``: parses each JSON line and dispatches content to
          ``output_callback``. Malformed JSON lines are skipped with a warning
          logged via ``logger.warning``.
        - ``get_result``: returns the final stdout string after streaming is
          complete. Concatenates all ``agent_message`` item texts. Returns
          ``None`` if no ``agent_message`` events were received (including
          partial collection on unexpected provider exit).
        - ``final_message_callback``: receives each complete agent message so
          the caller can preserve the last one separately from complete stdout.

        Event routing:
        - ``item.started`` + ``command_execution`` → ``[tool: {command}]``
        - ``item.started`` + ``file_change`` → ``[tool: file_change]``
        - ``item.started`` + ``mcp_tool_call`` → ``[tool: {name}]``
        - ``item.completed`` + ``agent_message`` → ``item.text`` (accumulated)
        - ``item.completed`` + ``reasoning`` → ``[thinking] {text}``
        - ``turn.failed`` / ``error`` → warning and optional error callback
          (never included in agent output)
        """
        agent_message_parts: list[str] = []

        def stream_callback(line: str) -> None:
            if not line.strip():
                return
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Malformed JSON line skipped: %s", line)
                return

            event_type = event.get("type")

            if event_type == _EVENT_ITEM_STARTED:
                item = event.get("item", {})
                item_type = item.get("type")
                if item_type == _ITEM_TYPE_COMMAND_EXECUTION:
                    command = item.get("command", "unknown")
                    output_callback(f"[tool: {command}]")
                elif item_type == _ITEM_TYPE_FILE_CHANGE:
                    output_callback("[tool: file_change]")
                elif item_type == _ITEM_TYPE_MCP_TOOL_CALL:
                    name = item.get("name", "unknown")
                    output_callback(f"[tool: {name}]")

            elif event_type == _EVENT_ITEM_COMPLETED:
                item = event.get("item", {})
                item_type = item.get("type")
                if item_type == _ITEM_TYPE_AGENT_MESSAGE:
                    text = item.get("text", "")
                    if final_message_callback is not None:
                        final_message_callback(text)
                    if text:
                        agent_message_parts.append(text)
                        output_callback(text)
                elif item_type == _ITEM_TYPE_REASONING:
                    text = item.get("text", "")
                    if text:
                        output_callback(f"[thinking] {text}")

            elif event_type in (_EVENT_TURN_FAILED, _EVENT_ERROR):
                detail = event.get("error") or event.get("message")
                if isinstance(detail, dict):
                    detail = detail.get("message")
                message = (
                    detail.strip()
                    if isinstance(detail, str) and detail.strip()
                    else f"Codex {event_type} event without an error message"
                )
                if event_type == _EVENT_TURN_FAILED:
                    logger.warning("turn.failed event received: %s", message)
                else:
                    logger.warning("Codex error event: %s", message)
                if error_callback is not None:
                    error_callback(message)

        def get_result() -> str | None:
            if agent_message_parts:
                return "\n".join(agent_message_parts)
            return None

        return stream_callback, get_result

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
        session_request: SessionRequest | None = None,
    ) -> ProviderResult:
        """Execute Codex CLI with a prompt.

        Args:
            prompt: The prompt to send to Codex
            model: Model name
            timeout: Timeout in seconds
            command: Ignored for codex provider
            output_callback: Optional callback for streaming stdout lines.
                When provided, ``--json`` is appended to the CLI invocation
                and ``ProviderResult.stdout`` is populated from concatenated
                ``agent_message`` item texts (falling back to partial content
                on unexpected provider exit). ``ProviderResult.final_message``
                contains the last complete ``agent_message``.
            stderr_callback: Optional callback for streaming stderr lines
            on_process_start: Optional callback invoked after Popen creation
            summary_callback: Optional callback for summary lines (ignored for Codex).

        Returns:
            ProviderResult with exit code and output
        """
        session = (
            _CodexSession(session_request) if session_request is not None else None
        )
        schema_path: Path | None = None
        if output_schema is not None:
            encoded_schema = serialize_output_schema(output_schema)
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    prefix="fdsx-codex-schema-",
                    suffix=".json",
                    delete=False,
                ) as schema_file:
                    schema_path = Path(schema_file.name)
                    schema_file.write(encoded_schema)
            except (OSError, UnicodeError) as exc:
                if schema_path is not None:
                    try:
                        schema_path.unlink(missing_ok=True)
                    except OSError as cleanup_exc:
                        structured_logger.warning(
                            "codex_schema_file_cleanup_failed",
                            path=str(schema_path),
                            error=str(cleanup_exc),
                        )
                structured_logger.error(
                    "codex_schema_file_creation_failed",
                    error=str(exc),
                )
                raise ProviderSchemaError(
                    "Failed to create the Codex output schema file"
                ) from exc

        try:
            use_stdin = len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD
            stdin_data: str | None
            args = ["codex", "exec"]
            if model:
                args.extend(["--model", model])
            args.extend(self.options.to_cli_flags())
            if schema_path is not None:
                args.extend(["--output-schema", str(schema_path)])
            if session is not None:
                # Persist native history even when user configuration is ephemeral.
                # All exec options precede the subcommand (not all are global).
                args.extend(["-c", "ephemeral=false", "--json"])
                if session.source_id is not None:
                    args.extend(["fork", session.source_id])
                args.append("-")
                stdin_data = prompt
                output_callback = output_callback or (lambda line: None)
                stderr_callback = None  # Native errors can contain private prompts.
            elif use_stdin:
                stdin_data = prompt
            else:
                args.append(prompt)
                stdin_data = None

            effective_inactivity = (
                self.options.inactivity_timeout
                if self.options.inactivity_timeout is not None
                else DEFAULT_INACTIVITY_TIMEOUT
            )
            effective_timeout = (
                timeout if timeout is not None else DEFAULT_EXECUTION_TIMEOUT
            )

            if output_callback is not None:
                if session is None:
                    args.extend(_STREAM_FORMAT_FLAGS)
                final_message: list[str | None] = [None]
                errors: list[str] = []
                guard = PrivateStreamGuard()

                def capture_error(message: str) -> None:
                    if message not in errors:
                        errors.append(message)
                        if stderr_callback is not None:
                            stderr_callback(message)

                def capture_final_message(message: str) -> None:
                    # None denotes an absent final event, never a received null.
                    # Validate before retaining it or falling back to prior stdout.
                    if guard.enabled and not isinstance(message, str):
                        raise ValueError("private agent message must be text")
                    final_message[0] = message

                stream_callback, get_result = self._make_stream_callback(
                    output_callback,
                    final_message_callback=capture_final_message,
                    error_callback=capture_error,
                )
                stream_callback = guard.wrap(stream_callback)
                result = _run_subprocess(
                    args=args,
                    timeout=effective_timeout,
                    output_callback=session.wrap(stream_callback)
                    if session
                    else stream_callback,
                    stderr_callback=stderr_callback,
                    stdin_data=stdin_data,
                    inactivity_timeout=effective_inactivity,
                    on_process_start=on_process_start,
                )
                guard.check()
                if session is not None:
                    return session.finish(result, get_result(), final_message[0])
                if result.exit_code != 0 and errors:
                    diagnostics = (
                        [result.stderr.strip()] if result.stderr.strip() else []
                    )
                    diagnostics.extend(
                        message for message in errors if message not in diagnostics
                    )
                    result = ProviderResult(
                        exit_code=result.exit_code,
                        stdout=result.stdout,
                        stderr="\n".join(diagnostics),
                        final_message=result.final_message,
                    )
                if output_schema is not None:
                    result = add_schema_update_guidance(
                        result,
                        provider_name="Codex",
                        schema_flag="--output-schema",
                    )
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
                    result,
                    provider_name="Codex",
                    schema_flag="--output-schema",
                )
            return result
        finally:
            if schema_path is not None:
                try:
                    schema_path.unlink(missing_ok=True)
                except OSError:
                    structured_logger.warning(
                        "codex_schema_file_cleanup_failed",
                        path=str(schema_path),
                    )

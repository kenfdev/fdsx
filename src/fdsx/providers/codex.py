import json
import logging
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    ProviderBase,
    ProviderResult,
    _run_subprocess,
)

logger = logging.getLogger(__name__)

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


class CodexOptions(BaseModel):
    """Options for the Codex CLI provider."""

    model_config = ConfigDict(extra="forbid")

    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | None = None
    approval_policy: Literal["untrusted", "on-request", "never"] | None = None
    full_auto: bool = False
    dangerously_bypass_approvals_and_sandbox: bool = False

    def to_cli_flags(self) -> list[str]:
        """Translate options to Codex CLI flags."""
        flags: list[str] = []
        if self.sandbox is not None:
            flags.extend(["--sandbox", self.sandbox])
        if self.approval_policy is not None:
            flags.extend(["--approval-policy", self.approval_policy])
        if self.full_auto:
            flags.append("--full-auto")
        if self.dangerously_bypass_approvals_and_sandbox:
            flags.append("--dangerously-bypass-approvals-and-sandbox")
        return flags


class CodexProvider(ProviderBase):
    """Codex provider - executes Codex CLI."""

    def __init__(self, options: CodexOptions | None = None) -> None:
        self.options: CodexOptions = options if options is not None else CodexOptions()

    def _make_stream_callback(
        self, output_callback: Callable[[str], None]
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

        Event routing:
        - ``item.started`` + ``command_execution`` → ``[tool: {command}]``
        - ``item.started`` + ``file_change`` → ``[tool: file_change]``
        - ``item.started`` + ``mcp_tool_call`` → ``[tool: {name}]``
        - ``item.completed`` + ``agent_message`` → ``item.text`` (accumulated)
        - ``item.completed`` + ``reasoning`` → ``[thinking] {text}``
        - ``turn.failed`` → ``logger.warning``, no callback dispatch
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
                    if text:
                        agent_message_parts.append(text)
                        output_callback(text)
                elif item_type == _ITEM_TYPE_REASONING:
                    text = item.get("text", "")
                    if text:
                        output_callback(f"[thinking] {text}")

            elif event_type == _EVENT_TURN_FAILED:
                logger.warning("turn.failed event received: %s", event.get("error", ""))

            elif event_type == _EVENT_ERROR:
                logger.warning(
                    "Codex error event: %s", event.get("message", str(event))
                )

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
                on unexpected provider exit).
            stderr_callback: Optional callback for streaming stderr lines

        Returns:
            ProviderResult with exit code and output
        """
        use_stdin = len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD
        args = ["codex", "exec"]
        if model:
            args.extend(["--model", model])
        args.extend(self.options.to_cli_flags())
        if use_stdin:
            stdin_data: str | None = prompt
        else:
            args.append(prompt)
            stdin_data = None

        if output_callback is not None:
            args.extend(_STREAM_FORMAT_FLAGS)
            stream_callback, get_result = self._make_stream_callback(output_callback)
            result = _run_subprocess(
                args=args,
                timeout=timeout,
                output_callback=stream_callback,
                stderr_callback=stderr_callback,
                stdin_data=stdin_data,
            )
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
        )

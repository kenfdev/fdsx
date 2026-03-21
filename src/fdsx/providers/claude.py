from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import ProviderBase, ProviderResult, _run_subprocess


class ClaudeOptions(BaseModel):
    """Options for the Claude CLI provider."""

    model_config = ConfigDict(extra="forbid")

    permission_mode: Literal["default", "acceptEdits", "bypassPermissions", "dontAsk", "plan", "auto"] | None = None
    dangerously_skip_permissions: bool = False
    allowed_tools: list[str] = []
    disallowed_tools: list[str] = []

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
        self.options: ClaudeOptions = options if options is not None else ClaudeOptions()

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        command: str | None = None,
        output_callback: Callable[[str], None] | None = None,
        stderr_callback: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        """Execute Claude CLI with a prompt.

        Args:
            prompt: The prompt to send to Claude
            model: Model name (e.g., opus, sonnet)
            timeout: Timeout in seconds
            command: Ignored for claude provider
            output_callback: Optional callback for streaming stdout lines
            stderr_callback: Optional callback for streaming stderr lines

        Returns:
            ProviderResult with exit code and output
        """
        args = ["claude", "-p", prompt]
        if model:
            args.extend(["--model", model])
        args.extend(self.options.to_cli_flags())

        return _run_subprocess(
            args=args,
            timeout=timeout,
            output_callback=output_callback,
            stderr_callback=stderr_callback,
        )

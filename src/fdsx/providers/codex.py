from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import ProviderBase, ProviderResult, _run_subprocess


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

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        command: str | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        """Execute Codex CLI with a prompt.

        Args:
            prompt: The prompt to send to Codex
            model: Model name
            timeout: Timeout in seconds
            command: Ignored for codex provider
            output_callback: Optional callback for streaming output

        Returns:
            ProviderResult with exit code and output
        """
        args = ["codex", "exec"]
        if model:
            args.extend(["--model", model])
        args.append(prompt)

        return _run_subprocess(
            args=args,
            timeout=timeout,
            output_callback=output_callback,
        )

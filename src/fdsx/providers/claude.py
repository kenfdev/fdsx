from typing import Callable

from fdsx.providers.base import ProviderBase, ProviderResult, _run_subprocess


class ClaudeProvider(ProviderBase):
    """Claude provider - executes Claude CLI."""

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        command: str | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        """Execute Claude CLI with a prompt.

        Args:
            prompt: The prompt to send to Claude
            model: Model name (e.g., opus, sonnet)
            timeout: Timeout in seconds
            command: Ignored for claude provider
            output_callback: Optional callback for streaming output

        Returns:
            ProviderResult with exit code and output
        """
        args = ["claude", "-p", prompt]
        if model:
            args.extend(["--model", model])

        return _run_subprocess(
            args=args,
            timeout=timeout,
            output_callback=output_callback,
        )

from typing import Callable

from fdsx.providers.base import ProviderBase, ProviderResult, _run_subprocess


class OpenCodeProvider(ProviderBase):
    """OpenCode provider - executes OpenCode CLI."""

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        command: str | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        """Execute OpenCode CLI with a prompt.

        Args:
            prompt: The prompt to send to OpenCode
            model: Model name
            timeout: Timeout in seconds
            command: Ignored for opencode provider
            output_callback: Optional callback for streaming output

        Returns:
            ProviderResult with exit code and output
        """
        args = ["opencode"]
        if model:
            args.extend(["--model", model])
        args.append(prompt)

        return _run_subprocess(
            args=args,
            timeout=timeout,
            output_callback=output_callback,
        )

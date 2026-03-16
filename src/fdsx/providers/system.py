from typing import Callable

from fdsx.providers.base import ProviderBase, ProviderResult, _run_subprocess


class SystemProvider(ProviderBase):
    """System provider - executes shell commands."""

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        command: str | None = None,
        output_callback: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        """Execute a shell command.

        Args:
            prompt: Ignored for system provider
            model: Ignored for system provider
            timeout: Timeout in seconds
            command: Shell command to execute
            output_callback: Optional callback for streaming output

        Returns:
            ProviderResult with exit code and output
        """
        cmd = command or prompt

        if not cmd:
            return ProviderResult(
                exit_code=1,
                stdout="",
                stderr="No command provided",
            )

        return _run_subprocess(
            args=[cmd],
            timeout=timeout,
            output_callback=output_callback,
            shell=True,
        )

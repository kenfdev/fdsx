import subprocess  # nosec B404 - process callback type annotations only.
from collections.abc import Callable
from typing import Any

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
        stderr_callback: Callable[[str], None] | None = None,
        on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
        summary_callback: Callable[[str], None] | None = None,
        output_schema: Any | None = None,
    ) -> ProviderResult:
        """Execute a shell command.

        Args:
            prompt: Ignored for system provider
            model: Ignored for system provider
            timeout: Timeout in seconds
            command: Shell command to execute
            output_callback: Optional callback for streaming stdout lines
            stderr_callback: Optional callback for streaming stderr lines
            on_process_start: Optional callback invoked after Popen creation
            summary_callback: Optional callback for summary lines (ignored for system).

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

        # System tasks explicitly execute trusted workflow shell commands by design.
        return _run_subprocess(  # nosec B604
            args=[cmd],
            timeout=timeout,
            output_callback=output_callback,
            stderr_callback=stderr_callback,
            shell=True,
            on_process_start=on_process_start,
        )

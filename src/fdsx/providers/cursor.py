import shutil
import subprocess
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

        # TODO: streaming branch (T012)
        return _run_subprocess(
            args=args,
            timeout=effective_timeout,
            output_callback=output_callback,
            stderr_callback=stderr_callback,
            stdin_data=stdin_data,
            inactivity_timeout=effective_inactivity,
            on_process_start=on_process_start,
        )

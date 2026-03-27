import logging
import subprocess
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderBase,
    ProviderResult,
    _run_subprocess,
)

logger = logging.getLogger(__name__)


class GeminiOptions(BaseModel):
    """Options for the Gemini CLI provider."""

    model_config = ConfigDict(extra="forbid")

    approval_mode: Literal["default", "auto_edit", "yolo", "plan"] | None = None
    yolo: bool = False
    sandbox: bool = False
    include_directories: list[str] = []
    extensions: list[str] = []
    policy: list[str] = []
    inactivity_timeout: int | None = None

    def to_cli_flags(self) -> list[str]:
        """Translate options to Gemini CLI flags."""
        flags: list[str] = []
        if self.yolo:
            flags.append("--yolo")
        elif self.approval_mode is not None:
            flags.extend(["--approval-mode", self.approval_mode])
        if self.sandbox:
            flags.append("--sandbox")
        if self.include_directories:
            flags.extend(["--include-directories", ",".join(self.include_directories)])
        if self.extensions:
            flags.extend(["--extensions", ",".join(self.extensions)])
        for p in self.policy:
            flags.extend(["--policy", p])
        return flags


class GeminiProvider(ProviderBase):
    """Gemini provider - executes Gemini CLI."""

    def __init__(self, options: GeminiOptions | None = None) -> None:
        self.options: GeminiOptions = (
            options if options is not None else GeminiOptions()
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
        """Execute Gemini CLI with a prompt.

        Args:
            prompt: The prompt to send to Gemini
            model: Model name
            timeout: Timeout in seconds
            command: Ignored for gemini provider
            output_callback: Optional callback for streaming stdout lines.
            stderr_callback: Optional callback for streaming stderr lines.
            on_process_start: Optional callback invoked after Popen creation.
            summary_callback: Optional callback for summary lines (ignored for Gemini).

        Returns:
            ProviderResult with exit code and output
        """
        use_stdin = len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD
        args = ["gemini", "-p"]
        if use_stdin:
            args.append("-")
            stdin_data: str | None = prompt
        else:
            args.append(prompt)
            stdin_data = None
        if model:
            args.extend(["--model", model])
        args.extend(self.options.to_cli_flags())

        effective_inactivity = (
            self.options.inactivity_timeout
            if self.options.inactivity_timeout is not None
            else DEFAULT_INACTIVITY_TIMEOUT
        )

        return _run_subprocess(
            args=args,
            timeout=timeout,
            output_callback=output_callback,
            stderr_callback=stderr_callback,
            stdin_data=stdin_data,
            inactivity_timeout=effective_inactivity,
            on_process_start=on_process_start,
        )

import logging
import shutil
import subprocess
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderBase,
    ProviderResult,
    _run_subprocess,
)

logger = logging.getLogger(__name__)


class PiProviderError(Exception):
    """Raised when the pi provider encounters a domain-level error."""


class PiOptions(BaseModel):
    """Options for the pi CLI provider."""

    model_config = ConfigDict(extra="forbid")

    inactivity_timeout: int | None = None

    def to_cli_flags(self) -> list[str]:
        """Translate options to pi CLI flags."""
        return []


class PiProvider(ProviderBase):
    """pi provider - executes pi CLI."""

    def __init__(self, options: PiOptions | None = None) -> None:
        self.options: PiOptions = options if options is not None else PiOptions()

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
        """Execute pi CLI with a prompt."""
        if shutil.which("pi") is None:
            logger.warning("pi_binary_missing")
            raise PiProviderError(
                "pi binary not found on PATH. Ensure pi is installed and available."
            )

        use_stdin = len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD
        args = ["pi", "-p"]
        if use_stdin:
            stdin_data: str | None = prompt
        else:
            args.append(prompt)
            stdin_data = None

        if model:
            args.extend(["--model", model])

        effective_inactivity = (
            self.options.inactivity_timeout
            if self.options.inactivity_timeout is not None
            else DEFAULT_INACTIVITY_TIMEOUT
        )
        effective_timeout = (
            timeout if timeout is not None else DEFAULT_EXECUTION_TIMEOUT
        )

        return _run_subprocess(
            args=args,
            timeout=effective_timeout,
            output_callback=output_callback,
            stderr_callback=stderr_callback,
            stdin_data=stdin_data,
            inactivity_timeout=effective_inactivity,
            on_process_start=on_process_start,
        )

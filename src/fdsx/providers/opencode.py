import json
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderBase,
    ProviderResult,
    _run_subprocess,
)


class OpenCodeOptions(BaseModel):
    """Options for the OpenCode CLI provider."""

    model_config = ConfigDict(extra="forbid")

    permission: str | dict[str, Any] | None = None
    inactivity_timeout: int | None = None

    def to_cli_flags(self) -> list[str]:
        """Translate options to OpenCode CLI flags (none currently defined)."""
        return []

    def to_env(self) -> dict[str, str]:
        """Build extra environment variables for the OpenCode subprocess."""
        if self.permission is None:
            return {}
        config = {"permission": self.permission}
        return {"OPENCODE_CONFIG_CONTENT": json.dumps(config)}


class OpenCodeProvider(ProviderBase):
    """OpenCode provider - executes OpenCode CLI."""

    def __init__(self, options: OpenCodeOptions | None = None) -> None:
        self.options: OpenCodeOptions = (
            options if options is not None else OpenCodeOptions()
        )

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        command: str | None = None,
        output_callback: Callable[[str], None] | None = None,
        stderr_callback: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        """Execute OpenCode CLI with a prompt.

        Args:
            prompt: The prompt to send to OpenCode
            model: Model name
            timeout: Timeout in seconds
            command: Ignored for opencode provider
            output_callback: Optional callback for streaming stdout lines
            stderr_callback: Optional callback for streaming stderr lines

        Returns:
            ProviderResult with exit code and output
        """
        use_stdin = len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD
        args = ["opencode", "run"]
        if model:
            args.extend(["-m", model])
        args.extend(self.options.to_cli_flags())
        if use_stdin:
            stdin_data: str | None = prompt
        else:
            args.append(prompt)
            stdin_data = None

        env = self.options.to_env() or None

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
            env=env,
            inactivity_timeout=effective_inactivity,
        )

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderBase,
    ProviderResult,
    SessionRequest,
    _run_subprocess,
    append_structured_output_guidance,
)

logger = logging.getLogger(__name__)


class PiProviderError(Exception):
    """Raised when the pi provider encounters a domain-level error."""


class PiOptions(BaseModel):
    """Options for the pi CLI provider."""

    model_config = ConfigDict(extra="forbid")

    inactivity_timeout: int | None = None
    allowed_tools: list[str] = []
    disallowed_tools: list[str] = []
    disable_tools: bool = False

    @model_validator(mode="after")
    def validate_tool_restrictions(self) -> "PiOptions":
        """Validate mutually exclusive pi tool restriction options."""
        if self.disable_tools and self.allowed_tools:
            raise ValueError("disable_tools cannot be combined with allowed_tools")
        if self.disable_tools and self.disallowed_tools:
            raise ValueError("disable_tools cannot be combined with disallowed_tools")
        return self

    def to_cli_flags(self) -> list[str]:
        """Translate options to pi CLI flags."""
        flags: list[str] = []

        if self.disable_tools:
            flags.append("--no-tools")
            return flags

        if self.allowed_tools:
            flags.extend(["--tools", ",".join(self.allowed_tools)])
        if self.disallowed_tools:
            flags.extend(["--exclude-tools", ",".join(self.disallowed_tools)])

        return flags


class PiProvider(ProviderBase):
    """pi provider - executes pi CLI."""

    def __init__(self, options: PiOptions | None = None) -> None:
        self.options: PiOptions = options if options is not None else PiOptions()

    def execute_with_session(
        self, request: SessionRequest, **kwargs: Any
    ) -> ProviderResult:
        """Optional native-session capability; other providers need not implement it."""
        return self.execute(session_request=request, **kwargs)

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
        session_request: SessionRequest | None = None,
    ) -> ProviderResult:
        """Execute pi CLI with a prompt."""
        if shutil.which("pi") is None:
            logger.warning("pi_binary_missing")
            if session_request is not None:
                from fdsx.providers.pi_sessions import session_error

                raise session_error(
                    session_request.state_name,
                    "runtime is unavailable; install Pi >= 0.85.1 on PATH",
                )
            raise PiProviderError(
                "pi binary not found on PATH. Ensure pi is installed and available."
            )

        prompt = append_structured_output_guidance(prompt, output_schema)
        use_stdin = len(prompt.encode("utf-8")) >= ARG_MAX_STDIN_THRESHOLD
        args = ["pi", "-p"]
        if use_stdin:
            stdin_data: str | None = prompt
        else:
            args.append(prompt)
            stdin_data = None

        if model:
            args.extend(["--model", model])

        args.extend(self.options.to_cli_flags())

        session_directory = None
        child_path = None
        if session_request is not None:
            from fdsx.providers.pi_sessions import (
                new_session_directory,
                select_source,
                session_error,
            )

            version_result = _run_subprocess(args=["pi", "--version"], timeout=10)
            version = re.fullmatch(
                r"(?:pi\s+)?(\d+)\.(\d+)\.(\d+)", version_result.stdout.strip()
            )
            if (
                version_result.exit_code != 0
                or version is None
                or tuple(map(int, version.groups())) < (0, 85, 1)
            ):
                raise session_error(
                    session_request.state_name,
                    "forks require Pi >= 0.85.1 with native SessionManager and v3 session persistence",
                )
            session_directory = new_session_directory(session_request.state_name)
            if session_request.source is not None:
                select_source(session_request.source, session_request.state_name)
                preparation = _run_subprocess(
                    args=[
                        "pi",
                        "-p",
                        "--no-session",
                        "--no-extensions",
                        "--no-skills",
                        "--no-prompt-templates",
                        "--no-themes",
                        "-e",
                        str(Path(__file__).with_name("pi_fork.ts")),
                    ],
                    timeout=30,
                    stdin_data="",
                    on_process_start=on_process_start,
                    env={
                        "FDSX_PI_FORK_REQUEST": json.dumps(
                            {
                                **session_request.source,
                                "directory": str(session_directory),
                            }
                        )
                    },
                )
                try:
                    metadata = json.loads(preparation.stdout)
                    if (
                        preparation.exit_code != 0
                        or not isinstance(metadata, dict)
                        or not isinstance(metadata.get("path"), str)
                    ):
                        raise ValueError
                    child_path = Path(metadata["path"]).resolve()
                    if (
                        child_path.parent != session_directory
                        or not child_path.is_file()
                    ):
                        raise ValueError
                except (ValueError, OSError, RecursionError):
                    raise session_error(
                        session_request.state_name,
                        "native endpoint fork failed; check Pi >= 0.85.1 and saved history",
                    ) from None
                from fdsx.providers.pi_sessions import capture_reference

                child_reference = capture_reference(
                    child_path, session_request.state_name
                )
                if (
                    child_reference["id"] == session_request.source["id"]
                    or child_reference["endpoint"] != session_request.source["endpoint"]
                ):
                    raise session_error(
                        session_request.state_name,
                        "native fork returned the wrong child endpoint",
                    )
                select_source(session_request.source, session_request.state_name)
                args.extend(["--session", str(child_path)])
            args.extend(["--session-dir", str(session_directory)])

        effective_inactivity = (
            self.options.inactivity_timeout
            if self.options.inactivity_timeout is not None
            else DEFAULT_INACTIVITY_TIMEOUT
        )
        effective_timeout = (
            timeout if timeout is not None else DEFAULT_EXECUTION_TIMEOUT
        )

        result = _run_subprocess(
            args=args,
            timeout=effective_timeout,
            output_callback=output_callback,
            # Native diagnostics can include conversation history, even on
            # success. Only emit a sanitized failure after execution finishes.
            stderr_callback=stderr_callback if session_request is None else None,
            stdin_data=stdin_data,
            inactivity_timeout=effective_inactivity,
            on_process_start=on_process_start,
        )
        if session_request is not None:
            from fdsx.providers.pi_sessions import (
                capture_directory,
                capture_reference,
                select_source,
            )

            if session_request.source is not None:
                select_source(session_request.source, session_request.state_name)
            if result.exit_code == 0 and session_directory is not None:
                result.session_reference = (
                    capture_reference(child_path, session_request.state_name)
                    if child_path is not None
                    else capture_directory(
                        session_directory, session_request.state_name
                    )
                )
            elif result.exit_code != 0:
                # Native CLI diagnostics may contain history. Retry only with a
                # new native child; never expose content or execute a blank one.
                result.stderr = f"State '{session_request.state_name}': Pi native session execution failed (exit {result.exit_code}); check native fork support and session availability"
                if stderr_callback:
                    stderr_callback(result.stderr)
            if result.exit_code == 0:
                result.stderr = ""
        return result

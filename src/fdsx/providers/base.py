import logging
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)

# Commands at or above this byte length are piped via stdin to avoid ARG_MAX limits.
ARG_MAX_STDIN_THRESHOLD = 131072  # 128 KB


@dataclass
class ProviderResult:
    """Result from a provider execution."""

    exit_code: int
    stdout: str
    stderr: str


class ProviderBase(Protocol):
    """Protocol for provider adapters."""

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        command: str | None = None,
        output_callback: Callable[[str], None] | None = None,
        stderr_callback: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        """Execute a provider.

        Args:
            prompt: The prompt to send (or command for system)
            model: Model name (provider-specific)
            timeout: Timeout in seconds
            command: Command for system provider
            output_callback: Optional callback for streaming stdout lines
            stderr_callback: Optional callback for streaming stderr lines

        Returns:
            ProviderResult with exit code and output
        """
        ...


def check_cli_exists(command: str) -> bool:
    """Check if a CLI command exists on PATH."""
    import shutil

    return shutil.which(command) is not None


def _run_subprocess(
    args: list[str],
    timeout: int | None = None,
    output_callback: Callable[[str], None] | None = None,
    stderr_callback: Callable[[str], None] | None = None,
    stdin_data: str | None = None,
    shell: bool = False,
) -> ProviderResult:
    """Shared subprocess execution helper for all providers.

    Args:
        args: Command arguments list. If shell=True, args[0] is the shell command
            (passed to sh -c, or piped via stdin for large commands).
        timeout: Timeout in seconds.
        output_callback: Optional callback for streaming stdout lines.
        stderr_callback: Optional callback for streaming stderr lines.
        stdin_data: Optional data to pass via stdin.
        shell: If True, execute as shell command (via sh -c, or stdin fallback
            if command exceeds ARG_MAX_STDIN_THRESHOLD).

    Returns:
        ProviderResult with exit code and output.
    """
    if shell:
        command_size = len(args[0].encode("utf-8"))
        if command_size >= ARG_MAX_STDIN_THRESHOLD:
            logger.debug(
                "Command size %d bytes exceeds ARG_MAX_STDIN_THRESHOLD (%d bytes); piping via stdin",
                command_size,
                ARG_MAX_STDIN_THRESHOLD,
            )
            cmd: list[str] = ["sh"]
            stdin_data = args[0]
        else:
            cmd = ["sh", "-c", args[0]]
    else:
        cmd = args

    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        killed_by_timeout = False

        def _watchdog() -> None:
            nonlocal killed_by_timeout
            if timeout is None:
                return
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                killed_by_timeout = True
                process.kill()

        if timeout:
            watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
            watchdog_thread.start()

        # Write stdin data if provided, then close stdin
        if stdin_data is not None and process.stdin:
            try:
                process.stdin.write(stdin_data)
                process.stdin.close()
            except BrokenPipeError:
                pass

        stderr_lines: list[str] = []

        def _read_stderr() -> None:
            if process.stderr:
                try:
                    while True:
                        raw_line = process.stderr.readline()
                        if not raw_line:
                            break
                        line = raw_line.rstrip("\n")
                        stderr_lines.append(line)
                        if stderr_callback:
                            stderr_callback(line)
                except BrokenPipeError:
                    pass

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        stdout_lines: list[str] = []
        try:
            if process.stdout:
                while True:
                    line = process.stdout.readline()
                    if not line:
                        break
                    line = line.rstrip("\n")
                    stdout_lines.append(line)
                    if output_callback:
                        output_callback(line)
        except BrokenPipeError:
            pass

        stderr_thread.join(timeout=5)
        process.wait()
        if timeout:
            watchdog_thread.join(timeout=1)

        if killed_by_timeout:
            return ProviderResult(
                exit_code=124,
                stdout="",
                stderr=f"Command timed out after {timeout} seconds",
            )

        stdout = "\n".join(stdout_lines)
        stderr = "\n".join(stderr_lines)

        return ProviderResult(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    except Exception as e:
        return ProviderResult(
            exit_code=1,
            stdout="",
            stderr=str(e),
        )


def get_provider(name: str, options: dict[str, Any] | None = None) -> ProviderBase:
    """Factory function to get a provider by name.

    Args:
        name: Provider name (claude, codex, opencode, system).
        options: Optional dict of provider-specific options. Converted to the
                 appropriate typed options model internally. Ignored for system provider.

    Returns:
        A ProviderBase instance configured with the given options.

    Raises:
        ValueError: If the provider name is unknown.
    """
    if name == "system":
        from fdsx.providers.system import SystemProvider

        return SystemProvider()
    elif name == "claude":
        from fdsx.providers.claude import ClaudeOptions, ClaudeProvider

        claude_opts = ClaudeOptions.model_validate(options) if options else None
        return ClaudeProvider(claude_opts)
    elif name == "opencode":
        from fdsx.providers.opencode import OpenCodeOptions, OpenCodeProvider

        opencode_opts = OpenCodeOptions.model_validate(options) if options else None
        return OpenCodeProvider(opencode_opts)
    elif name == "codex":
        from fdsx.providers.codex import CodexOptions, CodexProvider

        codex_opts = CodexOptions.model_validate(options) if options else None
        return CodexProvider(codex_opts)
    else:
        raise ValueError(f"Unknown provider: {name}")

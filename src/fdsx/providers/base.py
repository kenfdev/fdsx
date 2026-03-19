import subprocess
import threading
from dataclasses import dataclass
from typing import Callable, Protocol


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
    ) -> ProviderResult:
        """Execute a provider.

        Args:
            prompt: The prompt to send (or command for system)
            model: Model name (provider-specific)
            timeout: Timeout in seconds
            command: Command for system provider
            output_callback: Optional callback for streaming output

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
    stdin_data: str | None = None,
    shell: bool = False,
) -> ProviderResult:
    """Shared subprocess execution helper for all providers.

    Args:
        args: Command arguments list. If shell=True, args[0] is passed to sh -c.
        timeout: Timeout in seconds.
        output_callback: Optional callback for streaming stdout lines.
        stdin_data: Optional data to pass via stdin.
        shell: If True, execute via sh -c (for system provider).

    Returns:
        ProviderResult with exit code and output.
    """
    if shell:
        cmd: list[str] = ["sh", "-c", args[0]]
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

        stderr_output: list[str] = []

        def _read_stderr() -> None:
            if process.stderr:
                stderr_output.append(process.stderr.read())

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        stdout_lines: list[str] = []
        try:
            if process.stdout:
                for line in process.stdout:
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
        stderr = stderr_output[0] if stderr_output else ""

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


def get_provider(name: str) -> ProviderBase:
    """Factory function to get a provider by name."""
    if name == "system":
        from fdsx.providers.system import SystemProvider

        return SystemProvider()
    elif name == "claude":
        from fdsx.providers.claude import ClaudeProvider

        return ClaudeProvider()
    elif name == "opencode":
        from fdsx.providers.opencode import OpenCodeProvider

        return OpenCodeProvider()
    elif name == "codex":
        from fdsx.providers.codex import CodexProvider

        return CodexProvider()
    else:
        raise ValueError(f"Unknown provider: {name}")

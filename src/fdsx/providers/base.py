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
    completion_event: threading.Event | None = None,
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
        completion_event: Optional event that signals logical completion of the
            subprocess stream protocol. When set, initiates an escalating
            termination cascade: wait 5s for voluntary exit → SIGTERM →
            wait 5s → SIGKILL. Collected data is preserved in the result.

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
                process.wait()  # Reap zombie so pipes close and readers unblock

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

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def _read_stdout() -> None:
            if process.stdout:
                try:
                    while True:
                        raw_line = process.stdout.readline()
                        if not raw_line:
                            break
                        line = raw_line.rstrip("\n")
                        stdout_lines.append(line)
                        if output_callback:
                            output_callback(line)
                except BrokenPipeError:
                    pass

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

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stdout_thread.start()
        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        if completion_event is not None:
            # Poll: wait for stdout EOF or completion_event firing, whichever
            # comes first.  A 100ms granularity check avoids a tight spin loop.
            while stdout_thread.is_alive():
                if completion_event.wait(timeout=0.1):
                    break

            if completion_event.is_set():
                # Termination cascade: wait for voluntary exit → SIGTERM → SIGKILL
                try:
                    process.wait(timeout=5)  # 1) Wait for voluntary exit
                except subprocess.TimeoutExpired:
                    logger.debug(
                        "Process pid=%d did not exit voluntarily after completion event;"
                        " sending SIGTERM",
                        process.pid,
                    )
                    try:
                        process.terminate()  # 2) SIGTERM
                    except OSError:
                        pass  # Already dead
                    try:
                        process.wait(timeout=5)  # 3) Wait after SIGTERM
                    except subprocess.TimeoutExpired:
                        logger.debug(
                            "Process pid=%d did not respond to SIGTERM;"
                            " sending SIGKILL",
                            process.pid,
                        )
                        try:
                            process.kill()  # 4) SIGKILL
                        except OSError:
                            pass  # Already dead
                        process.wait()  # 5) Reap zombie
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
            else:
                # stdout EOF reached naturally (process likely exited already)
                stderr_thread.join(timeout=5)
                process.wait()
        elif timeout:
            # When a timeout is configured, wait for the watchdog to finish
            # first (it either returns immediately if the process finished
            # on its own, or kills the process after the timeout).  Then use
            # bounded joins so the main thread is not blocked forever if the
            # reader threads are stuck on readline() after the kill.
            watchdog_thread.join()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            if not killed_by_timeout:
                process.wait()
        else:
            stdout_thread.join()
            stderr_thread.join(timeout=5)
            process.wait()

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

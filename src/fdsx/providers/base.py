import logging
import os
import signal as signal_module
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Commands at or above this byte length are piped via stdin to avoid ARG_MAX limits.
ARG_MAX_STDIN_THRESHOLD = 131072  # 128 KB

# Default inactivity timeout in seconds (5 minutes). When no output is received
# from the subprocess for this duration, the process is killed with exit_code=124.
# Set inactivity_timeout=0 to disable.
DEFAULT_INACTIVITY_TIMEOUT = 300


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
        on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
        summary_callback: Callable[[str], None] | None = None,
    ) -> ProviderResult:
        """Execute a provider.

        Args:
            prompt: The prompt to send (or command for system)
            model: Model name (provider-specific)
            timeout: Timeout in seconds
            command: Command for system provider
            output_callback: Optional callback for streaming stdout lines
            stderr_callback: Optional callback for streaming stderr lines
            on_process_start: Optional callback invoked immediately after
                ``subprocess.Popen()`` creation.  Used by ``SignalHandler`` to
                register active subprocesses for signal forwarding.
            summary_callback: Optional callback for summary lines ([tool: X],
                [thinking] ...) that should be visible even in quiet mode.

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
    env: dict[str, str] | None = None,
    inactivity_timeout: int | None = None,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
    on_inactivity_hooks: Callable[[Callable[[], None], Callable[[], None]], None]
    | None = None,
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
        env: Optional extra environment variables. Merged with os.environ
            so the subprocess inherits the full environment.
        inactivity_timeout: Seconds of silence (no stdout or stderr) before
            the process is killed with exit_code=124. Set to 0 to disable.
            None leaves the feature off (callers opt-in explicitly).
        on_process_start: Optional callback invoked immediately after
            ``subprocess.Popen()`` creation.  Used by ``SignalHandler`` to
            register active subprocesses for signal forwarding.
        on_inactivity_hooks: Optional callback invoked with (suspend_fn, resume_fn)
            when inactivity_timeout is active. Callers can use these to prevent
            false inactivity kills during long-running tool execution. suspend_fn
            stops the inactivity timer; resume_fn restarts it from the current time.

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
        proc_env = {**os.environ, **env} if env else None
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_data is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=proc_env,
            start_new_session=True,
        )

        if on_process_start is not None:
            on_process_start(process)

        killed_by_timeout = False
        killed_by_inactivity = False

        # Shared state for the inactivity watchdog
        _last_activity: list[float] = [time.monotonic()]
        _last_activity_lock = threading.Lock()
        # _suppressed: set when the completion_event path takes control, preventing
        # the inactivity watchdog from issuing a redundant kill.
        _suppressed = threading.Event()
        # _tool_in_progress: set when a tool is running (suspend) to prevent
        # the inactivity watchdog from issuing a false kill.
        _tool_in_progress = threading.Event()

        def _suspend_inactivity() -> None:
            _tool_in_progress.set()

        def _resume_inactivity() -> None:
            _tool_in_progress.clear()
            with _last_activity_lock:
                _last_activity[0] = time.monotonic()

        def _killpg(sig: int) -> None:
            """Send *sig* to the subprocess's process group (best-effort)."""
            try:
                os.killpg(process.pid, sig)
            except OSError:
                pass  # Already dead or no such group

        def _watchdog() -> None:
            nonlocal killed_by_timeout
            if timeout is None:
                return
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                killed_by_timeout = True
                _killpg(signal_module.SIGKILL)
                process.wait()  # Reap zombie so pipes close and readers unblock

        if timeout:
            watchdog_thread = threading.Thread(target=_watchdog, daemon=True)
            watchdog_thread.start()

        def _inactivity_watchdog() -> None:
            nonlocal killed_by_inactivity
            assert inactivity_timeout is not None  # guarded by caller
            while True:
                time.sleep(1)
                if _suppressed.is_set():
                    break
                if process.poll() is not None:
                    break
                if _tool_in_progress.is_set():
                    continue
                with _last_activity_lock:
                    idle = time.monotonic() - _last_activity[0]
                if idle > inactivity_timeout:
                    # Re-check suppressed after acquiring idle measurement
                    if _suppressed.is_set():
                        break
                    killed_by_inactivity = True
                    logger.debug(
                        "Process pid=%d killed due to inactivity timeout after %ds",
                        process.pid,
                        inactivity_timeout,
                    )
                    _killpg(signal_module.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        _killpg(signal_module.SIGKILL)
                        process.wait()  # Reap zombie
                    break

        if inactivity_timeout:
            inactivity_watchdog_thread = threading.Thread(
                target=_inactivity_watchdog, daemon=True
            )
            inactivity_watchdog_thread.start()

        if on_inactivity_hooks is not None and inactivity_timeout:
            on_inactivity_hooks(_suspend_inactivity, _resume_inactivity)

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
                        if inactivity_timeout:
                            with _last_activity_lock:
                                _last_activity[0] = time.monotonic()
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
                        if inactivity_timeout:
                            with _last_activity_lock:
                                _last_activity[0] = time.monotonic()
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
                # Suppress inactivity watchdog — completion_event takes control
                _suppressed.set()
                # Termination cascade: wait for voluntary exit → SIGTERM → SIGKILL
                try:
                    process.wait(timeout=5)  # 1) Wait for voluntary exit
                except subprocess.TimeoutExpired:
                    logger.debug(
                        "Process pid=%d did not exit voluntarily after completion event;"
                        " sending SIGTERM",
                        process.pid,
                    )
                    _killpg(signal_module.SIGTERM)  # 2) SIGTERM the group
                    try:
                        process.wait(timeout=5)  # 3) Wait after SIGTERM
                    except subprocess.TimeoutExpired:
                        logger.debug(
                            "Process pid=%d did not respond to SIGTERM;"
                            " sending SIGKILL",
                            process.pid,
                        )
                        _killpg(signal_module.SIGKILL)  # 4) SIGKILL the group
                        process.wait()  # 5) Reap zombie
                stdout_thread.join(timeout=1)
                stderr_thread.join(timeout=1)
                if inactivity_timeout:
                    inactivity_watchdog_thread.join(timeout=1)
            else:
                # stdout EOF reached naturally (process likely exited already)
                stderr_thread.join(timeout=5)
                process.wait()
                if inactivity_timeout:
                    inactivity_watchdog_thread.join(timeout=1)
        elif timeout:
            # When a timeout is configured, wait for the watchdog to finish
            # first (it either returns immediately if the process finished
            # on its own, or kills the process after the timeout).  Then use
            # bounded joins so the main thread is not blocked forever if the
            # reader threads are stuck on readline() after the kill.
            watchdog_thread.join()
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            if inactivity_timeout:
                inactivity_watchdog_thread.join(timeout=1)
            if not killed_by_timeout:
                process.wait()
        else:
            stdout_thread.join()
            stderr_thread.join(timeout=5)
            process.wait()
            if inactivity_timeout:
                inactivity_watchdog_thread.join(timeout=1)

        if killed_by_timeout:
            return ProviderResult(
                exit_code=124,
                stdout="",
                stderr=f"Command timed out after {timeout} seconds",
            )

        if killed_by_inactivity:
            return ProviderResult(
                exit_code=124,
                stdout="",
                stderr=f"Process killed due to inactivity timeout after {inactivity_timeout} seconds (no output received)",
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
    elif name == "gemini":
        from fdsx.providers.gemini import GeminiOptions, GeminiProvider

        gemini_opts = GeminiOptions.model_validate(options) if options else None
        return GeminiProvider(gemini_opts)
    else:
        raise ValueError(f"Unknown provider: {name}")

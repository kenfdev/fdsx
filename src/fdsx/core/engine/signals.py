"""Signal handler context manager for graceful subprocess cleanup.

Registers SIGINT and SIGTERM handlers during flow execution that:
1. Propagate the signal to all active child subprocesses
2. Wait up to 5 seconds for voluntary exit
3. SIGKILL any surviving subprocesses
4. Release the flow's checkpoint lock (idempotent)
5. Print "Workflow interrupted" to stderr
6. Exit with code 128 + signum
"""

import signal
import subprocess
import sys
import threading
import time
from types import FrameType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fdsx.checkpoint.manager import CheckpointManager

# Exit code base for signal-interrupted processes (POSIX convention)
_SIGNAL_EXIT_BASE = 128

# Seconds to wait for child processes to exit voluntarily after receiving
# the forwarded signal before escalating to SIGKILL.
_GRACEFUL_SHUTDOWN_TIMEOUT = 5.0

# Message printed to stderr when the signal handler fires.
_INTERRUPT_MESSAGE = "\nWorkflow interrupted"


class SignalHandler:
    """Context manager that installs SIGINT/SIGTERM handlers during flow execution.

    Tracks active child subprocesses via ``register_process``.  On signal:
    1. Forwards the signal to all tracked processes.
    2. Waits up to ``_GRACEFUL_SHUTDOWN_TIMEOUT`` seconds for voluntary exit.
    3. SIGKILLs any processes that are still alive.
    4. Releases the checkpoint lock (if a CheckpointManager is provided).
    5. Prints ``_INTERRUPT_MESSAGE`` to stderr.
    6. Calls ``sys.exit(128 + signum)``.

    The ``sys.exit()`` raises ``SystemExit`` (a ``BaseException``), which:
    - Skips ``except Exception`` blocks in run_flow/resume_flow.
    - Hits ``finally`` blocks → lock is released again (idempotent, safe).
    - Propagates to the CLI → process exits with the expected code.

    Usage::

        handler = SignalHandler(checkpoint_manager, thread_id)
        compiled = compile_flow(..., on_process_start=handler.register_process)
        try:
            with handler:
                for state in compiled.graph.stream(...):
                    ...
        finally:
            checkpoint_manager.release_lock(thread_id)  # defense-in-depth
    """

    def __init__(
        self,
        checkpoint_manager: "CheckpointManager | None",
        thread_id: str,
    ) -> None:
        self._checkpoint_manager = checkpoint_manager
        self._thread_id = thread_id
        self._active_processes: set[subprocess.Popen[Any]] = set()
        self._processes_lock = threading.Lock()
        # signal.signal() returns the previous handler, which may be a callable
        # or one of the signal.Handlers enum values (SIG_DFL, SIG_IGN).
        # Use Any to avoid complex typing for the stored previous handler.
        self._prev_sigint: Any = signal.SIG_DFL
        self._prev_sigterm: Any = signal.SIG_DFL

    def register_process(self, proc: subprocess.Popen[Any]) -> None:
        """Register an active subprocess for signal forwarding.

        Called as the ``on_process_start`` callback immediately after
        ``subprocess.Popen()`` creation in ``_run_subprocess``.

        Thread-safe: may be called concurrently from parallel branch executors.

        Args:
            proc: The newly created subprocess.Popen object.
        """
        with self._processes_lock:
            self._active_processes.add(proc)

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:
        """Signal handler: forward signal → wait → SIGKILL → cleanup → exit."""
        with self._processes_lock:
            procs = list(self._active_processes)

        # Step 1: Forward the signal to all active child processes.
        for proc in procs:
            if proc.poll() is None:
                try:
                    proc.send_signal(signum)
                except OSError:
                    pass  # Already dead

        # Step 2: Wait up to _GRACEFUL_SHUTDOWN_TIMEOUT seconds for voluntary exit.
        deadline = time.monotonic() + _GRACEFUL_SHUTDOWN_TIMEOUT
        for proc in procs:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if proc.poll() is None:
                try:
                    proc.wait(timeout=max(0.05, remaining))
                except subprocess.TimeoutExpired:
                    pass

        # Step 3: SIGKILL any processes still alive after the grace period.
        for proc in procs:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass  # Already dead
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass

        # Step 4: Release the checkpoint lock (idempotent).
        if self._checkpoint_manager is not None:
            try:
                self._checkpoint_manager.release_lock(self._thread_id)
            except Exception:
                pass  # Best-effort; the finally block in run_flow handles cleanup

        # Step 5: Print interrupt message to stderr.
        print(_INTERRUPT_MESSAGE, file=sys.stderr)

        # Step 6: Exit with 128 + signum.  Raises SystemExit (BaseException),
        # which is NOT caught by "except Exception" in run_flow/resume_flow.
        # The "finally" block still runs, releasing the lock idempotently.
        sys.exit(_SIGNAL_EXIT_BASE + signum)

    def __enter__(self) -> "SignalHandler":
        """Register custom SIGINT and SIGTERM handlers, saving the previous ones."""
        self._prev_sigint = signal.signal(signal.SIGINT, self._handle_signal)
        self._prev_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)
        return self

    def __exit__(self, *args: Any) -> None:
        """Restore the previous SIGINT and SIGTERM handlers."""
        signal.signal(signal.SIGINT, self._prev_sigint)
        signal.signal(signal.SIGTERM, self._prev_sigterm)

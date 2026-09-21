"""Execution-scoped cooperative cancellation, inherited by child task contexts."""

import subprocess
from collections.abc import Callable
from contextvars import ContextVar
from threading import Event


class ExecutionInterrupted(KeyboardInterrupt):
    """Stop execution without turning interruption into an item failure."""


class Cancellation:
    def __init__(self, register: Callable[[subprocess.Popen[str]], None]) -> None:
        self.stopped = Event()
        self.register = register
        self.system_exit: SystemExit | None = None

    def interrupt(self, error: KeyboardInterrupt | SystemExit) -> None:
        if isinstance(error, SystemExit):
            self.system_exit = error
        self.stopped.set()

    def check(self) -> None:
        if self.stopped.is_set():
            if self.system_exit is not None:
                raise SystemExit(self.system_exit.code)
            raise ExecutionInterrupted()


current_cancellation: ContextVar[Cancellation | None] = ContextVar(
    "execution_cancellation", default=None
)


def check_cancelled() -> None:
    cancellation = current_cancellation.get()
    if cancellation is not None:
        cancellation.check()

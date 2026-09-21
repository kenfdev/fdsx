"""Execution-local attempt observation, including tasks inside local graphs."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_observer: ContextVar[Callable[[], None] | None] = ContextVar(
    "attempt_observer", default=None
)


@contextmanager
def observe_attempts(callback: Callable[[], None]) -> Iterator[None]:
    token = _observer.set(callback)
    try:
        yield
    finally:
        _observer.reset(token)


def record_attempt() -> None:
    callback = _observer.get()
    if callback is not None:
        callback()

"""Context-local suppression of raw adapter diagnostics for private callers."""

from contextvars import ContextVar
from typing import Any

private_diagnostics: ContextVar[bool] = ContextVar(
    "private_provider_diagnostics", default=False
)


class PrivateAwareLogger:
    """Keep ordinary task logging unchanged, including concurrent task calls."""

    def __init__(self, logger: Any):
        self.logger = logger

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.logger, name)

        def emit(*args: Any, **kwargs: Any) -> Any:
            if not private_diagnostics.get():
                return method(*args, **kwargs)
            return None

        return emit


class PrivateStreamGuard:
    """Carry malformed private stream failures back from the reader thread."""

    def __init__(self) -> None:
        self.enabled = private_diagnostics.get()
        self.failed = False

    def wrap(self, callback: Any) -> Any:
        def receive(line: str) -> None:
            if not self.enabled:
                callback(line)
                return
            if self.failed or not line.strip():
                return
            try:
                import json

                if not isinstance(json.loads(line), dict):
                    self.failed = True
                    return
                callback(line)
            except (AttributeError, TypeError, KeyError, ValueError, RecursionError):
                self.failed = True

        return receive

    def check(self) -> None:
        if self.failed:
            from fdsx.providers.base import ProviderError

            raise ProviderError("private provider stream is malformed")

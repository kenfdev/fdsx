"""Domain errors exposed by the engine seam."""


class EngineError(RuntimeError):
    """Base class for engine failures callers may handle programmatically."""


class CheckpointNotFoundError(EngineError):
    """Raised when a requested thread has no durable checkpoint."""


class RunLockedError(EngineError):
    """Raised when another process owns the requested thread."""


class FlowExecutionError(EngineError):
    """Raised when a flow attempt fails unexpectedly during execution."""

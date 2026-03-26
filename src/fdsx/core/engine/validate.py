"""Flow validation utilities for the engine package."""

from pathlib import Path

from fdsx.core.loader import load_flow


class FlowValidationError(Exception):
    """Raised when flow validation fails."""

    pass


def validate_flow(flow_path: Path) -> tuple[bool, list[str], str | None]:
    """Validate a flow without executing it.

    Args:
        flow_path: Path to the YAML workflow file

    Returns:
        tuple of (is_valid, list of error messages, flow_name or None)
    """
    flow, errors = load_flow(flow_path)
    return flow is not None, errors, flow.name if flow else None

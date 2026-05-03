"""Flow validation utilities for the engine package."""

from pathlib import Path

from fdsx.core.config import load_config
from fdsx.core.loader import load_flow


class FlowValidationError(Exception):
    """Raised when flow validation fails."""

    pass


class FailStateTermination(Exception):
    """Raised when a FailState is reached during flow execution."""

    def __init__(self, state_name: str, error: str, cause: str) -> None:
        super().__init__(f"Fail state '{state_name}': {error} — {cause}")
        self.state_name = state_name
        self.error = error
        self.cause = cause


def validate_flow(flow_path: Path) -> tuple[bool, list[str], str | None]:
    """Validate a flow without executing it.

    Args:
        flow_path: Path to the YAML workflow file

    Returns:
        tuple of (is_valid, list of error messages, flow_name or None)
    """
    config = load_config()
    config_profiles = None
    if config.profiles:
        config_profiles = {
            name: prof.model_dump() for name, prof in config.profiles.items()
        }

    flow, errors = load_flow(flow_path, config_profiles=config_profiles)
    return flow is not None, errors, flow.name if flow else None

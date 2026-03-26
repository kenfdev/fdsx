"""Profile resolution for task steps.

Profiles are named provider/model configuration bundles that can be referenced
in workflow YAML files instead of repeating provider/model fields on each task.

This module handles:
1. Merging profiles from config and workflow levels
2. Resolving profile references into provider/model/provider_options in task states
3. Validating XOR constraint (profile vs explicit provider/model)
"""

from typing import Any


def merge_profiles(
    config_profiles: dict[str, dict[str, Any]] | None,
    workflow_profiles: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    """Merge config-level and workflow-level profiles.

    Workflow-level profiles override config-level profiles (full replacement per name,
    not deep merge).

    Args:
        config_profiles: Profiles from config file (lower priority).
        workflow_profiles: Profiles from workflow YAML (higher priority).

    Returns:
        Merged profile dictionary.
    """
    if config_profiles is None:
        config_profiles = {}
    if workflow_profiles is None:
        workflow_profiles = {}

    result = dict(config_profiles)
    result.update(workflow_profiles)
    return result


def resolve_profiles_in_flow(
    data: dict[str, Any],
    config_profiles: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Resolve profile references in task states to provider/model fields.

    This operates on raw YAML dicts BEFORE Pydantic validation. This is required
    because TaskState.provider is a required field (Field(...)), so tasks with
    only `profile:` but no `provider:` would fail Pydantic validation if not
    resolved first.

    Args:
        data: Raw YAML workflow dict (before Flow(**data)).
        config_profiles: Config-level profiles (optional, merged with workflow profiles).

    Returns:
        Tuple of (modified_data, errors). The data dict is mutated in-place.
        Errors contains XOR validation failures.
    """
    errors: list[str] = []

    workflow_profiles = data.get("profiles")

    if workflow_profiles is not None and not isinstance(workflow_profiles, dict):
        return data, ["'profiles' must be a YAML mapping, not a list or scalar"]

    merged_profiles = merge_profiles(config_profiles, workflow_profiles)

    states = data.get("states", {})
    if not isinstance(states, dict):
        return data, ["states must be a dict"]

    for state_name, state_data in states.items():
        if not isinstance(state_data, dict):
            continue

        if state_data.get("type") != "task":
            continue

        has_profile = "profile" in state_data
        has_provider = "provider" in state_data
        has_model = "model" in state_data

        if has_profile and (has_provider or has_model):
            errors.append(
                f"State '{state_name}': profile and (provider|model) are mutually exclusive. "
                f"Use either profile reference or explicit provider/model, not both."
            )
            continue

        if not has_profile:
            continue

        profile_name = state_data["profile"]
        if profile_name not in merged_profiles:
            errors.append(
                f"State '{state_name}': profile '{profile_name}' not found in profiles. "
                f"Available profiles: {list(merged_profiles.keys())}"
            )
            continue

        profile = merged_profiles[profile_name]
        if not isinstance(profile, dict):
            errors.append(
                f"State '{state_name}': profile '{profile_name}' must be a dict"
            )
            continue

        provider = profile.get("provider")
        model = profile.get("model")

        if provider is not None:
            state_data["provider"] = provider

        if model is not None:
            state_data["model"] = model

        extra_fields = {
            k: v for k, v in profile.items() if k not in ("provider", "model")
        }
        if extra_fields:
            state_data["provider_options"] = extra_fields

        del state_data["profile"]

    return data, errors

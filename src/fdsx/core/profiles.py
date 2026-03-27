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


def _resolve_profile_on_dict(
    item: dict[str, Any],
    label: str,
    merged_profiles: dict[str, dict[str, Any]],
) -> list[str]:
    """Resolve profile reference on a single dict (task or branch item).

    Operates on raw YAML dicts BEFORE Pydantic validation.

    Args:
        item: The dict to modify in-place (task state or branch dict).
        label: Human-readable label for error messages (e.g., "State 'X'" or "State 'X', branch Y").
        merged_profiles: Merged profiles dictionary.

    Returns:
        List of error strings.
    """
    errors: list[str] = []

    has_profile = "profile" in item
    has_provider = "provider" in item
    has_model = "model" in item

    if has_profile and (has_provider or has_model):
        errors.append(
            f"{label}: profile and (provider|model) are mutually exclusive. "
            f"Use either profile reference or explicit provider/model, not both."
        )
        return errors

    if not has_profile:
        return errors

    profile_name = item["profile"]
    if profile_name not in merged_profiles:
        errors.append(
            f"{label}: profile '{profile_name}' not found in profiles. "
            f"Available profiles: {list(merged_profiles.keys())}. "
            f"Define profiles in workflow YAML, project config (.fdsx/config.yaml), or global config (~/.config/fdsx/config.yaml)."
        )
        return errors

    profile = merged_profiles[profile_name]

    provider = profile.get("provider")
    model = profile.get("model")

    if provider is not None:
        item["provider"] = provider

    if model is not None:
        item["model"] = model

    extra_fields = {k: v for k, v in profile.items() if k not in ("provider", "model")}
    if extra_fields:
        item["provider_options"] = extra_fields

    del item["profile"]

    return errors


def _resolve_fallback_profile(
    item: dict[str, Any],
    label: str,
    merged_profiles: dict[str, dict[str, Any]],
) -> list[str]:
    """Resolve profile reference on extract.fallback dict.

    Operates on raw YAML dicts BEFORE Pydantic validation.

    Args:
        item: The dict to modify in-place (task state or branch dict).
        label: Human-readable label for error messages.
        merged_profiles: Merged profiles dictionary.

    Returns:
        List of error strings.
    """
    errors: list[str] = []

    extract = item.get("extract")
    if not isinstance(extract, dict):
        return errors

    fallback = extract.get("fallback")
    if not isinstance(fallback, dict):
        return errors

    if "profile" not in fallback:
        return errors

    fallback_errors = _resolve_profile_on_dict(
        fallback,
        f"{label}, extract.fallback",
        merged_profiles,
    )
    errors.extend(fallback_errors)
    return errors


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

        if state_data.get("type") == "task":
            state_errors = _resolve_profile_on_dict(
                state_data,
                f"State '{state_name}'",
                merged_profiles,
            )
            errors.extend(state_errors)
            fallback_errors = _resolve_fallback_profile(
                state_data,
                f"State '{state_name}'",
                merged_profiles,
            )
            errors.extend(fallback_errors)
        elif state_data.get("type") == "parallel":
            for branch_idx, branch in enumerate(state_data.get("branches", [])):
                if not isinstance(branch, dict):
                    continue
                branch_errors = _resolve_profile_on_dict(
                    branch,
                    f"State '{state_name}', branch {branch_idx}",
                    merged_profiles,
                )
                errors.extend(branch_errors)
                fallback_errors = _resolve_fallback_profile(
                    branch,
                    f"State '{state_name}', branch {branch_idx}",
                    merged_profiles,
                )
                errors.extend(fallback_errors)

    return data, errors


def resolve_profiles_in_config(
    data: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Resolve profile references in task_splitter and workflow_selector config.

    Operates on raw YAML dicts BEFORE Pydantic validation.

    Args:
        data: Raw merged config dict (before FdsxConfig.model_validate()).

    Returns:
        Tuple of (modified_data, errors). The data dict is mutated in-place.
    """
    errors: list[str] = []

    profiles = data.get("profiles")
    if profiles is None or not isinstance(profiles, dict):
        profiles = {}

    for config_key in ("task_splitter", "workflow_selector"):
        config_item = data.get(config_key)
        if not isinstance(config_item, dict):
            continue

        if "profile" not in config_item or config_item["profile"] is None:
            continue

        config_errors = _resolve_profile_on_dict(
            config_item,
            f"config.{config_key}",
            profiles,
        )
        errors.extend(config_errors)

    return data, errors

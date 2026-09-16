import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import SchemaError
from jsonschema.validators import validator_for

from fdsx.core.profiles import resolve_profiles_in_flow
from fdsx.core.variables import analyze_variable_references
from fdsx.models.flow import Flow, ParallelState, TaskState


def load_flow(
    path: Path,
    input_keys: set[str] | None = None,
    config_profiles: dict[str, dict[str, Any]] | None = None,
) -> tuple[Flow | None, list[str]]:
    """Load and validate a flow from a YAML file.

    Args:
        path: Path to the YAML workflow file
        input_keys: Optional set of CLI --input variable keys known at runtime
        config_profiles: Optional config-level profiles for resolution

    Returns:
        tuple of (Flow or None, list of error messages)
    """
    if not path.exists():
        return None, [f"File not found: {path}"]

    try:
        with path.open() as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        return None, [f"Invalid YAML: {e}"]

    if data is None:
        return None, ["Empty YAML file"]

    if not isinstance(data, dict):
        return None, ["Workflow file must be a YAML mapping, not a list or scalar"]

    flow, flow_errors = _parse_and_validate_flow(data, path, config_profiles)
    if flow_errors:
        return None, flow_errors

    if flow is None:
        return None, ["Failed to parse flow"]

    var_errors = analyze_variable_references(flow, input_keys=input_keys)

    if var_errors:
        return None, var_errors

    return flow, []


def _parse_and_validate_flow(
    data: dict[str, Any],
    yaml_path: Path,
    config_profiles: dict[str, dict[str, Any]] | None = None,
) -> tuple[Flow | None, list[str]]:
    """Parse raw YAML data into Flow model and validate."""
    errors: list[str] = []

    if "description" not in data or not data.get("description"):
        return None, [
            "Missing required field 'description'. "
            "Please add a description to your workflow file, e.g.:\n"
            "  description: 'My workflow that does X and Y'"
        ]

    data, profile_errors = resolve_profiles_in_flow(data, config_profiles)
    if profile_errors:
        return None, profile_errors

    try:
        flow = Flow(**data)
    except Exception as e:
        return None, [f"Validation error: {e}"]

    flow, resolve_errors = _resolve_prompt_files(flow, yaml_path)
    if resolve_errors:
        return None, resolve_errors

    schema_errors = _resolve_structured_output_schemas(flow, yaml_path)
    if schema_errors:
        return None, schema_errors

    return flow, errors


def _resolve_structured_output_schemas(flow: Flow, yaml_path: Path) -> list[str]:
    """Load and validate structured-output schemas relative to the workflow."""
    errors: list[str] = []
    yaml_dir = yaml_path.parent.resolve()
    contracts: list[tuple[str, Any]] = []
    for state_name, state in flow.states.items():
        if isinstance(state, TaskState) and state.structured_output is not None:
            contracts.append((f"State '{state_name}'", state.structured_output))
        elif isinstance(state, ParallelState):
            for index, branch in enumerate(state.branches):
                if branch.structured_output is not None:
                    contracts.append(
                        (
                            f"Parallel state '{state_name}' branch {index}",
                            branch.structured_output,
                        )
                    )

    for context, contract in contracts:
        raw_path = Path(contract.schema_path)
        if raw_path.is_absolute():
            errors.append(f"{context}: schema must be a relative path")
            continue
        schema_path = (yaml_dir / raw_path).resolve()
        try:
            schema_path.relative_to(yaml_dir)
        except ValueError:
            errors.append(f"{context}: schema path escapes workflow directory")
            continue
        if not schema_path.is_file():
            errors.append(f"{context}: schema not found: {contract.schema_path}")
            continue
        try:
            with schema_path.open() as schema_file:
                document = json.load(schema_file)
            validator_class = validator_for(document)
            validator_class.check_schema(document)
        except (OSError, json.JSONDecodeError, SchemaError, TypeError) as exc:
            errors.append(f"{context}: invalid schema '{contract.schema_path}': {exc}")
            continue
        contract.schema_document = document
    return errors


def _validate_prompt_file_path(
    raw_path: str, prompt_path: Path, yaml_dir: Path, context: str
) -> str | None:
    """Validate that a prompt_file path is relative and stays within the workflow directory.

    Returns an error string, or None if the path is safe.
    """
    if Path(raw_path).is_absolute():
        return (
            f"{context}: prompt_file must be a relative path, got absolute: {raw_path}"
        )
    resolved_dir = yaml_dir.resolve()
    try:
        prompt_path.relative_to(resolved_dir)
    except ValueError:
        return f"{context}: prompt_file path escapes workflow directory: {raw_path}"
    return None


def _resolve_prompt_files(flow: Flow, yaml_path: Path) -> tuple[Flow, list[str]]:
    """Resolve prompt_file paths relative to YAML location.

    Returns:
        tuple of (Flow or original flow if errors, list of error messages)
    """
    yaml_dir = yaml_path.parent

    import copy

    flow_dict = copy.deepcopy(flow.model_dump())
    errors: list[str] = []

    for state_name, state_data in flow_dict.get("states", {}).items():
        if state_data.get("type") == "task":
            if state_data.get("prompt_file"):
                prompt_path = (yaml_dir / state_data["prompt_file"]).resolve()
                path_error = _validate_prompt_file_path(
                    state_data["prompt_file"],
                    prompt_path,
                    yaml_dir,
                    f"State '{state_name}'",
                )
                if path_error:
                    errors.append(path_error)
                    continue
                if not prompt_path.exists():
                    errors.append(
                        f"State '{state_name}': prompt_file not found: {state_data['prompt_file']}"
                    )
                    continue
                try:
                    with prompt_path.open() as f:
                        state_data["prompt_template"] = f.read()
                    del state_data["prompt_file"]
                except Exception as e:
                    errors.append(
                        f"State '{state_name}': failed to read prompt_file: {e}"
                    )
        elif state_data.get("type") == "parallel":
            for branch_idx, branch in enumerate(state_data.get("branches", [])):
                if branch.get("prompt_file"):
                    prompt_path = (yaml_dir / branch["prompt_file"]).resolve()
                    path_error = _validate_prompt_file_path(
                        branch["prompt_file"],
                        prompt_path,
                        yaml_dir,
                        f"Parallel branch {branch_idx}",
                    )
                    if path_error:
                        errors.append(path_error)
                        continue
                    if not prompt_path.exists():
                        errors.append(
                            f"Parallel branch {branch_idx}: prompt_file not found: {branch['prompt_file']}"
                        )
                        continue
                    try:
                        with prompt_path.open() as f:
                            branch["prompt_template"] = f.read()
                        del branch["prompt_file"]
                    except Exception as e:
                        errors.append(
                            f"Parallel branch {branch_idx}: failed to read prompt_file: {e}"
                        )
        elif state_data.get("type") == "map":
            for iter_state in state_data.get("iterator", {}).get("states", []):
                if iter_state.get("prompt_file"):
                    prompt_path = (yaml_dir / iter_state["prompt_file"]).resolve()
                    path_error = _validate_prompt_file_path(
                        iter_state["prompt_file"],
                        prompt_path,
                        yaml_dir,
                        f"Map '{state_name}' iterator state '{iter_state['name']}'",
                    )
                    if path_error:
                        errors.append(path_error)
                        continue
                    if not prompt_path.exists():
                        errors.append(
                            f"Map '{state_name}' iterator state '{iter_state['name']}': "
                            f"prompt_file not found: {iter_state['prompt_file']}"
                        )
                        continue
                    try:
                        with prompt_path.open() as f:
                            iter_state["prompt_template"] = f.read()
                        del iter_state["prompt_file"]
                    except Exception as e:
                        errors.append(
                            f"Map '{state_name}' iterator state '{iter_state['name']}': "
                            f"failed to read prompt_file: {e}"
                        )

    if errors:
        return flow, errors

    try:
        return Flow(**flow_dict), []
    except Exception as e:
        return flow, [f"Failed to re-validate flow after prompt_file resolution: {e}"]


def validate_flow(path: Path) -> tuple[bool, list[str]]:
    """Validate a flow without executing it.

    Returns:
        tuple of (is_valid, list of error messages)
    """
    flow, errors = load_flow(path)
    return flow is not None, errors

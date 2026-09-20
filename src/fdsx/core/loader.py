import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from jsonschema import SchemaError
from jsonschema.validators import validator_for
from pydantic import ValidationError

from fdsx.core.definitions import raw_definitions, walk_states
from fdsx.core.profiles import resolve_profiles_in_flow
from fdsx.core.variables import analyze_variable_references
from fdsx.models.classifier import ClassifierDefinition
from fdsx.models.flow import (
    Branch,
    ClassifierBranch,
    ClassifierState,
    Flow,
    IteratorDef,
    MapState,
    ParallelState,
    TaskState,
)

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig


def load_flow(
    path: Path,
    input_keys: set[str] | None = None,
    config_profiles: dict[str, dict[str, Any]] | None = None,
    config: "FdsxConfig | None" = None,
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

    from fdsx.core.session_forks import validate_session_forks

    fork_errors = validate_session_forks(flow, config)
    if fork_errors:
        return None, fork_errors

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
    except ValidationError as e:
        return None, [
            "Validation error at "
            + ".".join(str(part) for part in error["loc"])
            + ": "
            + error["msg"]
            for error in e.errors(include_input=False, include_context=False)
        ]

    flow, resolve_errors = _resolve_prompt_files(flow, yaml_path)
    if resolve_errors:
        return None, resolve_errors

    schema_errors = _resolve_structured_output_schemas(flow, yaml_path)
    if schema_errors:
        return None, schema_errors

    from fdsx.core.evaluation_schema import (
        EvaluationSchemaError,
        compile_evaluation_output,
    )
    from fdsx.providers.base import get_provider

    classifiers: list[ClassifierDefinition] = [
        state for _, state in walk_states(flow) if isinstance(state, ClassifierState)
    ]
    classifiers.extend(
        branch
        for state in flow.states.values()
        if isinstance(state, ParallelState)
        for branch in state.branches
        if isinstance(branch, ClassifierBranch)
    )
    for classifier in classifiers:
        fallback = classifier.fallback
        if fallback is not None:
            try:
                get_provider(fallback.provider, fallback.provider_options)
            except ValueError:
                errors.append("classifier fallback provider options are invalid")

    for name, state in walk_states(flow):
        if isinstance(state, TaskState) and state.provider == "jev":
            place = f"states.{name}"
            contract = state.structured_output
            if contract is None:
                errors.append(f"{place}: Jev requires structured_output")
                continue
            if flow.providers and flow.providers.get("jev"):
                errors.append(
                    f"{place}: Jev does not support workflow provider options"
                )
            if not state.model or not state.model.strip():
                errors.append(f"{place}: Jev requires a nonblank model")
            if contract.merge is not None:
                errors.append(f"{place}: Jev does not support structured_output.merge")
            for option in (
                "fork_from",
                "timeout_seconds",
                "provider_options",
                "result_file",
            ):
                if getattr(state, option) is not None:
                    errors.append(f"{place}: Jev does not support {option}")
            if "retry" in state.model_fields_set and state.retry != 0:
                errors.append(f"{place}: Jev requires retry: 0 when explicitly set")
            try:
                compile_evaluation_output(contract.schema_document, location=place)
            except EvaluationSchemaError as exc:
                errors.append(str(exc))
        elif isinstance(state, ParallelState):
            for index, branch in enumerate(state.branches):
                if isinstance(branch, Branch) and branch.provider == "jev":
                    errors.append(
                        f"states.{name}.branches.{index}: nested Jev requires the local workflow form"
                    )
        elif isinstance(state, MapState) and isinstance(state.iterator, IteratorDef):
            for task in state.iterator.states:
                if task.provider == "jev":
                    errors.append(
                        f"states.{name}.iterator.{task.name}: nested Jev requires the local workflow form"
                    )
    if errors:
        return None, errors
    return flow, errors


def _resolve_structured_output_schemas(flow: Flow, yaml_path: Path) -> list[str]:
    """Load and validate structured-output schemas relative to the workflow."""
    errors: list[str] = []
    yaml_dir = yaml_path.parent.resolve()
    contracts: list[tuple[str, Any]] = []
    for state_name, state in walk_states(flow):
        if isinstance(state, TaskState) and state.structured_output is not None:
            contracts.append((f"State '{state_name}'", state.structured_output))
        elif isinstance(state, ParallelState):
            for index, branch in enumerate(state.branches):
                if isinstance(branch, Branch) and branch.structured_output is not None:
                    contracts.append(
                        (
                            f"Parallel state '{state_name}' branch {index}",
                            branch.structured_output,
                        )
                    )

        elif isinstance(state, MapState) and isinstance(state.iterator, IteratorDef):
            for task in state.iterator.states:
                if task.structured_output is not None:
                    contracts.append(
                        (
                            f"Map state '{state_name}' task '{task.name}'",
                            task.structured_output,
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
        from fdsx.core.evaluation_schema import (
            EvaluationSchemaError,
            prepare_evaluation_provider_schema,
        )

        try:
            prepare_evaluation_provider_schema(document)
        except EvaluationSchemaError as exc:
            errors.append(f"{context}: {exc}")
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

    for state_name, state_data in raw_definitions(flow_dict):
        raw_path = state_data.get("prompt_file")
        if not raw_path:
            continue
        prompt_path = (yaml_dir / raw_path).resolve()
        context = state_name
        path_error = _validate_prompt_file_path(
            raw_path, prompt_path, yaml_dir, context
        )
        if path_error:
            errors.append(path_error)
            continue
        if not prompt_path.is_file():
            errors.append(f"{context}: prompt_file not found: {raw_path}")
            continue
        try:
            state_data["prompt_template"] = prompt_path.read_text()
            del state_data["prompt_file"]
        except (OSError, UnicodeError) as error:
            errors.append(f"{context}: failed to read prompt_file: {error}")

    if errors:
        return flow, errors

    try:
        return Flow(**flow_dict), []
    except ValidationError as e:
        return flow, [
            "Validation error at "
            + ".".join(str(part) for part in error["loc"])
            + ": "
            + error["msg"]
            for error in e.errors(include_input=False, include_context=False)
        ]


def validate_flow(path: Path) -> tuple[bool, list[str]]:
    """Validate a flow without executing it.

    Returns:
        tuple of (is_valid, list of error messages)
    """
    flow, errors = load_flow(path)
    return flow is not None, errors

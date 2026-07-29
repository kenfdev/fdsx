"""Recovery-jump validation and checkpoint update helpers."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from fdsx.core.compiler.helpers import _merge_provider_options
from fdsx.core.paths import parse_jsonpath
from fdsx.core.variables import (
    extract_template_references,
    jsonpath_exists,
)
from fdsx.models.flow import (
    ChoiceState,
    FailState,
    Flow,
    MapState,
    ParallelState,
    PassState,
    TaskState,
    WaitState,
)

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig

logger = structlog.get_logger(__name__)
_RECOVERY_RUNTIME_VARS = {"run_path", "state.iteration"}


class RecoveryValidationError(RuntimeError):
    """Raised when an explicit recovery jump is not safe to execute."""


class RecoveryStateRequiredError(RecoveryValidationError):
    """Raised when a terminal workflow needs an explicit recovery target."""


def eligible_recovery_states(
    flow: Flow,
    run_log: dict[str, Any],
) -> list[str]:
    """Return executed user-defined states that can be recovery targets."""
    eligible: list[str] = []
    seen: set[str] = set()
    for entry in run_log.get("states", []):
        state_name = entry.get("name")
        if not isinstance(state_name, str) or state_name in seen:
            continue
        state = flow.states.get(state_name)
        if state is None or isinstance(state, FailState):
            continue
        seen.add(state_name)
        eligible.append(state_name)
    return eligible


def validate_recovery_request(
    flow: Flow,
    run_log: dict[str, Any],
    from_state: str,
    state_values: dict[str, Any],
    config: "FdsxConfig | None" = None,
) -> None:
    """Validate the stable eligibility rules for a recovery target."""
    if run_log.get("status") == "completed":
        raise RecoveryValidationError(
            "Cannot recover a workflow that already completed successfully"
        )

    state = flow.states.get(from_state)
    if state is None:
        raise RecoveryValidationError(
            f"Recovery state '{from_state}' does not exist in the current workflow"
        )
    if isinstance(state, FailState):
        raise RecoveryValidationError(
            f"Fail state '{from_state}' cannot be used as a recovery target"
        )

    eligible = eligible_recovery_states(flow, run_log)
    if from_state not in eligible:
        candidates = ", ".join(eligible) if eligible else "none"
        raise RecoveryValidationError(
            f"Recovery state '{from_state}' was not executed in this thread. "
            f"Eligible states: {candidates}"
        )

    missing = [
        path
        for path in sorted(_required_state_inputs(flow, from_state, state, config))
        if path not in _RECOVERY_RUNTIME_VARS
        and not jsonpath_exists(path, state_values)
    ]
    if missing:
        raise RecoveryValidationError(
            f"Cannot recover from state '{from_state}': missing required "
            f"variables: {', '.join(missing)}"
        )


def recovery_state_required_message(
    flow: Flow,
    run_log: dict[str, Any],
) -> str:
    """Build the diagnostic shown when terminal recovery needs a target."""
    eligible = eligible_recovery_states(flow, run_log)
    candidates = ", ".join(eligible) if eligible else "none"
    return (
        "This workflow requires an explicit recovery state. "
        f"Eligible states: {candidates}. "
        "Resume with --from <state>."
    )


def build_recovery_update(
    flow: Flow,
    state_values: dict[str, Any],
) -> dict[str, Any]:
    """Build the control-state reset applied before a recovery jump."""
    existing_meta = state_values.get("_meta", {})
    reset_meta = {
        key: value
        for key, value in existing_meta.items()
        if key not in {"terminal_failure", "terminal_status"}
    }
    update: dict[str, Any] = {
        "_meta": reset_meta,
        "_state_iterations": {},
    }
    for state_name, state in flow.states.items():
        if isinstance(state, ParallelState):
            update[f"_br_{state_name}"] = []
    return update


def reset_recovery_progress(
    flow: Flow,
    state_values: dict[str, Any],
) -> None:
    """Remove map continuation scratch files before an explicit recovery."""
    run_dir = state_values.get("_meta", {}).get("run_dir")
    if not isinstance(run_dir, str) or not run_dir:
        return
    run_path = Path(run_dir).resolve()
    for state_name, state in flow.states.items():
        if not isinstance(state, MapState):
            continue
        progress_path = (run_path / state_name / "progress.json").resolve()
        try:
            progress_path.relative_to(run_path)
            progress_path.unlink(missing_ok=True)
        except (OSError, ValueError) as error:
            logger.error(
                "recovery_progress_reset_failed",
                state=state_name,
                path=str(progress_path),
                error=str(error),
            )
            raise RecoveryValidationError(
                f"Could not reset map progress for state '{state_name}'"
            ) from error


def _provider_option_references(options: dict[str, Any] | None) -> set[str]:
    if not options:
        return set()
    return extract_template_references(
        *(
            value
            for key in (
                "system_prompt",
                "append_system_prompt",
                "developer_instructions",
            )
            if isinstance((value := options.get(key)), str)
        )
    )


def _clean_path(path: str) -> str:
    return path[2:] if path.startswith("$.") else path


def _path_is_provided(path: str, provided_paths: set[str]) -> bool:
    path_parts = parse_jsonpath(path)
    for provided in provided_paths:
        provided_parts = parse_jsonpath(_clean_path(provided))
        common_length = min(len(path_parts), len(provided_parts))
        if path_parts[:common_length] == provided_parts[:common_length]:
            return True
    return False


def _effective_provider_options(
    flow: Flow,
    state_name: str,
    provider: str,
    local_options: dict[str, Any] | None,
    config: "FdsxConfig | None",
) -> dict[str, Any] | None:
    return _merge_provider_options(
        config,
        flow,
        provider,
        local_options,
        state_name=state_name,
    )


def _required_state_inputs(
    flow: Flow,
    state_name: str,
    state: Any,
    config: "FdsxConfig | None",
) -> set[str]:
    if isinstance(state, TaskState):
        options = _effective_provider_options(
            flow,
            state_name,
            state.provider,
            state.provider_options,
            config,
        )
        return extract_template_references(
            state.prompt_template,
            state.command,
        ) | _provider_option_references(options)
    if isinstance(state, ParallelState):
        references: set[str] = set()
        for branch in state.branches:
            options = _effective_provider_options(
                flow,
                state_name,
                branch.provider,
                branch.provider_options,
                config,
            )
            references.update(
                extract_template_references(
                    branch.prompt_template,
                    branch.command,
                )
                | _provider_option_references(options)
            )
        return references
    if isinstance(state, ChoiceState):
        return {_clean_path(choice.variable) for choice in state.choices}
    if isinstance(state, PassState):
        references = extract_template_references(
            *(
                value
                for value in (state.parameters or {}).values()
                if isinstance(value, str)
            )
        )
        if state.aggregate is not None:
            references.add(_clean_path(state.aggregate.source))
        return references
    if isinstance(state, MapState):
        references = {_clean_path(state.items_path)}
        iterator_outputs: set[str] = set()
        for iterator_state in state.iterator.states:
            options = _effective_provider_options(
                flow,
                f"{state_name}.{iterator_state.name}",
                iterator_state.provider,
                iterator_state.provider_options,
                config,
            )
            iterator_references = extract_template_references(
                iterator_state.prompt_template,
                iterator_state.command,
            ) | _provider_option_references(options)
            references.update(
                reference
                for reference in iterator_references
                if reference != "item"
                and not reference.startswith("item.")
                and not _path_is_provided(reference, iterator_outputs)
            )
            iterator_outputs.add(_clean_path(iterator_state.result_path))
            if iterator_state.extract is not None:
                iterator_outputs.add(_clean_path(iterator_state.extract.result_path))
        return references
    if isinstance(state, WaitState):
        references = extract_template_references(state.message)
        if state.notify is not None:
            references.update(
                extract_template_references(state.notify.webhook.template)
            )
        return references
    return set()

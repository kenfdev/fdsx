"""Local-only evaluation preflight shared by fresh and resumed executions."""

import os
from collections.abc import Iterable
from pathlib import Path

import structlog

from fdsx.core.config import load_config
from fdsx.core.graph_utils import get_next_states
from fdsx.core.loader import load_flow
from fdsx.models.flow import EvaluateState, Flow, ParallelState, TaskState, WaitState

from .validate import FlowValidationError

log = structlog.get_logger(__name__)


def validate_evaluation_key(flow: Flow, starts: Iterable[str] | None = None) -> None:
    # Fresh execution checks the entire definition, including unreachable states.
    pending = list(flow.states if starts is None else starts)
    visited: set[str] = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        state = flow.states.get(name)
        if state is None:
            # LangGraph splits waits and parallels into internal nodes.
            # Resolve these through the corresponding logical state.
            for logical, definition in flow.states.items():
                if (
                    isinstance(definition, WaitState) and name == f"_{logical}_int"
                ) or (
                    isinstance(definition, ParallelState)
                    and name in {f"_branch_{logical}", f"_collect_{logical}"}
                ):
                    pending.append(logical)
            continue
        if (
            isinstance(state, EvaluateState)
            or (isinstance(state, TaskState) and state.provider == "jev")
        ) and not os.environ.get("TYPESAFE_API_KEY", "").strip():
            log.error("evaluation_configuration_missing", state=name)
            raise FlowValidationError(f"State '{name}': TYPESAFE_API_KEY is required")
        pending.extend(get_next_states(state) - visited)


def validate_evaluation_file(
    path: Path, *, base_dir: Path | None, input_keys: set[str]
) -> None:
    config = load_config(project_dir=base_dir.parent if base_dir is not None else None)
    profiles = (
        {name: value.model_dump() for name, value in config.profiles.items()}
        if config.profiles
        else None
    )
    flow, errors = load_flow(
        path, input_keys=input_keys, config_profiles=profiles, config=config
    )
    if flow is None:
        log.error("evaluation_preflight_invalid")
        raise FlowValidationError("Flow validation failed: " + "; ".join(errors))
    validate_evaluation_key(flow)

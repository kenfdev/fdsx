"""Helper utilities for the compiler package."""

from typing import TYPE_CHECKING, Annotated, Any, TypedDict

import structlog
from langgraph.managed import RemainingSteps

from fdsx.core.config import _deep_merge
from fdsx.models.flow import (
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


def _top_level_key(path: str) -> str | None:
    """Extract the top-level key from a JSONPath like '$.reviews' → 'reviews'."""
    if path.startswith("$."):
        path = path[2:]
    if not path:
        return None
    return path.split(".")[0].split("[")[0] or None


def _parallel_branch_reducer(current: list[Any], update: list[Any]) -> list[Any]:
    """Reducer for parallel branch results that supports reset.

    Branch nodes return ``[result]`` which appends via concatenation.
    The collector node returns ``[]`` after reading the accumulated
    results, which resets the list so that a subsequent loop iteration
    starts with a clean accumulator.
    """
    if not update:
        return []  # reset signal from collector
    return current + update


def _merge_provider_options(
    config: "FdsxConfig | None",
    flow: Flow,
    provider_name: str,
    task_options: dict[str, Any] | None,
    state_name: str = "",
) -> dict[str, Any] | None:
    """Merge provider options from three levels: config → workflow → task/branch.

    Args:
        config: Top-level fdsx configuration (level 1 source).
        flow: The flow definition carrying workflow-level provider options (level 2).
        provider_name: Provider name (e.g. 'claude', 'codex', 'opencode').
        task_options: Per-task or per-branch provider_options dict (level 3).
        state_name: Name of the state being merged (for error messages).

    Returns:
        Merged options dict, or None if no options were set at any level.
    """
    merged: dict[str, Any] = {}

    # Level 1: Config-level options.
    # Use exclude_defaults=True so that Pydantic default values (False, [], None)
    # do not override explicit settings at higher-priority levels.
    if config is not None and config.providers is not None:
        config_opts = getattr(config.providers, provider_name, None)
        if config_opts is not None:
            merged = _deep_merge(merged, config_opts.model_dump(exclude_defaults=True))

    # Level 2: Workflow-level options (from flow.providers dict).
    if flow.providers is not None:
        flow_opts = flow.providers.get(provider_name)
        if flow_opts is not None:
            merged = _deep_merge(merged, flow_opts)

    # Level 3: Task/Branch-level options.
    if task_options is not None:
        merged = _deep_merge(merged, task_options)

    if not merged:
        return None

    # Claude-specific: enforce mutual exclusion between system_prompt and append_system_prompt
    if provider_name == "claude":
        has_system_prompt = bool(merged.get("system_prompt"))
        has_append = bool(merged.get("append_system_prompt"))
        if has_system_prompt and has_append:
            from fdsx.core.engine.validate import FlowValidationError

            raise FlowValidationError(
                f"State '{state_name}': 'system_prompt' and 'append_system_prompt' "
                f"are mutually exclusive. Both cannot be set."
            )
    else:
        # Non-Claude providers: warn once and strip the fields
        for field in ("system_prompt", "append_system_prompt"):
            if field in merged:
                logger.warning(
                    "provider_option_unsupported",
                    state=state_name,
                    provider=provider_name,
                    field=field,
                )
                del merged[field]

    return merged if merged else None


def _extract_result_paths(flow: Flow) -> list[str]:
    """Extract all result_path fields from a flow."""
    paths = []
    for _state_name, state in flow.states.items():
        if isinstance(state, TaskState) and state.result_path:
            paths.append(state.result_path)
            if state.extract:
                paths.append(state.extract.result_path)
            if state.result_file:
                paths.append(state.result_file)
        elif isinstance(state, ParallelState) and state.result_path:
            paths.append(state.result_path)
            if state.result_file:
                paths.append(state.result_file)
        elif isinstance(state, PassState) and state.aggregate:
            paths.append(state.aggregate.result_path)
        elif isinstance(state, (WaitState, MapState)) and state.result_path:
            paths.append(state.result_path)
    return paths


def _check_max_iterations(state_name: str, state_def: Any, iteration: int) -> None:
    """Raise RuntimeError if the state has exceeded its max_iterations limit.

    Called BEFORE execution logic so the flow fails on entry when the limit is hit.
    """
    max_iter = getattr(state_def, "max_iterations", None)
    if max_iter is not None and iteration > max_iter:
        raise RuntimeError(
            f"State '{state_name}' reached max_iterations limit ({max_iter})"
        )


def _get_next_state(state: Any) -> str | None:
    """Get the next state from a state."""
    if hasattr(state, "next") and state.next:
        return state.next  # type: ignore[no-any-return]
    if hasattr(state, "end") and state.end:
        return "END"
    return None


def _build_state_schema(flow: Flow, input_keys: set[str] | None = None) -> type:
    """Build a TypedDict state schema that covers ALL state keys used by the flow.

    LangGraph's _get_updates filters every node's output dict to only keys that
    are declared as channels in the schema. With a partial schema (only _br_* keys),
    all workflow variables like $.reviews, $.decision, $.plan_output would be silently
    dropped by _get_updates. This function declares ALL needed keys:

    1. _br_{state_name} reducer channels (Annotated[list, _parallel_branch_reducer]) for each
       ParallelState — required for Send API fan-in accumulation.
    2. All result_path / extract / aggregate top-level keys as LastValue channels.
    3. Input keys from --input CLI flags.
    4. _meta internal key.
    5. remaining_steps managed channel for loop control.

    Always returns a TypedDict class with named channels for proper per-key
    channel tracking in LangGraph checkpoints.
    """
    annotations: dict[str, Any] = {}

    # 1. Reducer channels for parallel branch result accumulation
    for state_name, state in flow.states.items():
        if isinstance(state, ParallelState):
            annotations[f"_br_{state_name}"] = Annotated[list, _parallel_branch_reducer]

    # 2. All result_path / extract.result_path / aggregate.result_path top-level keys
    for _state_name, state in flow.states.items():
        if isinstance(state, TaskState) and state.result_path:
            k = _top_level_key(state.result_path)
            if k:
                annotations.setdefault(k, Any)
            if state.extract:
                k = _top_level_key(state.extract.result_path)
                if k:
                    annotations.setdefault(k, Any)
            if state.result_file:
                k = _top_level_key(state.result_file)
                if k:
                    annotations.setdefault(k, Any)
        elif isinstance(state, ParallelState) and state.result_path:
            k = _top_level_key(state.result_path)
            if k:
                annotations.setdefault(k, Any)
            if state.result_file:
                k = _top_level_key(state.result_file)
                if k:
                    annotations.setdefault(k, Any)
        elif isinstance(state, PassState):
            if state.aggregate:
                k = _top_level_key(state.aggregate.result_path)
                if k:
                    annotations.setdefault(k, Any)
            if state.parameters:
                for target in state.parameters:
                    k = _top_level_key(str(target))
                    if k:
                        annotations.setdefault(k, Any)
        elif isinstance(state, MapState):
            if state.result_path:
                k = _top_level_key(state.result_path)
                if k:
                    annotations.setdefault(k, Any)
            k = _top_level_key(state.items_path)
            if k:
                annotations.setdefault(k, Any)
        elif isinstance(state, WaitState) and state.result_path:
            k = _top_level_key(state.result_path)
            if k:
                annotations.setdefault(k, Any)

    # 3. Input keys from --input CLI flags
    if input_keys:
        for key in input_keys:
            annotations.setdefault(key, Any)

    # 4. Internal tracking keys
    annotations.setdefault("_meta", Any)
    annotations.setdefault("_state_iterations", Any)

    # 5. Managed channel for loop control (Phase 4)
    annotations["remaining_steps"] = RemainingSteps

    return TypedDict("FlowState", annotations, total=False)  # type: ignore[no-any-return,operator]

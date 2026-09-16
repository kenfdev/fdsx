"""Static constraints for native conversation forks."""

from collections.abc import Iterator
from typing import TYPE_CHECKING

from fdsx.core.graph_utils import get_next_states
from fdsx.models.flow import (
    Branch,
    EscalationConfig,
    Flow,
    IteratorTaskState,
    MapState,
    ParallelState,
    TaskState,
)

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig


def reachable_states(flow: Flow, *, excluding: str | None = None) -> set[str]:
    """Walk entry paths, optionally deleting one vertex (including its edges)."""
    reached: set[str] = set()
    pending = [flow.start_at]
    while pending:
        name = pending.pop()
        if name == excluding or name in reached:
            continue
        reached.add(name)
        pending.extend(get_next_states(flow.states[name]) - reached)
    return reached


def fork_destinations(
    flow: Flow,
) -> Iterator[tuple[str, str, TaskState | Branch | IteratorTaskState]]:
    """Yield outer ancestry anchor, diagnostic name and destination."""
    for name, state in flow.states.items():
        if isinstance(state, TaskState):
            yield name, name, state
        elif isinstance(state, ParallelState):
            for index, branch in enumerate(state.branches):
                yield name, f"{name}.{branch.name or index}", branch
        elif isinstance(state, MapState):
            for task in state.iterator.states:
                yield name, f"{name}.{task.name}", task


def validate_session_forks(flow: Flow, config: "FdsxConfig | None" = None) -> list[str]:
    """Require strict dominance and effective Pi identity on both endpoints."""
    errors: list[str] = []
    reachable = reachable_states(flow)
    escalation = flow.retry_escalation
    if escalation is None and config is not None:
        escalation = config.retry_escalation
    for name, diagnostic_name, destination in fork_destinations(flow):
        if destination.fork_from is None:
            continue
        source_name = destination.fork_from
        prefix = f"State '{diagnostic_name}' fork_from '{source_name}': "
        source = flow.states.get(source_name)
        if not isinstance(source, TaskState) or source.provider == "system":
            errors.append(
                prefix
                + "source must name a top-level ordinary AI task in this workflow"
            )
            continue
        if source.provider != "pi" or destination.provider != "pi":
            errors.append(
                prefix + "both endpoints must use the same supported provider (pi)"
            )
        if name not in reachable:
            errors.append(prefix + "unreachable fork destinations are not supported")
        elif name == source_name or name in reachable_states(
            flow, excluding=source_name
        ):
            errors.append(
                prefix
                + "source must strictly precede the destination on every entry path, including the first loop visit"
            )
        if isinstance(escalation, EscalationConfig) and escalation.provider != "pi":
            errors.append(
                prefix
                + "effective retry_escalation must remain provider pi on both endpoints; use pi or disable inherited escalation"
            )
    return errors

"""Walk declared workflow scopes without conflating local and parent namespaces."""

from collections.abc import Iterator
from typing import Any

from fdsx.models.flow import (
    Flow,
    LocalWorkflow,
    MapState,
    ParallelState,
    State,
    WorkflowBranch,
)


def local_workflows(flow: Flow) -> Iterator[tuple[str, LocalWorkflow]]:
    for name, state in flow.states.items():
        if isinstance(state, MapState) and isinstance(state.iterator, LocalWorkflow):
            yield f"{name}.iterator", state.iterator
        elif isinstance(state, ParallelState):
            for index, branch in enumerate(state.branches):
                if isinstance(branch, WorkflowBranch):
                    yield f"{name}.branches.{index}", branch.workflow


def walk_states(flow: Flow) -> Iterator[tuple[str, State]]:
    yield from flow.states.items()
    for scope, workflow in local_workflows(flow):
        for name, state in workflow.states.items():
            yield f"{scope}.{name}", state


def raw_definitions(data: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Include legacy leaves and new local leaves for profile and file resolution."""
    states = data.get("states", {})
    if not isinstance(states, dict):
        return
    for name, state in states.items():
        if not isinstance(state, dict):
            continue
        yield f"State '{name}'", state
        if state.get("type") == "map":
            iterator = state.get("iterator")
            if not isinstance(iterator, dict):
                continue
            children = iterator.get("states", [])
            if isinstance(children, dict):
                for child, definition in children.items():
                    if isinstance(definition, dict):
                        yield f"Map '{name}' iterator state '{child}'", definition
            elif isinstance(children, list):
                for index, definition in enumerate(children):
                    if isinstance(definition, dict):
                        yield (
                            f"Map '{name}' iterator state '{definition.get('name', index)}'",
                            definition,
                        )
        elif state.get("type") == "parallel":
            branches = state.get("branches", [])
            if not isinstance(branches, list):
                continue
            for index, branch in enumerate(branches):
                if not isinstance(branch, dict):
                    continue
                yield f"Parallel state '{name}' branch {index}", branch
                workflow = branch.get("workflow")
                if isinstance(workflow, dict) and isinstance(
                    workflow.get("states"), dict
                ):
                    for child, definition in workflow["states"].items():
                        if isinstance(definition, dict):
                            yield (
                                f"Parallel state '{name}' branch {index}, state '{child}'",
                                definition,
                            )

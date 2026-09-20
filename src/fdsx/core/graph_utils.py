from collections.abc import Mapping

from fdsx.models.flow import (
    ChoiceState,
    ClassifierState,
    EvaluateState,
    FailState,
    MapState,
    ParallelState,
    PassState,
    State,
    TaskState,
    WaitState,
)

END_SENTINEL = "$END"


def get_next_states(state: State, include_end_sentinel: bool = False) -> set[str]:
    """Return the set of next state names reachable from the given state.

    Args:
        state: The current state to inspect.
        include_end_sentinel: When True, adds ``$END`` for states with
            ``end=True`` and for ChoiceState with no default.
            When False, only concrete next-state names are returned.
    """
    result: set[str] = set()

    if isinstance(state, (TaskState, EvaluateState, ClassifierState)):
        if state.next:
            result.add(state.next)
        if include_end_sentinel and state.end:
            result.add(END_SENTINEL)
    elif isinstance(state, ChoiceState):
        for choice in state.choices:
            result.add(choice.next)
        if state.default:
            result.add(state.default)
        if include_end_sentinel and state.default is None:
            result.add(END_SENTINEL)
    elif isinstance(state, (ParallelState, PassState, WaitState, MapState)):
        if state.next:
            result.add(state.next)
        if include_end_sentinel and state.end:
            result.add(END_SENTINEL)
    elif isinstance(state, FailState):
        pass  # FailState has no successors — always returns empty set

    return result


def has_terminal_path(states: Mapping[str, State], start: str) -> bool:
    """Whether the entry can reach a declared termination, despite possible cycles."""
    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == END_SENTINEL:
            return True
        if current in visited:
            continue
        visited.add(current)
        state = states.get(current)
        if state is None:
            continue
        if isinstance(state, FailState):
            return True
        pending.extend(get_next_states(state, include_end_sentinel=True) - visited)
    return False

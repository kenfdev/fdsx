"""Routing functions for the compiler package."""

from typing import Any, Callable

from langgraph.graph import END

from fdsx.models.flow import ChoiceState


def _create_routing_function(state: ChoiceState) -> Callable[[dict[str, Any]], str]:
    """Create a routing function for a Choice state."""

    def route(state_dict: dict[str, Any]) -> str:
        for choice in state.choices:
            variable_value = _resolve_jsonpath(choice.variable, state_dict)
            if _evaluate_condition(variable_value, choice.operator, choice.value):
                return choice.next

        if state.default:
            return state.default

        return END

    return route


def _resolve_jsonpath(path: str, data: dict[str, Any]) -> Any:
    """Resolve a JSONPath in data."""
    from fdsx.core.variables import resolve_jsonpath

    return resolve_jsonpath(path, data)


def _evaluate_condition(value: Any, operator: str, expected: Any) -> bool:
    """Evaluate a choice condition."""
    if operator == "equals":
        return value == expected  # type: ignore[no-any-return]
    elif operator == "not_equals":
        return value != expected  # type: ignore[no-any-return]
    elif operator == "greater_than":
        return value > expected  # type: ignore[no-any-return]
    elif operator == "less_than":
        return value < expected  # type: ignore[no-any-return]
    elif operator == "contains":
        return expected in str(value)
    else:
        raise ValueError(f"Unknown operator: {operator}")

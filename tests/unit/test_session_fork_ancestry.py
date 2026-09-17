"""Strict dominance across joins, loops and unreachable components."""

import pytest
from pydantic import ValidationError

from fdsx.models.flow import Flow


def task(next=None, source=None):
    state = dict(type="task", provider="pi", model="test", prompt_template="test")
    state.update({"next": next} if next else {"end": True})
    if source:
        state["fork_from"] = source
    return state


def split(left, right):
    return dict(
        type="choice",
        choices=[dict(variable="$.x", operator="equals", value=True, next=left)],
        default=right,
    )


@pytest.mark.parametrize(
    ("start", "states", "valid"),
    [
        ("plan", {"plan": task("work"), "work": task(source="plan")}, True),
        (
            "plan",
            {
                "plan": task("split"),
                "split": split("a", "b"),
                "a": task("work"),
                "b": task("work"),
                "work": task(source="plan"),
            },
            True,
        ),
        (
            "split",
            {
                "split": split("plan", "other"),
                "plan": task("work"),
                "other": task("work"),
                "work": task(source="plan"),
            },
            False,
        ),
        ("work", {"work": task(source="work")}, False),
        (
            "work",
            {
                "work": task("route", source="plan"),
                "route": split("plan", "done"),
                "plan": task("work"),
                "done": task(),
            },
            False,
        ),
        (
            "plan",
            {
                "plan": task("work"),
                "work": task("route", source="plan"),
                "route": split("plan", "done"),
                "done": task(),
            },
            True,
        ),
        (
            "done",
            {"done": task(), "plan": task("work"), "work": task(source="plan")},
            False,
        ),
        (
            "plan",
            {
                "plan": task("work"),
                "work": task(source="plan"),
                "unreachable": task("work"),
            },
            True,
        ),
    ],
)
@pytest.mark.parametrize("kind", ["task", "parallel", "map"])
@pytest.mark.parametrize("provider", ["pi", "claude", "codex", "grok"])
def test_mandatory_first_visit_ancestor(start, states, valid, kind, provider):
    states = {name: dict(state) for name, state in states.items()}
    for state in states.values():
        if state["type"] == "task":
            state["provider"] = provider
    if kind != "task":
        for name, state in list(states.items()):
            if "fork_from" not in state:
                continue
            child = {
                key: value
                for key, value in state.items()
                if key not in {"type", "next", "end"}
            }
            outer = {
                key: value for key, value in state.items() if key in {"next", "end"}
            }
            if kind == "parallel":
                states[name] = dict(
                    type=kind, branches=[child], result_path="$.results", **outer
                )
            else:
                states[name] = dict(
                    type=kind,
                    items_path="$.items",
                    iterator={
                        "states": [dict(name="plan", result_path="$.value", **child)]
                    },
                    result_path="$.results",
                    **outer,
                )

    definition = dict(
        name="graph", description="Ancestry", start_at=start, states=states
    )
    if valid:
        Flow.model_validate(definition)
    else:
        with pytest.raises(ValidationError, match="fork_from"):
            Flow.model_validate(definition)

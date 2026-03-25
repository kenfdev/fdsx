"""Shared interrupt-handling loop for the engine package."""
from typing import Any

from langgraph.types import Command

from fdsx.display.terminal import display_wait_prompt


def handle_interrupts(
    graph: Any,
    config: dict[str, Any],
    last_state: dict[str, Any],
    stream_mode: str = "values",
) -> dict[str, Any]:
    """Handle interrupt loop for wait states requiring user input.

    Encapsulates the while-loop pattern: get_state → find interrupt →
    display_wait_prompt → stream Command(resume=...).

    Args:
        graph: The compiled LangGraph graph.
        config: The LangGraph config dict (with thread_id etc.).
        last_state: The last state dict before entering the interrupt loop.
        stream_mode: LangGraph stream mode (default "values").

    Returns:
        Updated last_state after all interrupts are handled.
    """
    while True:
        state_info = graph.get_state(config)

        if not state_info.tasks:
            break

        payload = None
        for task in state_info.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                payload = task.interrupts[0].value
                break

        if payload is None:
            break

        message = payload.get("message", "")
        choices = payload.get("choices", [])
        state_name = payload.get("state_name", "wait")

        user_selection = display_wait_prompt(state_name, message, choices)

        for state_snapshot in graph.stream(
            Command(resume=user_selection),
            config=config,
            stream_mode=stream_mode,
        ):
            if "__interrupt__" not in state_snapshot:
                last_state = state_snapshot

    return last_state

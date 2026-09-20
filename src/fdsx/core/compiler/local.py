"""Build local graphs with scoped names and the ordinary state implementations."""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fdsx.models.flow import ChoiceState, FailState, Flow, LocalWorkflow

from .compile import CompiledGraph, compile_flow

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig


def compile_local(
    definition: LocalWorkflow,
    scope: str,
    flow: Flow,
    input_keys: set[str],
    recorder: Any = None,
    config: "FdsxConfig | None" = None,
    log_dir: Path | None = None,
    quiet: bool = False,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> CompiledGraph:
    # Names are scoped for logs/hooks/counters; result paths stay author-defined.
    names = {name: f"{scope}.{name}" for name in definition.states}
    states = {}
    for name, original in definition.states.items():
        state = original.model_copy(deep=True)
        if isinstance(state, ChoiceState):
            for choice in state.choices:
                choice.next = names[choice.next]
            if state.default is not None:
                state.default = names[state.default]
        elif not isinstance(state, FailState) and state.next is not None:
            state.next = names[state.next]
        states[names[name]] = state
    limit = definition.max_loop if definition.max_loop is not None else flow.max_loop
    local_flow = flow.model_copy(
        update={
            "states": states,
            "start_at": names[definition.start_at],
            "max_loop": limit,
        }
    )
    return compile_flow(
        local_flow,
        input_keys=input_keys,
        checkpointer=False,
        recorder=recorder,
        config=config,
        log_dir=log_dir,
        quiet=quiet,
        on_process_start=on_process_start,
    )

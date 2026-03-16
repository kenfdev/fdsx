import time
from typing import Any, Callable, TypedDict

from langgraph.graph import END, StateGraph

from fdsx.core.variables import resolve_template, resolve_template_shell_safe, set_jsonpath
from fdsx.display import terminal
from fdsx.display.terminal import _sanitize_output
from fdsx.models.flow import (
    ChoiceState,
    Flow,
    ParallelState,
    PassState,
    TaskState,
    WaitState,
)
from fdsx.providers.base import get_provider


class FlowState(TypedDict):
    """Base flow state - uses Any for flexibility."""

    pass


class CompiledGraph:
    """Compiled LangGraph state machine."""

    def __init__(self, graph: Any, entry_point: str, result_paths: list[str]):
        self.graph = graph
        self.entry_point = entry_point
        self.result_paths = result_paths


def compile_flow(flow: Flow) -> CompiledGraph:
    """Compile a Flow into a LangGraph StateGraph.

    Args:
        flow: The Flow to compile

    Returns:
        CompiledGraph with the compiled state machine
    """
    result_paths = _extract_result_paths(flow)

    graph: StateGraph[Any] = StateGraph(object)

    for state_name, state in flow.states.items():
        if isinstance(state, TaskState):
            graph.add_node(state_name, _create_task_node(state_name, state, flow))  # type: ignore[call-overload]
        elif isinstance(state, ChoiceState):
            graph.add_node(state_name, _create_choice_node(state_name, state, flow))  # type: ignore[call-overload]
        elif state.type == "parallel":
            graph.add_node(state_name, _create_parallel_node(state_name, state, flow))  # type: ignore[call-overload]
        elif state.type == "pass":
            graph.add_node(state_name, _create_pass_node(state_name, state, flow))  # type: ignore[call-overload]
        elif state.type == "wait":
            graph.add_node(state_name, _create_wait_node(state_name, state, flow))  # type: ignore[call-overload]

    for state_name, state in flow.states.items():
        next_state = _get_next_state(state)
        if next_state:
            if next_state == "END":
                graph.add_edge(state_name, END)
            else:
                graph.add_edge(state_name, next_state)

        if isinstance(state, ChoiceState):
            choices = state.choices
            default = state.default or END
            graph.add_conditional_edges(
                state_name,
                _create_routing_function(state),
                {choice.next: choice.next for choice in choices} | {default: default},  # type: ignore[arg-type]
            )

    graph.set_entry_point(flow.start_at)

    compiled = graph.compile()

    return CompiledGraph(compiled, flow.start_at, result_paths)


def _extract_result_paths(flow: Flow) -> list[str]:
    """Extract all result_path fields from a flow."""
    paths = []
    for state_name, state in flow.states.items():
        if isinstance(state, TaskState) and state.result_path:
            paths.append(state.result_path)
        elif isinstance(state, ParallelState) and state.result_path:
            paths.append(state.result_path)
        elif isinstance(state, WaitState) and state.result_path:
            paths.append(state.result_path)
    return paths


def _create_task_node(
    state_name: str, state: TaskState, flow: Flow
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Task state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        from fdsx.providers.base import ProviderResult

        start_time = time.time()
        terminal.display_state_start(
            state_name=state_name,
            state_type="task",
            provider=state.provider,
            model=state.model,
        )

        prompt = state.prompt_template or ""
        resolved_prompt = resolve_template(prompt, state_dict)

        provider = get_provider(state.provider)

        max_retries = state.retry if state.retry is not None else 3
        last_error = "No attempts made"
        result = ProviderResult(exit_code=1, stdout="", stderr="")

        for attempt in range(max_retries + 1):
            if state.provider == "system":
                resolved_command = resolve_template_shell_safe(state.command or "", state_dict)
                result = provider.execute(
                    prompt="",
                    model=state.model,
                    timeout=state.timeout_seconds,
                    command=resolved_command,
                    output_callback=None,
                )
            else:
                result = provider.execute(
                    prompt=resolved_prompt,
                    model=state.model,
                    timeout=state.timeout_seconds,
                    output_callback=terminal.display_output_line,
                )

            if result.exit_code == 0:
                break
            last_error = result.stderr

        if result.exit_code != 0:
            terminal.display_state_error(state_name, last_error)
            raise RuntimeError(
                f"Provider {state.provider} failed after {max_retries + 1} attempts with exit code {result.exit_code}: {_sanitize_output(last_error)}"
            )

        output = result.stdout.strip()

        new_state = set_jsonpath(state.result_path, state_dict, output)

        duration = time.time() - start_time
        terminal.display_state_complete(state_name, duration)

        return new_state

    return node


def _create_choice_node(
    state_name: str, state: ChoiceState, flow: Flow
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Choice state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        return state_dict

    return node


def _create_parallel_node(
    state_name: str, state: ParallelState, flow: Flow
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Parallel state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        from fdsx.providers.base import ProviderResult

        start_time = time.time()
        terminal.display_state_start(
            state_name=state_name,
            state_type="parallel",
            provider=None,
            model=None,
        )

        results = []
        for branch in state.branches:
            prompt = branch.prompt_template or ""
            resolved_prompt = resolve_template(prompt, state_dict)

            provider = get_provider(branch.provider)

            max_retries = branch.retry if branch.retry is not None else 3
            last_error = "No attempts made"
            result = ProviderResult(exit_code=1, stdout="", stderr="")

            for attempt in range(max_retries + 1):
                if branch.provider == "system":
                    resolved_command = resolve_template_shell_safe(
                        branch.command or "", state_dict
                    )
                    result = provider.execute(
                        prompt="",
                        model=branch.model,
                        timeout=branch.timeout_seconds,
                        command=resolved_command,
                        output_callback=None,
                    )
                else:
                    result = provider.execute(
                        prompt=resolved_prompt,
                        model=branch.model,
                        timeout=branch.timeout_seconds,
                        output_callback=terminal.display_output_line,
                    )

                if result.exit_code == 0:
                    break
                last_error = result.stderr

            if result.exit_code != 0:
                last_error = f"Failed after {max_retries + 1} attempts: {_sanitize_output(last_error)}"

            results.append(
                {
                    "output": result.stdout.strip(),
                    "exit_code": result.exit_code,
                    "error": last_error if result.exit_code != 0 else None,
                }
            )

        new_state = set_jsonpath(state.result_path, state_dict, results)

        duration = time.time() - start_time
        terminal.display_state_complete(state_name, duration)

        return new_state

    return node


def _create_pass_node(
    state_name: str, state: PassState, flow: Flow
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Pass state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        if state.parameters:
            for target, source in state.parameters.items():
                if isinstance(source, str):
                    value = resolve_template(source, state_dict)
                else:
                    value = source
                new_state = set_jsonpath(target, state_dict, value)
                state_dict = new_state
        return state_dict

    return node


def _create_wait_node(
    state_name: str, state: WaitState, flow: Flow
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Wait state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        return state_dict

    return node


def _get_next_state(state: Any) -> str | None:
    """Get the next state from a state."""
    if hasattr(state, "next") and state.next:
        return state.next  # type: ignore[no-any-return]
    if hasattr(state, "end") and state.end:
        return "END"
    return None


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

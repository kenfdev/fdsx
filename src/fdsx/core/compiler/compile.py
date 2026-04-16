"""compile_flow implementation for the compiler package."""

import logging
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from fdsx.core.hooks import (
    INPUT_FILENAME,
    OUTPUT_FILENAME,
    collect_hooks,
    execute_hooks,
    write_hook_data,
)
from fdsx.models.flow import (
    ChoiceState,
    Flow,
    HookEntry,
    MapState,
    ParallelState,
    TaskState,
    WaitState,
)

from .helpers import (
    _build_state_schema,
    _extract_result_paths,
    _get_next_state,
)
from .map_iteration import _create_map_node
from .nodes import (
    _create_choice_node,
    _create_pass_node,
    _create_task_node,
    _create_wait_interrupt_node,
    _create_wait_notify_node,
)
from .parallel import (
    _create_branch_executor,
    _create_collector_node,
    _create_dispatch_node,
    _create_fan_out,
)
from .routing import _create_routing_function

if TYPE_CHECKING:
    from fdsx.core.config import FdsxConfig

logger = logging.getLogger(__name__)

_WHITE = 0
_GRAY = 1
_BLACK = 2


def _get_all_next_state_names(state: Any, flow_states: dict[str, Any]) -> list[str]:
    """Return all possible next state names from a state, filtered to known states.

    Handles TaskState/MapState/WaitState/PassState/ParallelState (next attribute)
    and ChoiceState (choices[*].next + default). Excludes "END" and None.
    """
    results: list[str] = []

    if isinstance(state, ChoiceState):
        for choice in state.choices:
            if choice.next and choice.next in flow_states:
                results.append(choice.next)
        if state.default and state.default in flow_states:
            results.append(state.default)
    else:
        next_val = getattr(state, "next", None)
        if next_val and next_val in flow_states:
            results.append(next_val)

    return results


def _detect_loop_back_edges(flow: Any) -> set[tuple[str, str]]:
    """Detect back-edges in the flow graph using DFS with color marking.

    A back-edge (src, dst) exists when dst is on the current DFS stack (GRAY).
    Returns the set of (source_state_name, target_state_name) back-edge pairs.
    """
    color: dict[str, int] = {name: _WHITE for name in flow.states}
    back_edges: set[tuple[str, str]] = set()

    def dfs(node: str) -> None:
        color[node] = _GRAY
        for neighbor in _get_all_next_state_names(flow.states[node], flow.states):
            if color[neighbor] == _GRAY:
                back_edges.add((node, neighbor))
            elif color[neighbor] == _WHITE:
                dfs(neighbor)
        color[node] = _BLACK

    if flow.start_at in color:
        dfs(flow.start_at)

    return back_edges


def _make_loop_guard(target: str, max_loop: int) -> Callable[[dict[str, Any]], str]:
    """Route to END after max_loop visits to target (loop start), else loop back."""

    def route(state_dict: dict[str, Any]) -> str:
        iters = state_dict.get("_state_iterations", {})
        if iters.get(target, 0) >= max_loop:
            return END
        return target

    return route


def _wrap_routing_with_loop_guard(
    routing_fn: Callable[[dict[str, Any]], str],
    loop_back_targets: set[str],
    max_loop: int,
) -> Callable[[dict[str, Any]], str]:
    """Wrap routing fn to intercept loop-back transitions after max_loop visits."""

    def route(state_dict: dict[str, Any]) -> str:
        destination = routing_fn(state_dict)
        if destination in loop_back_targets:
            iters = state_dict.get("_state_iterations", {})
            if iters.get(destination, 0) >= max_loop:
                return END
        return destination

    return route


class FlowState(TypedDict):
    """Base flow state - uses Any for flexibility."""

    pass


class CompiledGraph:
    """Compiled LangGraph state machine."""

    def __init__(self, graph: Any, entry_point: str, result_paths: list[str]):
        self.graph = graph
        self.entry_point = entry_point
        self.result_paths = result_paths


def _wrap_with_hooks(
    node_fn: Callable[[dict[str, Any]], dict[str, Any]],
    state_name: str,
    on_start_hooks: list[HookEntry],
    on_complete_hooks: list[HookEntry],
    *,
    recorder: Any = None,
    fdsx_base_dir: Path | None = None,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Wrap a node function with hook execution.

    Calls execute_hooks(on_start_hooks) with status "starting" before node
    execution and execute_hooks(on_complete_hooks) with status "completed"
    after.  Hook data (input/output state dicts) is written to
    .fdsx/runs/<thread-id>/hooks/<state-name>/{input,output}.json.

    If both hook lists are empty, returns node_fn unchanged (no-op).

    Args:
        node_fn: The original node function to wrap.
        state_name: Logical state name used for hook data paths.
        on_start_hooks: Hooks to execute before the node runs.
        on_complete_hooks: Hooks to execute after the node runs.
        recorder: RunRecorder providing thread_id and flow_name.
        fdsx_base_dir: The .fdsx root directory for hook data files.
                       When None, defaults to CWD/.fdsx.

    Returns:
        Wrapped node function, or the original node_fn when both lists are empty.
    """
    if not on_start_hooks and not on_complete_hooks:
        return node_fn

    def wrapped(state_dict: dict[str, Any]) -> dict[str, Any]:
        thread_id: str = recorder.thread_id if recorder is not None else ""
        flow_name: str = recorder.flow_name if recorder is not None else ""

        input_data_path = write_hook_data(
            state_dict,
            state_name=state_name,
            filename=INPUT_FILENAME,
            thread_id=thread_id,
            base_dir=fdsx_base_dir,
        )

        if on_start_hooks:
            execute_hooks(
                on_start_hooks,
                state_name=state_name,
                status="starting",
                data_path=input_data_path,
                thread_id=thread_id,
                flow_name=flow_name,
                event="on_state_start",
            )

        node_error: BaseException | None = None
        result: dict[str, Any] = (
            state_dict  # fallback data written to output.json on failure
        )
        try:
            result = node_fn(state_dict)
        except BaseException as exc:
            node_error = exc

        status = "completed" if node_error is None else "failed"

        try:
            output_data_path = write_hook_data(
                {**state_dict, **result},
                state_name=state_name,
                filename=OUTPUT_FILENAME,
                thread_id=thread_id,
                base_dir=fdsx_base_dir,
            )

            if on_complete_hooks:
                execute_hooks(
                    on_complete_hooks,
                    state_name=state_name,
                    status=status,
                    data_path=output_data_path,
                    thread_id=thread_id,
                    flow_name=flow_name,
                    event="on_state_end",
                )
        except BaseException:
            if node_error is not None:
                logger.warning("Hook cleanup failed after node error", exc_info=True)
                raise node_error from None
            raise

        if node_error is not None:
            raise node_error

        return result

    return wrapped


def compile_flow(
    flow: Flow,
    input_keys: set[str] | None = None,
    checkpointer: Any = None,
    recorder: Any = None,
    config: "FdsxConfig | None" = None,
    log_dir: Path | None = None,
    quiet: bool = False,
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
) -> CompiledGraph:
    """Compile a Flow into a LangGraph StateGraph.

    Args:
        flow: The Flow to compile
        input_keys: Top-level keys injected via --input; needed for the state schema
                    so LangGraph channels are created for them.
        checkpointer: Optional checkpointer for state persistence.
                      If not provided and the flow contains Wait states,
                      a MemorySaver will be used as default.
        config: Optional fdsx configuration used to resolve provider options.
        log_dir: Optional directory for per-state streaming log files
                 (.fdsx/runs/<thread-id>/logs/). When None, streaming still
                 works on the terminal but no log files are written.
        quiet: When True, suppresses stderr streaming output from StreamLogger.
               Log files are still written.
        on_process_start: Optional callback invoked immediately after each
            ``subprocess.Popen()`` creation during flow execution.  Used by
            ``SignalHandler`` to register active subprocesses for signal
            forwarding.  Captured in node closures at compile time.

    Returns:
        CompiledGraph with the compiled state machine
    """
    result_paths = _extract_result_paths(flow)

    schema = _build_state_schema(flow, input_keys)
    graph: StateGraph[Any] = StateGraph(schema)

    if checkpointer is None:
        has_wait = any(isinstance(s, WaitState) for s in flow.states.values())
        if has_wait:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()

    # Derive the .fdsx base directory for hook data files from log_dir.
    # log_dir = .fdsx/runs/<thread-id>/logs/ -> parent x3 = .fdsx/
    fdsx_base_dir: Path | None = (
        log_dir.parent.parent.parent if log_dir is not None else None
    )

    # Resolve config-level hooks (merged global+project hooks are in fdsx_config.hooks)
    config_hooks = config.hooks if config is not None else None

    def _collect_state_hooks(state_obj: Any) -> tuple[list[HookEntry], list[HookEntry]]:
        """Collect on_state_start and on_state_end hooks for a state from all levels."""
        on_s = collect_hooks(
            "on_state_start",
            global_hooks=config_hooks,
            project_hooks=None,
            flow_hooks=flow.hooks,
            state_hooks=state_obj.hooks,
        )
        on_c = collect_hooks(
            "on_state_end",
            global_hooks=config_hooks,
            project_hooks=None,
            flow_hooks=flow.hooks,
            state_hooks=state_obj.hooks,
        )
        return on_s, on_c

    for state_name, state in flow.states.items():
        if isinstance(state, TaskState):
            on_start, on_complete = _collect_state_hooks(state)
            node = _create_task_node(
                state_name,
                state,
                flow,
                recorder,
                config,
                log_dir,
                quiet,
                on_process_start=on_process_start,
            )
            graph.add_node(
                state_name,
                _wrap_with_hooks(
                    node,
                    state_name,
                    on_start,
                    on_complete,
                    recorder=recorder,
                    fdsx_base_dir=fdsx_base_dir,
                ),
            )  # type: ignore[call-overload]
        elif isinstance(state, ChoiceState):
            on_start, on_complete = _collect_state_hooks(state)
            node = _create_choice_node(state_name, state, flow, recorder)
            graph.add_node(
                state_name,
                _wrap_with_hooks(
                    node,
                    state_name,
                    on_start,
                    on_complete,
                    recorder=recorder,
                    fdsx_base_dir=fdsx_base_dir,
                ),
            )  # type: ignore[call-overload]
        elif isinstance(state, ParallelState):
            on_start, on_complete = _collect_state_hooks(state)
            # Hooks wrap dispatch (on_start) and collector (on_complete), not branch executor.
            dispatch_node = _create_dispatch_node(state_name, state, recorder)
            graph.add_node(
                state_name,
                _wrap_with_hooks(
                    dispatch_node,
                    state_name,
                    on_start,
                    [],
                    recorder=recorder,
                    fdsx_base_dir=fdsx_base_dir,
                ),
            )  # type: ignore[call-overload]
            graph.add_node(
                f"_branch_{state_name}",
                _create_branch_executor(
                    state_name,
                    state,
                    flow,
                    recorder,
                    config,
                    log_dir,
                    quiet,
                    on_process_start=on_process_start,
                ),
            )  # type: ignore[call-overload]
            collector_node = _create_collector_node(state_name, state, flow, recorder)
            graph.add_node(
                f"_collect_{state_name}",
                _wrap_with_hooks(
                    collector_node,
                    state_name,
                    [],
                    on_complete,
                    recorder=recorder,
                    fdsx_base_dir=fdsx_base_dir,
                ),
            )  # type: ignore[call-overload]
        elif state.type == "pass":
            on_start, on_complete = _collect_state_hooks(state)
            node = _create_pass_node(state_name, state, flow, recorder)
            graph.add_node(
                state_name,
                _wrap_with_hooks(
                    node,
                    state_name,
                    on_start,
                    on_complete,
                    recorder=recorder,
                    fdsx_base_dir=fdsx_base_dir,
                ),
            )  # type: ignore[call-overload]
        elif state.type == "wait":
            on_start, on_complete = _collect_state_hooks(state)
            # WaitState is split into two nodes: notify (pre-interrupt) and interrupt.
            # on_start hooks fire in the notify node; on_complete hooks fire in the interrupt node.
            notify_node = _create_wait_notify_node(state_name, state, recorder)
            graph.add_node(
                state_name,
                _wrap_with_hooks(
                    notify_node,
                    state_name,
                    on_start,
                    [],
                    recorder=recorder,
                    fdsx_base_dir=fdsx_base_dir,
                ),
            )  # type: ignore[call-overload]
            interrupt_node = _create_wait_interrupt_node(state_name, state, recorder)
            graph.add_node(
                f"_{state_name}_int",
                _wrap_with_hooks(
                    interrupt_node,
                    state_name,
                    [],
                    on_complete,
                    recorder=recorder,
                    fdsx_base_dir=fdsx_base_dir,
                ),
            )  # type: ignore[call-overload]
            graph.add_edge(state_name, f"_{state_name}_int")
        elif isinstance(state, MapState):
            on_start, on_complete = _collect_state_hooks(state)
            node = _create_map_node(
                state_name,
                state,
                flow,
                recorder,
                config,
                log_dir,
                quiet,
                on_process_start=on_process_start,
            )
            graph.add_node(
                state_name,
                _wrap_with_hooks(
                    node,
                    state_name,
                    on_start,
                    on_complete,
                    recorder=recorder,
                    fdsx_base_dir=fdsx_base_dir,
                ),
            )  # type: ignore[call-overload]

    # Pre-compute loop-back edges for loop guard insertion
    loop_back_edges = _detect_loop_back_edges(flow)
    loop_back_targets_by_source: dict[str, set[str]] = {}
    for src, tgt in loop_back_edges:
        loop_back_targets_by_source.setdefault(src, set()).add(tgt)

    for state_name, state in flow.states.items():
        if isinstance(state, ParallelState):
            # Fan-out: dispatch node → branch nodes via Send API (no path_map needed)
            graph.add_conditional_edges(
                state_name,
                _create_fan_out(state_name, state),
            )
            # Fan-in: each branch node → collector
            graph.add_edge(f"_branch_{state_name}", f"_collect_{state_name}")
            # Outgoing: collector → next state (NOT dispatch node)
            next_s = _get_next_state(state)
            if next_s:
                graph.add_edge(
                    f"_collect_{state_name}",
                    END if next_s == "END" else next_s,
                )
            continue  # Skip the regular edge-adding below for ParallelState

        if isinstance(state, WaitState):
            # Outgoing edge comes from the interrupt node (_{state_name}_int), not
            # the notify pre-node ({state_name}).  The pre-node → interrupt edge was
            # already added in the node-registration loop above.
            next_s = _get_next_state(state)
            if next_s:
                graph.add_edge(
                    f"_{state_name}_int",
                    END if next_s == "END" else next_s,
                )
            continue  # Skip the regular edge-adding below for WaitState

        next_state = _get_next_state(state)
        if next_state:
            if next_state == "END":
                graph.add_edge(state_name, END)
            elif next_state in loop_back_targets_by_source.get(state_name, set()):
                # Loop-back edge: replace add_edge with conditional guard
                graph.add_conditional_edges(
                    state_name,
                    _make_loop_guard(next_state, flow.max_loop),
                    {next_state: next_state, END: END},
                )
            else:
                graph.add_edge(state_name, next_state)

        if isinstance(state, ChoiceState):
            choices = state.choices
            default = state.default or END
            loop_back_targets = loop_back_targets_by_source.get(state_name, set())
            routing_fn = _create_routing_function(state)
            path_map: dict[Any, str] = {
                choice.next: choice.next for choice in choices
            } | {default: default}
            if loop_back_targets:
                routing_fn = _wrap_routing_with_loop_guard(
                    routing_fn, loop_back_targets, flow.max_loop
                )
                path_map[END] = END
            graph.add_conditional_edges(
                state_name,
                routing_fn,
                path_map,
            )

    graph.set_entry_point(flow.start_at)

    if checkpointer is not None:
        compiled = graph.compile(checkpointer=checkpointer)
    else:
        compiled = graph.compile()

    return CompiledGraph(compiled, flow.start_at, result_paths)

import operator
import subprocess
import time
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt, Send

from fdsx.core.variables import (
    resolve_template,
    resolve_template_shell_safe,
    set_jsonpath,
)
from fdsx.display import terminal
from fdsx.display.terminal import _sanitize_output
from fdsx.models.flow import (
    AggregateRule,
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


def _top_level_key(path: str) -> str | None:
    """Extract the top-level key from a JSONPath like '$.reviews' → 'reviews'."""
    if path.startswith("$."):
        path = path[2:]
    if not path:
        return None
    return path.split(".")[0].split("[")[0] or None


def _build_state_schema(flow: Flow, input_keys: set[str] | None = None) -> type:
    """Build a TypedDict state schema that covers ALL state keys used by the flow.

    LangGraph's _get_updates filters every node's output dict to only keys that
    are declared as channels in the schema. With a partial schema (only _br_* keys),
    all workflow variables like $.reviews, $.decision, $.plan_output would be silently
    dropped by _get_updates. This function declares ALL needed keys:

    1. _br_{state_name} reducer channels (Annotated[list, operator.add]) for each
       ParallelState — required for Send API fan-in accumulation.
    2. All result_path / extract / aggregate top-level keys as LastValue channels.
    3. Input keys from --input CLI flags.
    4. _meta internal key.

    Returns `object` (→ __root__ single channel, no filtering) for flows with no
    ParallelState, since they don't need the Send API reducer channels.
    """
    has_parallel = any(isinstance(s, ParallelState) for s in flow.states.values())
    if not has_parallel:
        return object

    annotations: dict[str, Any] = {}

    # 1. Reducer channels for parallel branch result accumulation
    for state_name, state in flow.states.items():
        if isinstance(state, ParallelState):
            annotations[f"_br_{state_name}"] = Annotated[list, operator.add]

    # 2. All result_path / extract.result_path / aggregate.result_path top-level keys
    for state_name, state in flow.states.items():
        if isinstance(state, TaskState) and state.result_path:
            k = _top_level_key(state.result_path)
            if k:
                annotations.setdefault(k, Any)
            if state.extract:
                k = _top_level_key(state.extract.result_path)
                if k:
                    annotations.setdefault(k, Any)
        elif isinstance(state, ParallelState) and state.result_path:
            k = _top_level_key(state.result_path)
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
        elif isinstance(state, WaitState) and state.result_path:
            k = _top_level_key(state.result_path)
            if k:
                annotations.setdefault(k, Any)

    # 3. Input keys from --input CLI flags
    if input_keys:
        for key in input_keys:
            annotations.setdefault(key, Any)

    # 4. Internal tracking key
    annotations.setdefault("_meta", Any)

    return TypedDict("FlowState", annotations, total=False)  # type: ignore[no-any-return,operator]


def compile_flow(
    flow: Flow,
    input_keys: set[str] | None = None,
    checkpointer: Any = None,
    recorder: Any = None,
) -> CompiledGraph:
    """Compile a Flow into a LangGraph StateGraph.

    Args:
        flow: The Flow to compile
        input_keys: Top-level keys injected via --input; needed for the state schema
                    so LangGraph channels are created for them.
        checkpointer: Optional checkpointer for state persistence.
                      If not provided and the flow contains Wait states,
                      a MemorySaver will be used as default.

    Returns:
        CompiledGraph with the compiled state machine
    """
    result_paths = _extract_result_paths(flow)

    schema = _build_state_schema(flow, input_keys)
    graph: StateGraph[Any] = StateGraph(schema)

    if checkpointer is None:
        from fdsx.models.flow import WaitState as _WaitState

        has_wait = any(isinstance(s, _WaitState) for s in flow.states.values())
        if has_wait:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()

    for state_name, state in flow.states.items():
        if isinstance(state, TaskState):
            graph.add_node(
                state_name, _create_task_node(state_name, state, flow, recorder)
            )  # type: ignore[call-overload]
        elif isinstance(state, ChoiceState):
            graph.add_node(
                state_name, _create_choice_node(state_name, state, flow, recorder)
            )  # type: ignore[call-overload]
        elif isinstance(state, ParallelState):
            graph.add_node(
                state_name, _create_dispatch_node(state_name, state, recorder)
            )  # type: ignore[call-overload]
            graph.add_node(
                f"_branch_{state_name}",
                _create_branch_executor(state_name, state, flow, recorder),
            )  # type: ignore[call-overload]
            graph.add_node(
                f"_collect_{state_name}",
                _create_collector_node(state_name, state, flow, recorder),
            )  # type: ignore[call-overload]
        elif state.type == "pass":
            graph.add_node(
                state_name, _create_pass_node(state_name, state, flow, recorder)
            )  # type: ignore[call-overload]
        elif state.type == "wait":
            graph.add_node(
                state_name, _create_wait_notify_node(state_name, state, recorder)
            )  # type: ignore[call-overload]
            graph.add_node(
                f"_{state_name}_int",
                _create_wait_interrupt_node(state_name, state, recorder),
            )  # type: ignore[call-overload]
            graph.add_edge(state_name, f"_{state_name}_int")

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

    if checkpointer is not None:
        compiled = graph.compile(checkpointer=checkpointer)
    else:
        compiled = graph.compile()

    return CompiledGraph(compiled, flow.start_at, result_paths)


def _extract_result_paths(flow: Flow) -> list[str]:
    """Extract all result_path fields from a flow."""
    paths = []
    for state_name, state in flow.states.items():
        if isinstance(state, TaskState) and state.result_path:
            paths.append(state.result_path)
            if state.extract:
                paths.append(state.extract.result_path)
        elif isinstance(state, ParallelState) and state.result_path:
            paths.append(state.result_path)
        elif isinstance(state, PassState) and state.aggregate:
            paths.append(state.aggregate.result_path)
        elif isinstance(state, WaitState) and state.result_path:
            paths.append(state.result_path)
    return paths


def _set_next_state_meta(state_dict: dict[str, Any], state: Any) -> dict[str, Any]:
    """Inject _meta.next_state so list_threads can show the correct current state.

    Stores the name of the node that will execute NEXT so that if the next node
    crashes before its checkpoint is written, list_threads() can still report the
    correct CURRENT_STATE for the stopped flow.
    """
    next_name = ""
    if hasattr(state, "next") and state.next:
        next_name = state.next
    elif hasattr(state, "end") and state.end:
        next_name = "__end__"
    if not next_name:
        return state_dict
    meta = state_dict.get("_meta", {})
    if isinstance(meta, dict):
        state_dict["_meta"] = {**meta, "next_state": next_name}
    else:
        state_dict["_meta"] = {"next_state": next_name}
    return state_dict


def _create_task_node(
    state_name: str, state: TaskState, flow: Flow, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Task state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        from fdsx.core.extraction import extract_value
        from fdsx.providers.base import ProviderResult

        start_time = time.time()
        terminal.display_state_start(
            state_name=state_name,
            state_type="task",
            provider=state.provider,
            model=state.model,
        )

        if recorder is not None:
            recorder.record_state_start(state_name, "task")

        prompt = state.prompt_template or ""
        resolved_prompt = resolve_template(prompt, state_dict)

        provider = get_provider(state.provider)

        max_retries = state.retry if state.retry is not None else 3
        last_error = "No attempts made"
        result = ProviderResult(exit_code=1, stdout="", stderr="")
        extracted: str | None = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                time.sleep(min(2 ** (attempt - 1), 30))
            try:
                if state.provider == "system":
                    resolved_command = resolve_template_shell_safe(
                        state.command or "", state_dict
                    )
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
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                last_error = str(exc)
                result = ProviderResult(exit_code=1, stdout="", stderr=last_error)
                continue

            if result.exit_code == 0:
                if state.extract:
                    extracted = extract_value(
                        result.stdout.strip(),
                        state.extract,
                        get_provider,
                        source_provider=state.provider,
                    )
                    if extracted is not None:
                        break
                    last_error = "Extraction failed: all strategies returned None"
                else:
                    break
            else:
                last_error = result.stderr

        if result.exit_code != 0:
            terminal.display_state_error(state_name, last_error)
            if recorder is not None:
                recorder.record_state_error(state_name, last_error)
            raise RuntimeError(
                f"Provider {state.provider} failed after {max_retries + 1} attempts with exit code {result.exit_code}: {_sanitize_output(last_error)}"
            )

        if state.extract:
            if extracted is None:
                terminal.display_state_error(state_name, last_error)
                if recorder is not None:
                    recorder.record_state_error(state_name, last_error)
                raise RuntimeError(
                    f"Extraction failed after {max_retries + 1} attempts: all strategies returned None"
                )
            new_state = set_jsonpath(state.extract.result_path, state_dict, extracted)
            new_state = set_jsonpath(
                state.result_path, new_state, result.stdout.strip()
            )
            variables_set = [state.extract.result_path, state.result_path]
        else:
            new_state = set_jsonpath(
                state.result_path, state_dict, result.stdout.strip()
            )
            variables_set = [state.result_path]

        duration = time.time() - start_time
        terminal.display_state_complete(state_name, duration)

        if recorder is not None:
            recorder.record_state_complete(
                state_name,
                "success",
                result.stdout,
                variables_set,
            )

        new_state = _set_next_state_meta(new_state, state)
        return new_state

    return node


def _create_choice_node(
    state_name: str, state: ChoiceState, flow: Flow, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Choice state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        if recorder is not None:
            recorder.record_state_start(state_name, "choice")
            recorder.record_state_complete(state_name, "success", "", [])
        return state_dict

    return node


def _create_dispatch_node(
    state_name: str, state: ParallelState, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the dispatch node for a Parallel state.

    Displays the parallel state start line and triggers fan-out via Send.
    Returns {} (no state update) — the fan-out is triggered by the conditional edges.
    Only emits display_parallel_start (not display_state_start) to match CLI contract.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        terminal.display_parallel_start(state_name, len(state.branches))
        if recorder is not None:
            recorder.record_state_start(state_name, "parallel")
        return {}

    return node


def _create_branch_executor(
    state_name: str, state: ParallelState, flow: Flow, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a shared branch executor node invoked once per branch via Send.

    Reads `_branch_index` from the state dict to identify which branch to run.
    Returns `{f"_br_{state_name}": [result]}` — accumulated by operator.add reducer.
    Never raises: all errors are captured in the result dict (exit_code != 0).
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        from fdsx.core.extraction import extract_value
        from fdsx.providers.base import ProviderResult

        branch_index: int = state_dict.get("_branch_index", 0)
        branch = state.branches[branch_index]

        start_time = time.time()
        terminal.display_branch_status(
            state_name=state_name,
            branch_index=branch_index,
            provider=branch.provider,
            status="running",
            duration=None,
        )

        prompt = branch.prompt_template or ""
        resolved_prompt = resolve_template(prompt, state_dict)

        provider = get_provider(branch.provider)

        max_retries = branch.retry if branch.retry is not None else 3
        last_error = "No attempts made"
        result = ProviderResult(exit_code=1, stdout="", stderr="")
        extracted: str | None = None

        for attempt in range(max_retries + 1):
            if attempt > 0:
                time.sleep(min(2 ** (attempt - 1), 30))
            try:
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
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                last_error = str(exc)
                result = ProviderResult(exit_code=1, stdout="", stderr=last_error)
                continue

            if result.exit_code == 0:
                if branch.extract:
                    extracted = extract_value(
                        result.stdout.strip(),
                        branch.extract,
                        get_provider,
                        source_provider=branch.provider,
                    )
                    if extracted is not None:
                        break
                    last_error = "Extraction failed: all strategies returned None"
                else:
                    break
            else:
                last_error = result.stderr

        duration = time.time() - start_time

        if result.exit_code != 0:
            terminal.display_branch_status(
                state_name=state_name,
                branch_index=branch_index,
                provider=branch.provider,
                status="failed",
                duration=duration,
            )
            branch_result: dict[str, Any] = {
                "index": branch_index,
                "output": result.stdout.strip(),
                "exit_code": result.exit_code,
                "error": _sanitize_output(last_error),
                "_duration": duration,
            }
        elif branch.extract and extracted is None:
            terminal.display_branch_status(
                state_name=state_name,
                branch_index=branch_index,
                provider=branch.provider,
                status="failed",
                duration=duration,
            )
            branch_result = {
                "index": branch_index,
                "output": result.stdout.strip(),
                "exit_code": 1,
                "error": f"Extraction failed after {max_retries + 1} attempts: all strategies returned None",
                "_duration": duration,
            }
        else:
            terminal.display_branch_status(
                state_name=state_name,
                branch_index=branch_index,
                provider=branch.provider,
                status="completed",
                duration=duration,
            )
            branch_result = {
                "index": branch_index,
                "output": result.stdout.strip(),
                "exit_code": 0,
                "error": None,
                "_duration": duration,
            }

        if branch.extract and extracted is not None:
            branch_result = set_jsonpath(
                branch.extract.result_path, branch_result, extracted
            )

        return {f"_br_{state_name}": [branch_result]}

    return node


def _create_fan_out(
    state_name: str, state: ParallelState
) -> Callable[[dict[str, Any]], list[Send]]:
    """Create the fan-out function that returns one Send per branch.

    Each Send carries a fresh `_br_{state_name}: []` reset so that accumulation
    from a previous pass through this parallel state (in a loop) does not bleed
    into the current pass.
    """

    def fan_out(state_dict: dict[str, Any]) -> list[Send]:
        return [
            Send(
                f"_branch_{state_name}",
                {**state_dict, "_branch_index": i, f"_br_{state_name}": []},
            )
            for i in range(len(state.branches))
        ]

    return fan_out


def _create_collector_node(
    state_name: str, state: ParallelState, flow: Flow, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the fan-in collector node for a Parallel state.

    Reads branch results accumulated by the operator.add reducer, sorts by index,
    enforces min_success (defaulting to all branches), and stores at result_path.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        start_time = time.time()

        raw_results: list[dict[str, Any]] = state_dict.get(f"_br_{state_name}", [])

        sorted_results = sorted(raw_results, key=lambda r: r.get("index", 0))

        branch_info_list: list[dict[str, Any]] = []
        for r in sorted_results:
            branch_info_list.append(
                {
                    "index": r.get("index", 0),
                    "provider": state.branches[r.get("index", 0)].provider
                    if r.get("index", 0) < len(state.branches)
                    else "unknown",
                    "status": "success" if r.get("exit_code") == 0 else "error",
                    "duration_seconds": int(r.get("_duration", 0)),
                }
            )

        clean_results = [
            {k: v for k, v in r.items() if k != "index" and k != "_duration"}
            for r in sorted_results
        ]

        min_required = (
            state.min_success if state.min_success is not None else len(state.branches)
        )
        successful = sum(1 for r in clean_results if r.get("exit_code") == 0)

        if successful < min_required:
            failed_branches = [
                f"branch {i}: {r.get('error', 'unknown error')}"
                for i, r in enumerate(clean_results)
                if r.get("exit_code") != 0
            ]
            if recorder is not None:
                recorder.record_state_complete(
                    state_name,
                    "error",
                    f"Only {successful}/{len(state.branches)} branches succeeded",
                    [state.result_path],
                    branch_info_list,
                )
            raise RuntimeError(
                f"Parallel state '{state_name}' failed: only {successful}/{len(state.branches)} "
                f"branches succeeded, required {min_required}. "
                f"Failed branches: {'; '.join(failed_branches)}"
            )

        new_state = set_jsonpath(state.result_path, state_dict, clean_results)

        duration = time.time() - start_time
        terminal.display_state_complete(state_name, duration)

        if recorder is not None:
            recorder.record_state_complete(
                state_name,
                "success",
                "",
                [state.result_path],
                branch_info_list,
            )

        new_state = _set_next_state_meta(new_state, state)
        return new_state

    return node


def _aggregate(source_data: list[dict[str, Any]], rule: AggregateRule) -> str:
    """Aggregate parallel results using majority/all/any strategy.

    Args:
        source_data: List of branch result dictionaries (already resolved from state)
        rule: AggregateRule with field, strategy, match/no_match values

    Returns:
        The aggregated result (rule.match or rule.no_match)

    Security note: Uses total branch count (len(source_data)) as the denominator,
    so failed branches that did not produce the field count as no_match votes.
    This prevents bypassing a dissenting reviewer by knocking out their branch.
    """
    if not source_data:
        return rule.no_match

    if not isinstance(source_data, list):
        return rule.no_match

    # Use total branch count as denominator — failed/missing branches count as no_match
    total = len(source_data)

    match_count = 0
    for item in source_data:
        if isinstance(item, dict) and rule.field in item:
            if str(item[rule.field]) == str(rule.match):
                match_count += 1
        # Items without the field (failed branches) are treated as no_match

    if rule.strategy == "majority":
        return rule.match if match_count > total / 2 else rule.no_match
    elif rule.strategy == "all":
        return rule.match if match_count == total else rule.no_match
    elif rule.strategy == "any":
        return rule.match if match_count > 0 else rule.no_match
    else:
        raise ValueError(f"Unknown aggregation strategy: {rule.strategy}")


def _create_pass_node(
    state_name: str, state: PassState, flow: Flow, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a LangGraph node function for a Pass state."""

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        if recorder is not None:
            recorder.record_state_start(state_name, "pass")

        variables_set = []
        if state.parameters:
            for target, source in state.parameters.items():
                if isinstance(source, str):
                    value = resolve_template(source, state_dict)
                else:
                    value = source
                new_state = set_jsonpath(target, state_dict, value)
                state_dict = new_state
                variables_set.append(target)

        if state.aggregate:
            from fdsx.core.variables import resolve_jsonpath

            source_data = resolve_jsonpath(state.aggregate.source, state_dict)
            if isinstance(source_data, list):
                result = _aggregate(source_data, state.aggregate)
            else:
                result = state.aggregate.no_match
            state_dict = set_jsonpath(state.aggregate.result_path, state_dict, result)
            variables_set.append(state.aggregate.result_path)

        if recorder is not None:
            recorder.record_state_complete(state_name, "success", "", variables_set)

        state_dict = _set_next_state_meta(state_dict, state)
        return state_dict

    return node


def _create_wait_notify_node(
    state_name: str, state: WaitState, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the pre-interrupt notify node for a Wait state.

    Sends the webhook notification (if configured) and returns the state so the
    result is checkpointed before the interrupt.  This guarantees the notification
    fires exactly once: on the first entry the checkpoint advances past this node,
    so on resume LangGraph replays only the interrupt node — not this one.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        if recorder is not None:
            recorder.record_state_start(state_name, "wait")

        if state.notify is not None:
            from fdsx.notify.webhook import send_notification

            send_notification(state.notify, state_dict)
        return state_dict

    return node


def _create_wait_interrupt_node(
    state_name: str, state: WaitState, recorder: Any = None
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the interrupt node for a Wait state.

    Uses LangGraph's interrupt() to pause execution and wait for user input.
    The engine handles the actual prompting and resume with Command(resume=value).
    Only this node is re-executed on resume; the notify node above is not.
    """

    def node(state_dict: dict[str, Any]) -> dict[str, Any]:
        resolved_message = resolve_template(state.message, state_dict)

        user_selection = interrupt(
            {
                "message": resolved_message,
                "choices": state.choices,
                "state_name": state_name,
            }
        )

        new_state = set_jsonpath(state.result_path, state_dict, user_selection)

        if recorder is not None:
            recorder.record_state_complete(
                state_name,
                "success",
                user_selection,
                [state.result_path],
                state_type="wait",
            )

        new_state = _set_next_state_meta(new_state, state)
        return new_state

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

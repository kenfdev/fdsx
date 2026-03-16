import re
import shlex
from typing import Any

from fdsx.models.flow import Flow, State


def resolve_template(template: str, variables: dict[str, Any]) -> str:
    """Resolve {variable} patterns in a template string.

    Only replaces registered variable names. Unknown {...} patterns
    are preserved as literals.
    """
    pattern = re.compile(r"\{([^}]+)\}")

    def replace_match(match: re.Match[str]) -> str:
        var_path = match.group(1)
        value = resolve_jsonpath(var_path, variables)
        if value is None:
            return match.group(0)
        return str(value)

    return pattern.sub(replace_match, template)


def resolve_template_shell_safe(template: str, variables: dict[str, Any]) -> str:
    """Resolve {variable} patterns with shell-safe quoting of values.

    Each interpolated value is passed through shlex.quote() to prevent
    shell injection when the result is used in sh -c commands.
    Unknown {...} patterns are preserved as literals.
    """
    pattern = re.compile(r"\{([^}]+)\}")

    def replace_match(match: re.Match[str]) -> str:
        var_path = match.group(1)
        value = resolve_jsonpath(var_path, variables)
        if value is None:
            return match.group(0)
        return shlex.quote(str(value))

    return pattern.sub(replace_match, template)


def resolve_jsonpath(path: str, data: dict[str, Any]) -> Any:
    """Resolve a JSONPath-like path in data.

    Supports dot notation and array indexing.
    Example: 'reviews[0].summary', 'plan', 'items[2]'
    """
    if not path:
        return data

    path = path.strip()
    if path.startswith("$."):
        path = path[2:]

    current = data

    parts = _parse_jsonpath(path)

    for part in parts:
        if isinstance(current, dict):
            if part not in current:
                return None
            if isinstance(part, str):
                current = current[part]
            else:
                return None
        elif isinstance(current, list):
            if not isinstance(part, int):
                return None
            if part < 0 or part >= len(current):
                return None
            current = current[part]
        else:
            return None

    return current


def _parse_jsonpath(path: str) -> list[str | int]:
    """Parse a JSONPath string into parts.

    Converts 'reviews[0].summary' into ['reviews', 0, 'summary']
    """
    parts: list[str | int] = []
    current = ""
    i = 0

    while i < len(path):
        char = path[i]

        if char == ".":
            if current:
                parts.append(current)
                current = ""
        elif char == "[":
            if current:
                parts.append(current)
                current = ""
            i += 1
            bracket_content = ""
            while i < len(path) and path[i] != "]":
                bracket_content += path[i]
                i += 1
            if bracket_content:
                try:
                    parts.append(int(bracket_content))
                except ValueError:
                    parts.append(bracket_content.strip("\"'"))
        else:
            current += char

        i += 1

    if current:
        parts.append(current)

    return parts


def set_jsonpath(path: str, data: dict[str, Any], value: Any) -> dict[str, Any]:
    """Set a value at a JSONPath location.

    Creates nested structures as needed.
    """
    if not path:
        result: dict[str, Any] = {}
        result[""] = value
        return result

    if path.startswith("$."):
        path = path[2:]

    parts = _parse_jsonpath(path)

    if not parts:
        result = {}
        result[""] = value
        return result

    if not data:
        data = {}

    result = dict(data)
    current: Any = result

    for i, part in enumerate(parts[:-1]):
        if isinstance(part, int):
            if not isinstance(current, list):
                raise ValueError(f"Cannot index into non-list at position {i}")
            if part < len(current):
                next_val = current[part]
                new_val = (
                    dict(next_val)
                    if isinstance(next_val, dict)
                    else list(next_val)
                    if isinstance(next_val, list)
                    else {}
                )
                current[part] = new_val
                current = new_val
            else:
                while len(current) <= part:
                    current.append({})
                current = current[part]
        else:
            if not isinstance(current, dict):
                raise ValueError(f"Cannot access dict key in non-dict at position {i}")
            if part not in current or not isinstance(current[part], (dict, list)):
                current[part] = {}
                current = current[part]
            else:
                next_val = current[part]
                new_val = (
                    dict(next_val) if isinstance(next_val, dict) else list(next_val)
                )
                current[part] = new_val
                current = new_val

    last_part = parts[-1]
    if isinstance(last_part, int):
        if not isinstance(current, list):
            raise ValueError("Cannot index into non-list at last position")
        while len(current) <= last_part:
            current.append(None)
        current[last_part] = value
    else:
        if not isinstance(current, dict):
            raise ValueError("Cannot set dict key in non-dict")
        current[last_part] = value

    return result


def _is_var_satisfied(var: str, available: set[str]) -> bool:
    """Check if a variable reference is satisfied by the available paths.

    Uses parsed JSONPath segments so both dot and bracket notation are handled.

    Rules:
    - Exact match: ``review`` satisfied by ``review``
    - Ancestor: ``review.decision`` satisfied by ``review`` (whole subtree provided)
    - Descendant: ``review`` satisfied by ``review.summary`` (parent object exists)
    - Bracket notation: ``reviews[0].summary`` satisfied by ``reviews``
    """
    var_parts = _parse_jsonpath(var)
    for provided in available:
        prov_parts = _parse_jsonpath(provided)
        # Exact match
        if var_parts == prov_parts:
            return True
        # Provided is ancestor: prov_parts is a proper prefix of var_parts
        if (
            len(prov_parts) < len(var_parts)
            and var_parts[: len(prov_parts)] == prov_parts
        ):
            return True
        # Provided is descendant: var_parts is a proper prefix of prov_parts
        if (
            len(var_parts) < len(prov_parts)
            and prov_parts[: len(var_parts)] == var_parts
        ):
            return True
    return False


def analyze_variable_references(flow: Flow, input_keys: set[str] | None = None) -> list[str]:
    """Static analysis to detect unreachable variable references.

    Traces reachable states from start_at and checks that {variable}
    references in prompts correspond to a result_path set by a
    preceding state on at least one reachable path.
    """
    errors: list[str] = []

    def get_next_states(state: State) -> set[str]:
        result = set()
        from fdsx.models.flow import (
            TaskState,
            ChoiceState,
            ParallelState,
            PassState,
            WaitState,
        )

        if isinstance(state, TaskState):
            if state.next:
                result.add(state.next)
            if state.end:
                result.add("$END")
        elif isinstance(state, ChoiceState):
            for choice in state.choices:
                result.add(choice.next)
            if state.default:
                result.add(state.default)
        elif isinstance(state, ParallelState):
            if state.next:
                result.add(state.next)
            if state.end:
                result.add("$END")
        elif isinstance(state, PassState):
            if state.next:
                result.add(state.next)
            if state.end:
                result.add("$END")
        elif isinstance(state, WaitState):
            if state.next:
                result.add(state.next)
            if state.end:
                result.add("$END")
        return result

    def get_prompt_variables(state: State) -> set[str]:
        variables: set[str] = set()
        from fdsx.models.flow import TaskState, Branch

        if isinstance(state, TaskState):
            prompt = state.prompt_template or ""
            command = state.command or ""
            prompt = prompt + " " + command
        elif isinstance(state, Branch):
            prompt = state.prompt_template or ""
            command = state.command or ""
            prompt = prompt + " " + command
        else:
            return variables

        pattern = re.compile(r"\{([^}]+)\}")
        for match in pattern.finditer(prompt):
            var_path = match.group(1)
            # Preserve full path (strip only leading "$." JSONPath prefix if present)
            if var_path.startswith("$."):
                var_path = var_path[2:]
            variables.add(var_path)

        return variables

    def get_result_paths(state: State) -> set[str]:
        result_paths: set[str] = set()
        from fdsx.models.flow import TaskState, Branch, ParallelState

        if isinstance(state, TaskState):
            if state.result_path:
                path = state.result_path
                if path.startswith("$."):
                    path = path[2:]
                result_paths.add(path)  # full path, not just root key
        elif isinstance(state, Branch):
            result_paths.add("branch_result")
        elif isinstance(state, ParallelState):
            if state.result_path:
                path = state.result_path
                if path.startswith("$."):
                    path = path[2:]
                result_paths.add(path)  # full path, not just root key
        return result_paths

    reachable_states = set()
    state_queue = [flow.start_at]

    while state_queue:
        current = state_queue.pop(0)
        if current in reachable_states:
            continue
        if current == "$END":
            continue
        if current not in flow.states:
            errors.append(f"State '{current}' referenced but not defined")
            continue

        reachable_states.add(current)
        state = flow.states[current]
        next_states = get_next_states(state)
        state_queue.extend(next_states - reachable_states)

    state_provides: dict[str, set[str]] = {}
    for state_name in reachable_states:
        state = flow.states[state_name]
        state_provides[state_name] = get_result_paths(state)

    predecessors: dict[str, set[str]] = {s: set() for s in reachable_states}
    for state_name in reachable_states:
        state = flow.states[state_name]
        next_states = get_next_states(state)
        for next_state in next_states:
            if next_state in predecessors:
                predecessors[next_state].add(state_name)

    available_vars: dict[str, set[str]] = {s: set() for s in reachable_states}
    changed = True
    while changed:
        changed = False
        for state_name in reachable_states:
            preds = predecessors.get(state_name, set())
            new_vars = set()
            for pred in preds:
                new_vars.update(available_vars.get(pred, set()))
                new_vars.update(state_provides.get(pred, set()))
            if new_vars != available_vars[state_name]:
                available_vars[state_name] = new_vars
                changed = True

    # Seed all reachable states with CLI --input keys
    if input_keys:
        for state_name in reachable_states:
            available_vars[state_name] = available_vars[state_name] | input_keys

    for state_name in reachable_states:
        state = flow.states[state_name]
        prompt_vars = get_prompt_variables(state)

        for var in prompt_vars:
            if (
                not _is_var_satisfied(var, available_vars.get(state_name, set()))
                and state_name != flow.start_at
            ):
                errors.append(
                    f"State '{state_name}' references variable '{var}' "
                    f"but no preceding state sets a result_path for it"
                )

    return errors

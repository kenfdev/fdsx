import sys
from datetime import datetime
from typing import Any


def _sanitize_output(text: str) -> str:
    """Strip control characters from text, preserving only printable content.

    Uses a whitelist approach: allows tab and newline, strips everything
    else in C0 (0x00-0x1F), DEL (0x7F), and C1 (0x80-0x9F) ranges.
    This catches all ANSI/OSC/CSI escape sequences by removing the ESC byte.
    """
    return "".join(
        ch
        for ch in text
        if ch in ("\t", "\n")
        or (ch >= " " and ch != "\x7f" and not ("\x80" <= ch <= "\x9f"))
    )


def display_state_start(
    state_name: str,
    state_type: str,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Display state start in terminal.

    Args:
        state_name: Name of the state
        state_type: Type of state (task, choice, parallel, etc.)
        provider: Provider name (for task states)
        model: Model name (for LLM providers)
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    provider_info = ""
    if provider:
        model_info = f"/{model}" if model else ""
        provider_info = f"/{provider}{model_info}"

    line = f"[{timestamp}] ▶ {state_name} ({state_type}{provider_info})"
    print(line, file=sys.stderr)


def display_state_complete(state_name: str, duration_seconds: float) -> None:
    """Display state completion in terminal.

    Args:
        state_name: Name of the completed state
        duration_seconds: Duration of execution
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] ✓ {state_name} completed ({int(duration_seconds)}s)"
    print(line, file=sys.stderr)


def display_state_error(state_name: str, error: str) -> None:
    """Display state error in terminal.

    Args:
        state_name: Name of the failed state
        error: Error message
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] ✗ {state_name} failed"
    print(line, file=sys.stderr)
    print(f"  Error: {_sanitize_output(error)}", file=sys.stderr)


def display_output_line(line: str) -> None:
    """Display LLM output line to terminal.

    Args:
        line: Output line from the LLM
    """
    print(_sanitize_output(line), file=sys.stderr)


def display_parallel_start(state_name: str, branch_count: int) -> None:
    """Display parallel state start in terminal.

    Args:
        state_name: Name of the parallel state
        branch_count: Number of parallel branches
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] ▶ {state_name} (parallel, {branch_count} branches)"
    print(line, file=sys.stderr)


def display_branch_start(
    state_name: str,
    branch_index: int,
    provider: str,
    model: str | None = None,
) -> None:
    """Display branch start in terminal.

    Args:
        state_name: Name of the parent parallel state
        branch_index: Index of the branch (0-based)
        provider: Provider name for the branch
        model: Model name for the branch (optional)
    """
    display_index = branch_index + 1
    sanitized_model = _sanitize_output(model) if model else None
    model_info = f"/{sanitized_model}" if sanitized_model else ""
    line = f"  [branch-{display_index}] {provider}{model_info}  ⏳ running..."
    print(line, file=sys.stderr)


def display_branch_complete(
    state_name: str,
    branch_index: int,
    provider: str,
    model: str | None = None,
    duration: float | None = None,
) -> None:
    """Display branch completion in terminal.

    Args:
        state_name: Name of the parent parallel state
        branch_index: Index of the branch (0-based)
        provider: Provider name for the branch
        model: Model name for the branch (optional)
        duration: Duration in seconds (optional, for display)
    """
    display_index = branch_index + 1
    sanitized_model = _sanitize_output(model) if model else None
    model_info = f"/{sanitized_model}" if sanitized_model else ""
    duration_info = f" ({int(duration)}s)" if duration is not None else ""
    line = (
        f"  [branch-{display_index}] {provider}{model_info}  ✓ completed{duration_info}"
    )
    print(line, file=sys.stderr)


def display_branch_failed(
    state_name: str,
    branch_index: int,
    provider: str,
    model: str | None = None,
) -> None:
    """Display branch failure in terminal.

    Args:
        state_name: Name of the parent parallel state
        branch_index: Index of the branch (0-based)
        provider: Provider name for the branch
        model: Model name for the branch (optional)
    """
    display_index = branch_index + 1
    sanitized_model = _sanitize_output(model) if model else None
    model_info = f"/{sanitized_model}" if sanitized_model else ""
    line = f"  [branch-{display_index}] {provider}{model_info}  ✗ failed"
    print(line, file=sys.stderr)


def display_parallel_results(
    state_name: str,
    branch_results: list[dict[str, Any]],
) -> None:
    """Display parallel branch results in terminal.

    Args:
        state_name: Name of the parallel state
        branch_results: List of branch result dictionaries
    """
    print("", file=sys.stderr)
    for result in branch_results:
        branch_index = result.get("index", 0)
        display_index = branch_index + 1
        provider = result.get("provider", "unknown")
        model = result.get("model")
        output = result.get("output", "")
        exit_code = result.get("exit_code", 1)

        sanitized_provider = _sanitize_output(provider) if provider else "unknown"
        sanitized_model = _sanitize_output(model) if model else None
        model_info = f"/{sanitized_model}" if sanitized_model else ""

        if exit_code == 0:
            header = (
                f"--- branch-{display_index} ({sanitized_provider}{model_info}) ---"
            )
        else:
            header = f"--- branch-{display_index} ({sanitized_provider}{model_info}) FAILED ---"

        print(_sanitize_output(header), file=sys.stderr)
        if output:
            for line in output.splitlines():
                print(_sanitize_output(line), file=sys.stderr)


def display_wait_prompt(state_name: str, message: str, choices: list[str]) -> str:
    """Display a wait prompt and get user selection.

    Args:
        state_name: Name of the wait state
        message: Message to display to the user
        choices: List of choice options

    Returns:
        The selected choice string (not the number)
    """
    if not choices:
        raise ValueError("choices must not be empty")

    timestamp = datetime.now().strftime("%H:%M:%S")
    print(
        f"[{timestamp}] ⏸ {_sanitize_output(state_name)} (waiting for input)",
        file=sys.stderr,
    )
    print("", file=sys.stderr)
    for line in _sanitize_output(message).splitlines():
        print(f"  {line}", file=sys.stderr)
    print("", file=sys.stderr)

    for i, choice in enumerate(choices, start=1):
        print(f"  [{i}] {_sanitize_output(choice)}", file=sys.stderr)

    print("", file=sys.stderr)

    while True:
        try:
            sys.stderr.write(f"  Select (1-{len(choices)}): ")
            sys.stderr.flush()
            user_input = input()
            choice_num = int(user_input)
            if 1 <= choice_num <= len(choices):
                return choices[choice_num - 1]
            print(
                f"Invalid choice. Please enter a number between 1 and {len(choices)}.",
                file=sys.stderr,
            )
        except ValueError:
            print(
                f"Invalid input. Please enter a number between 1 and {len(choices)}.",
                file=sys.stderr,
            )

import sys
from datetime import datetime


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

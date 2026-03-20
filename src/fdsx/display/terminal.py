import sys
import threading
from datetime import datetime
from typing import Any, TextIO


def is_interactive() -> bool:
    """Check if stderr is connected to an interactive terminal.

    Returns:
        True if stderr is a TTY, False otherwise.
    """
    return sys.stderr.isatty()


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


def _sanitize_spinner_text(text: str) -> str:
    """Sanitize text for single-line spinner display.

    Strips control characters via _sanitize_output(), then replaces
    newlines and tabs with spaces to prevent log/terminal line injection.
    """
    sanitized = _sanitize_output(text)
    return sanitized.replace("\n", " ").replace("\r", " ").replace("\t", " ")


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


class Spinner:
    """Terminal spinner with TTY and non-TTY fallback modes.

    In TTY mode: displays rotating spinner characters that update in place.
    In non-TTY mode: prints a simple message without animation (CI/log friendly).

    Attributes:
        _FRAMES: Sequence of spinner characters for TTY mode.
        _stream: Output stream (defaults to sys.stderr).
        _message: Current message to display.
        _interactive: Whether stderr is connected to an interactive terminal (via is_interactive()).
        _stop_event: Threading event to signal spinner stop.
        _thread: Background thread running the spinner animation (TTY mode only).
        _running: Whether the spinner is currently active.
    """

    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "", stream: TextIO | None = None) -> None:
        """Initialize the spinner.

        Args:
            message: Initial message to display next to the spinner.
            stream: Output stream to write to. Defaults to sys.stderr.
        """
        self._stream = stream if stream is not None else sys.stderr
        self._message = message
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._interactive = is_interactive()

    def start(self) -> "Spinner":
        """Start the spinner.

        In TTY mode: spawns a background thread that animates the spinner.
        In non-TTY mode: prints the message once and returns immediately (no thread).

        Returns:
            self, to allow use as a context manager value.
        """
        if self._running:
            self.stop()
        if self._interactive:
            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            self._stream.write(f"{_sanitize_spinner_text(self._message)}\n")
            self._stream.flush()
        return self

    def stop(self, final_message: str = "") -> None:
        """Stop the spinner.

        In TTY mode: joins the background thread, clears the spinner line,
        and optionally prints a final message.
        In non-TTY mode: optionally prints a final message.

        Args:
            final_message: Optional message to print after stopping.
        """
        if self._interactive:
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=1.0)
                self._thread = None
            self._running = False
            self._stream.write("\r\033[K")
            if final_message:
                self._stream.write(f"{_sanitize_spinner_text(final_message)}\n")
            self._stream.flush()
        else:
            if final_message:
                self._stream.write(f"{_sanitize_spinner_text(final_message)}\n")
                self._stream.flush()

    def update(self, message: str) -> None:
        """Update the spinner message.

        In TTY mode: updates the message shown on the next frame tick.
        In non-TTY mode: immediately prints the new message as a new line.

        Args:
            message: New message to display.
        """
        self._message = message
        if not self._interactive:
            self._stream.write(f"{_sanitize_spinner_text(message)}\n")
            self._stream.flush()

    def _run(self) -> None:
        """Main spinner loop running in background thread (TTY mode only)."""
        idx = 0
        while not self._stop_event.is_set():
            frame = self._FRAMES[idx % len(self._FRAMES)]
            self._stream.write(f"\r{frame} {_sanitize_spinner_text(self._message)}")
            self._stream.flush()
            idx += 1
            self._stop_event.wait(0.08)

    def __enter__(self) -> "Spinner":
        """Start spinner when used as a context manager."""
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Stop spinner when exiting the context manager."""
        self.stop()

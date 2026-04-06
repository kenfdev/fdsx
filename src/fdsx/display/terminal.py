import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, TextIO


def is_interactive() -> bool:
    """Check if stderr is connected to an interactive terminal.

    Returns:
        True if stderr is a TTY, False otherwise.
    """
    return sys.stderr.isatty()


def _sanitize_output(text: str) -> str:
    """Strip control characters and ANSI escape sequences from text.

    Uses a whitelist approach: allows tab and newline, strips everything
    else in C0 (0x00-0x1F), DEL (0x7F), and C1 (0x80-0x9F) ranges.
    Also strips complete ANSI escape sequences (ESC + bracket + params + letter).
    """
    result = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\x1b" and i + 1 < len(text) and text[i + 1] == "[":
            j = i + 2
            while j < len(text) and text[j] in "0123456789;?":
                j += 1
            if j < len(text) and text[j] in (
                "A",
                "B",
                "C",
                "D",
                "E",
                "F",
                "G",
                "H",
                "J",
                "K",
                "S",
                "T",
                "f",
                "m",
                "n",
                "s",
                "u",
            ):
                i = j + 1
                continue
            else:
                i += 1
                continue
        if ch in ("\t", "\n") or (
            ch >= " " and ch != "\x7f" and not ("\x80" <= ch <= "\x9f")
        ):
            result.append(ch)
        i += 1
    return "".join(result)


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


def _format_elapsed(seconds: float) -> str:
    """Format elapsed time as a human-readable string.

    Args:
        seconds: Elapsed time in seconds

    Returns:
        Formatted string like '34s', '2m 34s', or '1h 2m 34s'
    """
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    else:
        return f"{s}s"


def display_completion_summary(
    flow_name: str,
    elapsed_seconds: float,
    failed_state: str | None = None,
    error: str | None = None,
) -> None:
    """Display workflow completion summary to stderr.

    On success, prints a single line with flow name and elapsed time.
    On failure, prints flow name, the failed state name, and a brief error.

    Args:
        flow_name: Name of the workflow
        elapsed_seconds: Total elapsed time in seconds
        failed_state: Name of the failed state (None on success)
        error: Error message (None on success)
    """
    flow_name_safe = _sanitize_output(flow_name)
    time_str = _format_elapsed(elapsed_seconds)
    if failed_state is None:
        print(
            f"✓ Workflow '{flow_name_safe}' completed successfully in {time_str}",
            file=sys.stderr,
        )
    else:
        failed_state_safe = _sanitize_output(failed_state)
        error_str = _sanitize_output(error) if error else "unknown error"
        print(
            f"✗ Workflow '{flow_name_safe}' failed at state '{failed_state_safe}' — {error_str}",
            file=sys.stderr,
        )


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

    _FRAMES: ClassVar[list[str]] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

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


def confirm_workflow_assignments_interactive(
    display_keys: list[tuple[int, int]],
    workflow_assignments: dict[tuple[int, int], Path],
    task_files: list[tuple[Path, Any]],
    available_workflows: list[tuple[Path, str, str]],
    stream: TextIO | None = None,
) -> dict[tuple[int, int], Path] | None:
    """Present an interactive numbered-list CUI for workflow assignment confirmation.

    Displays a table of all entries (assigned and unassigned), allows the user
    to change individual assignments by number, and returns the (possibly
    modified) assignments dict on confirm or None on cancel.

    Args:
        display_keys: Ordered list of (file_idx, entry_idx) keys to display.
        workflow_assignments: Map of (file_idx, entry_idx) -> workflow path.
        task_files: List of (file_path, task_file) tuples.
        available_workflows: List of (workflow_path, description, display_name) tuples.
        stream: Output stream for prompts (defaults to sys.stderr).

    Returns:
        The confirmed assignments dict on 'c' confirm,
        or None if the user cancelled with 'q'.
    """
    if stream is None:
        stream = sys.stderr

    if not is_interactive():
        return dict(workflow_assignments)

    wf_display_map = {
        wf_path: display_name
        for wf_path, _, display_name in (available_workflows or [])
    }

    assignments = dict(workflow_assignments)

    while True:
        stream.write("\n")
        stream.write("=" * 79 + "\n")
        stream.write("WORKFLOW ASSIGNMENTS\n")
        stream.write("=" * 79 + "\n")
        stream.write(
            f"{'#':<4} {'FILE':<30} {'ENTRY':<6} {'WORKFLOW':<30} {'TASK':<15}\n"
        )
        stream.write("-" * 79 + "\n")

        for row_num, key in enumerate(display_keys, start=1):
            file_idx, entry_idx = key
            file_path, task_file = task_files[file_idx]
            entry = task_file.entries[entry_idx]
            wf_path = assignments.get(key)
            wf_name = (
                wf_display_map.get(wf_path, wf_path.name) if wf_path else "(unassigned)"
            )

            file_name = file_path.name[:29]
            entry_num = str(entry_idx + 1)
            task_preview = _sanitize_output(entry.description)[:14]

            stream.write(
                f"{row_num:<4} "
                f"{_sanitize_output(file_name):<30} "
                f"{entry_num:<6} "
                f"{_sanitize_output(wf_name):<30} "
                f"{_sanitize_output(task_preview):<15}\n"
            )

        stream.write("=" * 79 + "\n")

        has_unassigned = any(key not in assignments for key in display_keys)

        if has_unassigned:
            stream.write(
                "Enter number to change workflow, 'c' to confirm, 'q' to cancel\n"
            )
            stream.write(
                "(Note: unassigned entries must be assigned before confirming)\n"
            )
        else:
            stream.write(
                "Enter number to change workflow, 'c' to confirm, 'q' to cancel: "
            )

        stream.flush()
        user_input = input().strip()

        if user_input.lower() == "c":
            if has_unassigned:
                unassigned_count = sum(
                    1 for key in display_keys if key not in assignments
                )
                stream.write(
                    f"Cannot confirm: {unassigned_count} task(s) have no "
                    f"workflow assigned. Assign workflows to all tasks first.\n"
                )
                stream.flush()
                continue
            return assignments

        if user_input.lower() == "q":
            return None

        try:
            row_num = int(user_input)
            if not (1 <= row_num <= len(display_keys)):
                stream.write(f"Invalid number. Enter 1-{len(display_keys)}.\n")
                stream.flush()
                continue
        except ValueError:
            stream.write("Invalid input. Enter a number, 'c', or 'q'.\n")
            stream.flush()
            continue

        key = display_keys[row_num - 1]

        if not available_workflows:
            stream.write("No alternative workflows available to select.\n")
            stream.flush()
            continue

        stream.write("\nAvailable workflows:\n")
        stream.write("-" * 60 + "\n")
        for i, (_wf_path, description, display_name) in enumerate(
            available_workflows, 1
        ):
            desc_preview = (
                description[:50] + "..." if len(description) > 50 else description
            )
            stream.write(f"  {i}. {_sanitize_output(display_name)}\n")
            stream.write(f"      {_sanitize_output(desc_preview)}\n")
        stream.write("-" * 60 + "\n")
        stream.write("Enter number (or 'c' to cancel): ")
        stream.flush()

        pick_input = input().strip()
        if pick_input.lower() == "c":
            continue
        try:
            pick_idx = int(pick_input) - 1
            if 0 <= pick_idx < len(available_workflows):
                assignments[key] = available_workflows[pick_idx][0]
            else:
                stream.write(f"Invalid number. Enter 1-{len(available_workflows)}.\n")
                stream.flush()
        except ValueError:
            stream.write("Invalid input. Enter a number.\n")
            stream.flush()


def display_resume_command(
    mode: str,
    thread_id: str | None = None,
    tasks_dir: Path | None = None,
    extra_args: list[str] | None = None,
    stream: TextIO | None = None,
) -> None:
    """Display a box-formatted resume command for error/interrupt scenarios.

    Args:
        mode: Either "single-flow" or "tasks-dir".
        thread_id: Thread ID for single-flow mode.
        tasks_dir: Tasks directory for tasks-dir mode.
        extra_args: Additional command-line arguments to include.
        stream: Output stream (defaults to sys.stderr).
    """
    if stream is None:
        stream = sys.stderr

    if mode == "single-flow":
        if thread_id is None:
            raise ValueError("thread_id is required for single-flow mode")
        cmd_parts = ["fdsx", "resume", "--thread-id", _sanitize_output(thread_id)]
    elif mode == "tasks-dir":
        if tasks_dir is None:
            raise ValueError("tasks_dir is required for tasks-dir mode")
        cmd_parts = ["fdsx", "run", "--tasks-dir", _sanitize_output(str(tasks_dir))]
    else:
        raise ValueError(f"Invalid mode: {mode}. Use 'single-flow' or 'tasks-dir'.")

    if extra_args:
        for arg in extra_args:
            cmd_parts.append(_sanitize_output(arg))

    command = " ".join(cmd_parts)

    width = max(len(command) + 4, 50)
    border = "+" + "-" * (width - 2) + "+"
    padding = " " * (width - 2)

    stream.write("\n")
    stream.write(border + "\n")
    if mode == "single-flow":
        stream.write(f"|{padding}|\n")
        stream.write("|  To resume this flow, run:\n")
        stream.write(f"|  $ {command}\n")
        stream.write(f"|{padding}|\n")
    else:
        stream.write(f"|{padding}|\n")
        stream.write("|  To continue processing, run:\n")
        stream.write(f"|  $ {command}\n")
        stream.write(f"|{padding}|\n")
    stream.write(border + "\n")
    stream.write("\n")
    stream.flush()

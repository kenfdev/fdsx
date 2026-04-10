import sys

_interactive_mode: bool | None = None


def set_interactive_mode(mode: bool | None) -> None:
    """Set the interactive mode state.

    Args:
        mode: True for interactive, False for non-interactive, None to use TTY detection.
    """
    global _interactive_mode
    _interactive_mode = mode


def get_interactive_mode() -> bool | None:
    """Return the raw interactive mode state.

    Returns:
        The explicitly set mode value, or None if not set.
    """
    return _interactive_mode


def is_interactive() -> bool:
    """Check if the session should run in interactive mode.

    If interactive mode was explicitly set (True or False), returns that value.
    Otherwise, falls back to checking if stdin is a TTY.

    Returns:
        True if interactive mode is enabled, False otherwise.
    """
    if _interactive_mode is not None:
        return _interactive_mode
    return sys.stdin.isatty()

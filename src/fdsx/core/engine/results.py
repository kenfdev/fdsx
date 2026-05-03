"""Result extraction and helper utilities for the engine package."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fdsx.logging import RunRecorder


def _extract_results(state: dict[str, Any], result_paths: list[str]) -> dict[str, Any]:
    """Extract result values from final state preserving nested paths."""
    from fdsx.core.variables import resolve_jsonpath, set_jsonpath

    results: dict[str, Any] = {}
    for path in result_paths:
        clean_path = path[2:] if path.startswith("$.") else path
        value = resolve_jsonpath(clean_path, state)
        if value is not None:
            results = set_jsonpath(clean_path, results, value)

    return results


def _sanitize_state_for_log(state: dict[str, Any]) -> dict[str, Any]:
    """Create a sanitized copy of state for logging, stripping internal keys."""
    return {
        k: v
        for k, v in state.items()
        if not k.startswith("_meta")
        and not k.startswith("__")
        and not k.startswith("_br_")
        and not k.startswith("_state_")
    }


def _calc_elapsed(recorder: RunRecorder) -> float:
    """Calculate elapsed seconds between recorder.started_at and completed_at.

    Falls back to current time if completed_at is not set.

    Args:
        recorder: The RunRecorder instance

    Returns:
        Elapsed time in seconds as a float
    """
    try:
        start = datetime.fromisoformat(recorder.started_at.replace("Z", "+00:00"))
        end_str = recorder.completed_at
        end = (
            datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if end_str is not None
            else datetime.now(timezone.utc)
        )
        return (end - start).total_seconds()
    except (ValueError, TypeError):
        return 0.0


def _find_failed_state(recorder: RunRecorder) -> tuple[str, str] | None:
    """Return (state_name, error_message) for the most recent error state.

    Searches recorder.states in reverse order for the first state with
    status=="error".

    Args:
        recorder: The RunRecorder instance

    Returns:
        Tuple of (state_name, error_message) or None if no error state found
    """
    for state in reversed(recorder.states):
        if state.get("status") == "error":
            return (str(state.get("name", "unknown")), str(state.get("error", "")))
    return None


@dataclass(frozen=True)
class AbortInfo:
    """Structured abort metadata returned by _detect_abort_status."""

    state_name: str
    error_name: str | None
    error_cause: str | None


def _detect_abort_status(
    recorder: RunRecorder,
) -> tuple[str, AbortInfo | None]:
    """Detect if the workflow ended at an abort state.

    Args:
        recorder: The RunRecorder instance

    Returns:
        Tuple of (status, AbortInfo | None):
        - If last state type=="fail": ("aborted", AbortInfo(...))
        - If last state name starts with "abort_": ("aborted", AbortInfo(...))
        - Otherwise: ("completed", None)
    """
    if recorder.states:
        last = recorder.states[-1]
        name = last.get("name", "")
        if last.get("type") == "fail":
            return (
                "aborted",
                AbortInfo(
                    state_name=str(name),
                    error_name=last.get("error_name"),
                    error_cause=last.get("error_cause"),
                ),
            )
        if isinstance(name, str) and name.startswith("abort_"):
            return (
                "aborted",
                AbortInfo(state_name=name, error_name=None, error_cause=None),
            )
    return ("completed", None)


@dataclass(frozen=True)
class FlowResult:
    """Structured return value for run_flow and resume_flow."""

    results: dict[str, Any]
    status: str  # "completed" | "aborted"
    abort_state: str | None = None

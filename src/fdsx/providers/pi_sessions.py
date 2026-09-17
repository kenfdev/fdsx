"""Pi v3 native session references. Never persist or reconstruct conversations."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import structlog

from fdsx.providers.base import ProviderSessionError

log = structlog.get_logger(__name__)


def session_error(state: str, detail: str) -> ProviderSessionError:
    log.error("pi_session_unavailable", state=state, reason=detail)
    return ProviderSessionError(f"State '{state}': Pi session {detail}")


def new_session_directory(state: str) -> Path:
    """Leave retention and conversation storage in Pi's agent directory."""
    root = Path(
        os.environ.get("PI_CODING_AGENT_DIR", str(Path.home() / ".pi/agent"))
    ).expanduser()
    directory = root / "sessions" / ("fdsx-" + uuid4().hex)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except OSError:
        raise session_error(
            state, "storage is unavailable; check Pi's session directory permissions"
        ) from None
    return directory.resolve()


def capture_reference(
    path: Path, state: str, *, size: int | None = None
) -> dict[str, str]:
    """Validate a native tree and bind the entire completed file to its digest.

    Pin the native endpoint and the bytes through completion. Later appends are
    allowed; modifications to completed history fail closed.
    """
    try:
        raw = path.read_bytes()
        if size is not None:
            if len(raw) < size:
                raise ValueError
            raw = raw[:size]
        entries = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if not entries or any(not isinstance(entry, dict) for entry in entries):
            raise ValueError
        header = entries[0]
        if header.get("type") != "session" or header.get("version") != 3:
            raise ValueError
        session_id = header.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError
        seen: set[str] = set()
        by_id = {}
        leaf = ""
        assistant_seen = False
        for entry in entries[1:]:
            entry_id = entry.get("id")
            parent = entry.get("parentId")
            if not isinstance(entry_id, str) or not entry_id or entry_id in seen:
                raise ValueError
            if parent is not None and (
                not isinstance(parent, str) or parent not in seen
            ):
                raise ValueError
            seen.add(entry_id)
            by_id[entry_id] = entry
            leaf = entry_id
            message = entry.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                assistant_seen = True
        if not leaf or not assistant_seen:
            raise ValueError
        # Native createBranchedSession regenerates label entries. Pin the
        # conversation endpoint before any trailing labels instead of a label
        # ID that Pi intentionally replaces in its child.
        while by_id[leaf].get("type") == "label":
            leaf = by_id[leaf].get("parentId")
            if not isinstance(leaf, str) or leaf not in by_id:
                raise ValueError
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise session_error(
            state,
            "data is missing, corrupt, or incompatible; restore the original Pi history or rerun its source",
        ) from None
    return {
        "provider": "pi",
        "path": str(path.resolve()),
        "id": session_id,
        "endpoint": leaf,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": str(len(raw)),
    }


def select_source(reference: dict[str, str], state: str) -> Path:
    if (
        not isinstance(reference, dict)
        or set(reference) != {"provider", "path", "id", "endpoint", "sha256", "size"}
        or any(not isinstance(value, str) or not value for value in reference.values())
        or reference.get("provider") != "pi"
        or not Path(reference["path"]).is_absolute()
    ):
        raise session_error(
            state,
            "reference metadata is missing or incompatible; rerun the source task",
        )
    path = Path(reference["path"])
    # Reject corrupt appended data too; native loaders may otherwise repair a
    # torn tail and silently continue with a different saved history.
    capture_reference(path, state)
    try:
        size = int(reference["size"])
        if size <= 0:
            raise ValueError
    except ValueError:
        raise session_error(
            state, "reference size is incompatible; rerun the source task"
        ) from None
    if capture_reference(path, state, size=size) != reference:
        raise session_error(
            state,
            "history changed after source completion; restore the original history or rerun the source task",
        )
    return path


def capture_directory(directory: Path, state: str) -> dict[str, str]:
    try:
        paths = list(directory.glob("*.jsonl"))
    except OSError:
        raise session_error(state, "storage cannot be read") from None
    if len(paths) != 1:
        raise session_error(
            state,
            "capture expected one saved native session; check Pi version and persistence settings",
        )
    return capture_reference(paths[0], state)

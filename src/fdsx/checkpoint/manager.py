import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver


def _extract_meta_from_checkpoint(checkpoint_data: dict) -> dict:
    """Extract _meta from checkpoint channel_values, handling both
    __root__ (object schema) and named-channel (TypedDict schema) layouts."""
    channel_values = checkpoint_data.get("channel_values", {})
    # Named-channel layout (flows with ParallelState or TypedDict schema)
    meta = channel_values.get("_meta")
    if isinstance(meta, dict):
        return meta
    # __root__ layout (flows using object schema, e.g. no ParallelState)
    root = channel_values.get("__root__")
    if isinstance(root, dict):
        meta = root.get("_meta")
        if isinstance(meta, dict):
            return meta
    return {}


_SAFE_THREAD_ID = re.compile(r"^[a-zA-Z0-9_\-]+$")


class CheckpointManager:
    """Manages checkpoints and PID-based locks for flow execution.

    Wraps LangGraph's SqliteSaver to provide:
    - Checkpoint persistence to SQLite
    - PID-based lock files to prevent concurrent execution
    - Stale lock detection and cleanup
    - Thread listing functionality
    """

    DEFAULT_BASE_DIR = Path(".fdsx")

    def __init__(self, base_dir: Path | None = None):
        """Initialize the CheckpointManager.

        Args:
            base_dir: Base directory for checkpoints and locks.
                     Defaults to '.fdsx/' relative to CWD.
        """
        self.base_dir = base_dir if base_dir is not None else self.DEFAULT_BASE_DIR
        self.checkpoints_dir = self.base_dir / "checkpoints"
        self.locks_dir = self.base_dir / "locks"

        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.locks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def get_checkpointer(self) -> SqliteSaver:
        """Get a SqliteSaver checkpointer for the checkpoint directory.

        Returns:
            SqliteSaver configured to use the checkpoints database
        """
        db_path = self.checkpoints_dir / "checkpoints.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        try:
            os.chmod(str(db_path), 0o600)
        except OSError:
            pass
        return SqliteSaver(conn)

    def _get_lock_path(self, thread_id: str) -> Path:
        """Get the path to the lock file for a thread.

        Raises:
            ValueError: If thread_id contains unsafe characters or escapes locks_dir.
        """
        if not _SAFE_THREAD_ID.match(thread_id):
            raise ValueError(f"Invalid thread ID: {thread_id!r}")
        lock_path = (self.locks_dir / f"{thread_id}.lock").resolve()
        if not str(lock_path).startswith(str(self.locks_dir.resolve())):
            raise ValueError(f"Thread ID escapes lock directory: {thread_id!r}")
        return lock_path

    def acquire_lock(self, thread_id: str) -> bool:
        """Acquire a lock for the given thread ID.

        Args:
            thread_id: The thread ID to lock

        Returns:
            True if lock was acquired, False if already locked by alive process
        """
        lock_path = self._get_lock_path(thread_id)

        if lock_path.exists():
            try:
                with open(lock_path) as f:
                    pid = int(f.read().strip())
                try:
                    os.kill(pid, 0)
                    return False
                except OSError:
                    pass
            except (ValueError, IOError):
                pass

            lock_path.unlink(missing_ok=True)

        fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))

        return True

    def release_lock(self, thread_id: str) -> None:
        """Release the lock for the given thread ID.

        Args:
            thread_id: The thread ID to unlock
        """
        lock_path = self._get_lock_path(thread_id)
        lock_path.unlink(missing_ok=True)

    def is_locked(self, thread_id: str) -> tuple[bool, int | None]:
        """Check if a thread is locked.

        Args:
            thread_id: The thread ID to check

        Returns:
            Tuple of (is_locked, pid) where pid is the locking PID if locked
        """
        lock_path = self._get_lock_path(thread_id)

        if not lock_path.exists():
            return False, None

        try:
            with open(lock_path) as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                return True, pid
            except OSError:
                return False, None
        except (ValueError, IOError):
            return False, None

    def verify_checkpoint(self, thread_id: str) -> bool:
        """Verify checkpoint integrity for a thread ID.

        Args:
            thread_id: The thread ID to verify

        Returns:
            True if checkpoint is valid, False otherwise
        """
        db_path = self.checkpoints_dir / "checkpoints.db"
        if not db_path.exists():
            return False

        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?",
                (thread_id,),
            )
            count = cursor.fetchone()[0]
            conn.close()
            return count > 0
        except Exception:
            return False

    def list_threads(self) -> list[dict[str, Any]]:
        """List all known thread executions.

        Returns:
            List of thread info dictionaries with thread_id, status, flow_name
        """
        db_path = self.checkpoints_dir / "checkpoints.db"
        if not db_path.exists():
            return []

        try:
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT thread_id FROM checkpoints")
            thread_rows = cursor.fetchall()
            conn.close()

            threads = []
            checkpointer = self.get_checkpointer()
            for (thread_id,) in thread_rows:
                is_locked, pid = self.is_locked(thread_id)
                status = "running" if is_locked else "stopped"
                flow_name = thread_id  # fallback default

                current_state = ""
                started_at = ""
                config = {"configurable": {"thread_id": thread_id}}
                try:
                    checkpoint_tuple = checkpointer.get_tuple(config)
                    if checkpoint_tuple is not None:
                        checkpoint_data = checkpoint_tuple.checkpoint
                        meta = _extract_meta_from_checkpoint(checkpoint_data)
                        flow_name = meta.get("flow_name", thread_id)
                        if not is_locked:
                            if checkpoint_tuple.pending_writes:
                                has_error = any(
                                    pw[1] == "__error__"
                                    for pw in checkpoint_tuple.pending_writes
                                    if isinstance(pw, (list, tuple)) and len(pw) >= 2
                                )
                                status = "stopped" if has_error else "waiting"
                            else:
                                status = "completed"
                        # Extract current_state from checkpoint.
                        # For stopped/waiting flows, prefer _meta.next_state (the node
                        # about to execute when the crash/interrupt happened).
                        # For completed/running flows, use last entry in versions_seen.
                        if status in ("stopped", "waiting"):
                            next_state_val = meta.get("next_state", "")
                            if next_state_val and next_state_val != "__end__":
                                current_state = next_state_val
                            else:
                                versions_seen = checkpoint_data.get("versions_seen", {})
                                if isinstance(versions_seen, dict) and versions_seen:
                                    current_state = list(versions_seen.keys())[-1]
                        else:
                            versions_seen = checkpoint_data.get("versions_seen", {})
                            if isinstance(versions_seen, dict) and versions_seen:
                                current_state = list(versions_seen.keys())[-1]
                        # Extract started_at from checkpoint ts
                        ts = checkpoint_data.get("ts", "")
                        if ts and "T" in str(ts):
                            started_at = str(ts)[:16].replace("T", " ")
                except Exception:
                    pass

                threads.append(
                    {
                        "thread_id": thread_id,
                        "status": status,
                        "flow_name": flow_name,
                        "current_state": current_state,
                        "started_at": started_at,
                    }
                )
            return threads
        except Exception:
            return []

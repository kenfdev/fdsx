import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import Checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver

logger = logging.getLogger(__name__)


def _extract_meta_from_checkpoint(
    checkpoint_data: Checkpoint | dict[str, Any],
) -> dict[str, Any]:
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

    def _create_lock_file(self, lock_path: Path) -> bool:
        """Atomically create a lock file and write the current PID.

        Returns:
            True if the file was created, False if it already exists.
        """
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            return False

    def acquire_lock(self, thread_id: str) -> bool:
        """Acquire a lock for the given thread ID.

        Uses O_CREAT|O_EXCL for atomic creation to prevent TOCTOU race conditions.
        Automatically recovers stale locks from dead processes.

        Args:
            thread_id: The thread ID to lock

        Returns:
            True if lock was acquired, False if already locked by alive process
        """
        lock_path = self._get_lock_path(thread_id)

        if self._create_lock_file(lock_path):
            return True

        # Lock file already exists — check if the owning process is still alive
        try:
            with open(lock_path) as f:
                pid = int(f.read().strip())
            try:
                os.kill(pid, 0)
                # Process is alive — lock is legitimately held
                return False
            except OSError:
                # Process is dead — stale lock
                logger.warning(
                    "Removing stale lock for thread %r (dead PID %d)", thread_id, pid
                )
        except (OSError, ValueError):
            # Corrupt or empty lock file — treat as stale
            logger.warning("Removing corrupt lock file for thread %r", thread_id)

        # Remove the stale/corrupt lock and retry once
        lock_path.unlink(missing_ok=True)
        return self._create_lock_file(lock_path)

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
        except (OSError, ValueError):
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
            return bool(count > 0)
        except Exception:
            return False

    def list_threads(self) -> list[dict[str, Any]]:
        """List all known thread executions.

        Merges threads from the checkpoint database and from run log directories
        under <base_dir>/runs/.

        Returns:
            List of thread info dictionaries with thread_id, status, flow_name
        """
        from fdsx.logging.recorder import RUN_FILENAME, RUNS_DIR_NAME

        # Collect thread IDs from checkpoint DB
        checkpoint_thread_ids: list[str] = []
        db_path = self.checkpoints_dir / "checkpoints.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path), check_same_thread=False)
                cursor = conn.cursor()
                cursor.execute("SELECT DISTINCT thread_id FROM checkpoints")
                checkpoint_thread_ids = [row[0] for row in cursor.fetchall()]
                conn.close()
            except Exception:
                pass

        # Collect thread IDs from run log directories
        runs_dir = self.base_dir / RUNS_DIR_NAME
        run_log_thread_ids: list[str] = []
        if runs_dir.is_dir():
            for entry in runs_dir.iterdir():
                if entry.is_dir() and (entry / RUN_FILENAME).is_file():
                    run_log_thread_ids.append(entry.name)

        # Merge, preserving checkpoint-DB entries first, then run-log-only entries
        seen: set[str] = set(checkpoint_thread_ids)
        all_thread_ids = list(checkpoint_thread_ids)
        for tid in run_log_thread_ids:
            if tid not in seen:
                seen.add(tid)
                all_thread_ids.append(tid)

        if not all_thread_ids:
            return []

        try:
            threads = []
            checkpointer = self.get_checkpointer() if db_path.exists() else None
            for thread_id in all_thread_ids:
                is_locked, _pid = self.is_locked(thread_id)
                status = "running" if is_locked else "stopped"
                flow_name = thread_id  # fallback default

                current_state = ""
                started_at = ""
                config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
                try:
                    checkpoint_tuple = (
                        checkpointer.get_tuple(config)
                        if checkpointer is not None
                        else None
                    )
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

                # Fallback: read flow_name and started_at from run log when
                # the checkpoint did not provide them.
                if flow_name == thread_id or not started_at:
                    try:
                        import json

                        run_log_path = runs_dir / thread_id / RUN_FILENAME
                        if run_log_path.is_file():
                            with open(run_log_path) as f:
                                run_log = json.load(f)
                            if flow_name == thread_id:
                                flow_name = run_log.get("flow_name", thread_id)
                            if not started_at:
                                ts_str = run_log.get("started_at", "")
                                if ts_str and "T" in ts_str:
                                    started_at = ts_str[:16].replace("T", " ")
                            if not is_locked and flow_name != thread_id:
                                # Run-log-only thread: derive status from log status
                                log_status = run_log.get("status", "")
                                if log_status == "completed":
                                    status = "completed"
                    except (json.JSONDecodeError, OSError, KeyError):
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

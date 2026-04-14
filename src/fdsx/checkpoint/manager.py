import contextlib
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver

logger = logging.getLogger(__name__)


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
        with contextlib.suppress(OSError):
            db_path.chmod(0o600)
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
            with lock_path.open() as f:
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
            with lock_path.open() as f:
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
        import json

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
            # Cache compiled graphs by flow_path_str to avoid re-compiling
            _compiled_cache: dict[str, Any] = {}

            # Load project config once so profile-based flows can be reloaded
            from fdsx.core.compiler import compile_flow
            from fdsx.core.config import load_config as _load_config
            from fdsx.core.loader import load_flow

            _config_profiles: dict[str, Any] | None = None
            try:
                _fdsx_config = _load_config(project_dir=self.base_dir.parent)
                if _fdsx_config.profiles:
                    _config_profiles = {
                        name: prof.model_dump()
                        for name, prof in _fdsx_config.profiles.items()
                    }
            except Exception:
                pass  # malformed config: profile resolution unavailable, continue without profiles

            for thread_id in all_thread_ids:
                is_locked, _pid = self.is_locked(thread_id)
                status = "running" if is_locked else "stopped"
                flow_name = thread_id  # fallback default
                flow_path_str: str | None = None
                current_state = ""
                started_at = ""

                # Read flow_path and metadata from run.json (public sidecar, not checkpoint internals)
                run_log_path = runs_dir / thread_id / RUN_FILENAME
                if run_log_path.is_file():
                    try:
                        with run_log_path.open() as f:
                            _run_log_boot = json.load(f)
                        flow_path_str = _run_log_boot.get("flow_path")
                        flow_name = _run_log_boot.get("flow_name", thread_id)
                    except (json.JSONDecodeError, OSError, KeyError):
                        pass

                config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
                try:
                    checkpoint_tuple = (
                        checkpointer.get_tuple(config)
                        if checkpointer is not None
                        else None
                    )
                    if checkpoint_tuple is not None and not is_locked and flow_path_str:
                        try:
                            if flow_path_str not in _compiled_cache:
                                flow_path = Path(flow_path_str)
                                if flow_path.exists():
                                    loaded_flow, _errors = load_flow(
                                        flow_path, config_profiles=_config_profiles
                                    )
                                    if loaded_flow is not None:
                                        compiled = compile_flow(
                                            loaded_flow, checkpointer=checkpointer
                                        )
                                        _compiled_cache[flow_path_str] = compiled

                            if flow_path_str in _compiled_cache:
                                compiled = _compiled_cache[flow_path_str]
                                state_snapshot = compiled.graph.get_state(config)

                                # Prefer flow_name from snapshot values if available
                                if state_snapshot.values:
                                    snap_meta = state_snapshot.values.get("_meta", {})
                                    if isinstance(snap_meta, dict):
                                        flow_name = snap_meta.get(
                                            "flow_name", flow_name
                                        )

                                # Status detection via public StateSnapshot fields
                                if state_snapshot.interrupts:
                                    status = "waiting"
                                elif any(
                                    getattr(task, "error", None)
                                    for task in state_snapshot.tasks
                                ):
                                    status = "stopped"
                                elif state_snapshot.next == ():
                                    status = "completed"
                                else:
                                    status = "stopped"

                                # current_state: next node if pending, else run log
                                if state_snapshot.next:
                                    current_state = state_snapshot.next[0]

                                # started_at from snapshot timestamp
                                if state_snapshot.created_at:
                                    ts = str(state_snapshot.created_at)
                                    if "T" in ts:
                                        started_at = ts[:16].replace("T", " ")
                        except Exception:
                            pass
                except Exception:
                    pass

                # Fallback: read started_at, current_state, and status from run log
                # when the snapshot did not provide them.
                if not started_at or not current_state:
                    try:
                        if run_log_path.is_file():
                            with run_log_path.open() as f:
                                run_log = json.load(f)
                            if not started_at:
                                ts_str = run_log.get("started_at", "")
                                if ts_str and "T" in ts_str:
                                    started_at = ts_str[:16].replace("T", " ")
                            if not is_locked and flow_name != thread_id:
                                log_status = run_log.get("status", "")
                                if log_status == "completed":
                                    status = "completed"
                            if not current_state:
                                states = run_log.get("states", [])
                                if states:
                                    current_state = states[-1].get("name", "")
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

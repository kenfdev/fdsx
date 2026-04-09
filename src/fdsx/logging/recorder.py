import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUTPUT_PREVIEW_MAX_LENGTH = 500

THREAD_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Directory layout constants
FDSX_DIR_NAME = ".fdsx"
RUNS_DIR_NAME = "runs"
LOGS_DIR_NAME = "logs"
RUN_FILENAME = "run.json"


class RunRecorder:
    """Records per-state input/output/duration to a JSON run log."""

    def __init__(
        self,
        thread_id: str,
        flow_name: str,
        flow_version: str | None = None,
    ):
        if not THREAD_ID_PATTERN.match(thread_id):
            raise ValueError(
                f"Invalid thread_id '{thread_id}': must contain only alphanumeric characters, hyphens, and underscores"
            )
        self.thread_id = thread_id
        self.flow_name = flow_name
        self.flow_version = flow_version
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.status = "running"
        self.states: list[dict[str, Any]] = []
        self.completed_at: str | None = None
        self.final_variables: dict[str, Any] | None = None
        self._current_state: dict[str, Any] | None = None

    def record_state_start(self, state_name: str, state_type: str) -> None:
        """Append new state entry with name, type, started_at."""
        self._current_state = {
            "name": state_name,
            "type": state_type,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self.states.append(self._current_state)

    def record_state_complete(
        self,
        state_name: str,
        status: str,
        output: str,
        variables_set: list[str],
        branches: list[dict[str, Any]] | None = None,
        state_type: str | None = None,
    ) -> None:
        """Update the state entry with completed_at, duration_seconds, status, output_preview, variables_set, branches."""
        state = self._find_state_by_name(state_name)
        if state is None:
            state = {
                "name": state_name,
                "type": state_type or "unknown",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            self.states.append(state)

        completed_at = datetime.now(timezone.utc).isoformat()
        started_at = state.get("started_at", completed_at)

        try:
            start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            duration_seconds = int((end_dt - start_dt).total_seconds())
        except (ValueError, TypeError):
            duration_seconds = 0

        output_preview = output[:OUTPUT_PREVIEW_MAX_LENGTH] if output else ""

        state["completed_at"] = completed_at
        state["duration_seconds"] = duration_seconds
        state["status"] = status
        state["output_preview"] = output_preview
        state["variables_set"] = variables_set

        if branches is not None:
            state["branches"] = branches

        self._current_state = None

    def record_state_error(
        self, state_name: str, error: str, state_type: str | None = None
    ) -> None:
        """Update the state entry with status="error" and error message."""
        state = self._find_state_by_name(state_name)
        if state is None:
            state = {
                "name": state_name,
                "type": state_type or "unknown",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            self.states.append(state)

        completed_at = datetime.now(timezone.utc).isoformat()
        started_at = state.get("started_at", completed_at)

        try:
            start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            duration_seconds = int((end_dt - start_dt).total_seconds())
        except (ValueError, TypeError):
            duration_seconds = 0

        state["status"] = "error"
        state["error"] = error
        state["completed_at"] = completed_at
        state["duration_seconds"] = duration_seconds
        state["output_preview"] = ""
        state["variables_set"] = []

        self._current_state = None

    def record_map_start(self, state_name: str, item_count: int) -> None:
        """Record map state start with item count metadata.

        Args:
            state_name: Name of the map state
            item_count: Number of items to iterate over
        """
        self._current_state = {
            "name": state_name,
            "type": "map",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "item_count": item_count,
            "iterations": [],
        }
        self.states.append(self._current_state)

    def record_map_iteration_complete(
        self,
        state_name: str,
        index: int,
        status: str,
        output: str,
    ) -> None:
        """Record a single map iteration result.

        Args:
            state_name: Name of the parent map state
            index: Index of the iteration (0-based)
            status: Status of the iteration ("success" or "error")
            output: Output from the iteration
        """
        state = self._find_state_by_name(state_name)
        if state is None:
            return

        if "iterations" not in state:
            state["iterations"] = []

        output_preview = output[:OUTPUT_PREVIEW_MAX_LENGTH] if output else ""

        state["iterations"].append(
            {
                "index": index,
                "status": status,
                "output_preview": output_preview,
            }
        )

    def record_map_complete(
        self,
        state_name: str,
        status: str,
        results_count: int,
        failed_count: int,
    ) -> None:
        """Finalize a map state entry with results summary.

        Args:
            state_name: Name of the map state
            status: Overall status ("success" or "error")
            results_count: Number of successful results
            failed_count: Number of failed iterations
        """
        state = self._find_state_by_name(state_name)
        if state is None:
            state = {
                "name": state_name,
                "type": "map",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
            self.states.append(state)

        completed_at = datetime.now(timezone.utc).isoformat()
        started_at = state.get("started_at", completed_at)

        try:
            start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            duration_seconds = int((end_dt - start_dt).total_seconds())
        except (ValueError, TypeError):
            duration_seconds = 0

        state["completed_at"] = completed_at
        state["duration_seconds"] = duration_seconds
        state["status"] = status
        state["results_count"] = results_count
        state["failed_count"] = failed_count

        self._current_state = None

    def finalize(
        self, final_variables: dict[str, Any], status: str = "completed"
    ) -> None:
        """Set completed_at, status, final_variables on the log."""
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.status = status
        self.final_variables = final_variables

    def save(self, base_dir: Path | None = None) -> Path:
        """Write JSON to <base_dir>/runs/<thread_id>/run.json.

        When base_dir is None, defaults to <CWD>/.fdsx/runs/<thread_id>/run.json.
        When base_dir is provided, writes to <base_dir>/runs/<thread_id>/run.json.
        """
        if base_dir is not None:
            runs_dir = (base_dir / RUNS_DIR_NAME).resolve()
        else:
            runs_dir = (Path.cwd() / FDSX_DIR_NAME / RUNS_DIR_NAME).resolve()

        runs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        runs_dir.chmod(0o700)

        thread_dir = (runs_dir / self.thread_id).resolve()

        if not str(thread_dir).startswith(str(runs_dir)):
            raise ValueError("Invalid thread_id: path resolved outside runs directory")

        thread_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        thread_dir.chmod(0o700)

        file_path = thread_dir / RUN_FILENAME

        if file_path.exists():
            with file_path.open() as f:
                existing_log: dict[str, Any] = json.load(f)

            existing_states = existing_log.get("states", [])
            existing_states.extend(self.states)

            self.states = existing_states
            self.started_at = existing_log.get("started_at", self.started_at)

        log_data = self.to_dict()
        log_json = json.dumps(log_data, indent=2)

        fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, log_json.encode("utf-8"))
        finally:
            os.close(fd)

        return file_path

    def to_dict(self) -> dict[str, Any]:
        """Return the full log as a dict."""
        result: dict[str, Any] = {
            "thread_id": self.thread_id,
            "flow_name": self.flow_name,
            "flow_version": self.flow_version,
            "started_at": self.started_at,
            "status": self.status,
            "states": self.states,
        }

        if self.completed_at is not None:
            result["completed_at"] = self.completed_at

        if self.final_variables is not None:
            result["final_variables"] = self.final_variables

        return result

    def _find_state_by_name(self, state_name: str) -> dict[str, Any] | None:
        """Find a state by name, searching from the end (most recent first)."""
        for state in reversed(self.states):
            if state.get("name") == state_name:
                return state
        return None

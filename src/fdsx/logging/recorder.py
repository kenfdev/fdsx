import json
import os
import re
import shutil
import threading
import uuid
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


class InputRevisionError(RuntimeError):
    """Full revision history could not be preserved."""


class RunRecorder:
    """Records per-state input/output/duration to a JSON run log."""

    def __init__(
        self,
        thread_id: str,
        flow_name: str,
        flow_version: str | None = None,
        flow_path: str | None = None,
    ):
        if not THREAD_ID_PATTERN.match(thread_id):
            raise ValueError(
                f"Invalid thread_id '{thread_id}': must contain only alphanumeric characters, hyphens, and underscores"
            )
        self.thread_id = thread_id
        self.flow_name = flow_name
        self.flow_version = flow_version
        self.flow_path = flow_path
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.status = "running"
        self.states: list[dict[str, Any]] = []
        self.classifier_events: list[dict[str, Any]] = []
        self.local_workflows: list[dict[str, Any]] = []
        self.recoveries: list[dict[str, str]] = []
        self.completed_at: str | None = None
        self.final_variables: dict[str, Any] | None = None
        self._current_state: dict[str, Any] | None = None
        self._lock: threading.Lock = threading.Lock()

    def record_local_workflow(self, scope: str, recorder: "RunRecorder") -> None:
        """Keep local diagnostics separate from top-level terminal-state detection."""
        with self._lock:
            self.local_workflows.append(
                {
                    "scope": scope,
                    "states": recorder.states,
                    "classifier_events": recorder.classifier_events,
                }
            )

    def record_state_start(self, state_name: str, state_type: str) -> None:
        """Append new state entry with name, type, started_at."""
        self._current_state = {
            "name": state_name,
            "type": state_type,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self.states.append(self._current_state)

    def record_classifier_event(
        self, state_name: str, attempt: str, event: str, data: dict[str, Any]
    ) -> None:
        with self._lock:
            self.classifier_events.append(
                {
                    "name": state_name,
                    "type": "classifier_event",
                    "attempt": attempt,
                    "event": event,
                    "diagnostic": data,
                }
            )

    def record_evaluation_diagnostics(self, state_name: str, result: Any) -> None:
        """Record validated metrics without materials, rubric text or raw answers."""

        def label(value: str) -> str:
            return "".join(char if char.isprintable() else " " for char in value)[:128]

        state = self._find_state_by_name(state_name)
        if state is None:
            return
        state["evaluation"] = {
            "provider": "jev",
            "source": "service",
            "requested_model": label(result.requested_model),
            "reported_model": label(result.reported_model),
            "usage": {
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            "questions": [
                {
                    "name": label(name),
                    "type": answer["type"],
                    **(
                        {"confidence": answer["confidence"]}
                        if "confidence" in answer
                        else {}
                    ),
                    **(
                        {
                            "probabilities": [
                                {
                                    "candidate": label(str(candidate)),
                                    "probability": probability,
                                }
                                for candidate, probability in answer[
                                    "probabilities"
                                ].items()
                            ]
                        }
                        if "probabilities" in answer
                        else {}
                    ),
                }
                for name, answer in result.answers.items()
            ],
        }

    def record_state_escalation(
        self, state_name: str, target_provider: str, target_model: str
    ) -> None:
        """Record that escalation fired for a state (idempotent)."""
        state = self._find_state_by_name(state_name)
        if state is None:
            return
        state["escalation_activated"] = True
        state["escalation_provider"] = target_provider
        state["escalation_model"] = target_model

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
        self,
        state_name: str,
        error: str,
        state_type: str | None = None,
        *,
        error_name: str | None = None,
        error_cause: str | None = None,
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
        if error_name is not None:
            state["error_name"] = error_name
        if error_cause is not None:
            state["error_cause"] = error_cause
        state["completed_at"] = completed_at
        state["duration_seconds"] = duration_seconds
        state["output_preview"] = ""
        state["variables_set"] = []

        self._current_state = None

    def record_fallback_invocation(
        self,
        state_name: str,
        source: str,
        outcome: str,
        pattern: str,
        value_preview: str | None = None,
        error_kind: str | None = None,
        branch_index: int | None = None,
        iter_index: int | None = None,
    ) -> None:
        """Append one fallback invocation record to the state's fallback_invocations list."""
        record: dict[str, Any] = {
            "source": source,
            "outcome": outcome,
            "state_name": state_name,
            "pattern": pattern,
        }
        if value_preview is not None:
            record["value_preview"] = value_preview[:200]
        if error_kind is not None:
            record["error_kind"] = error_kind
        if branch_index is not None:
            record["branch_index"] = branch_index
        if iter_index is not None:
            record["iter_index"] = iter_index

        with self._lock:
            state = self._find_state_by_name(state_name)
            if state is None:
                state = {
                    "name": state_name,
                    "type": "unknown",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
                self.states.append(state)
            state.setdefault("fallback_invocations", []).append(record)

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

    def preserve_recovery_snapshot(
        self,
        run_dir: Path,
        saved_values: dict[str, Any],
        run_log: dict[str, Any],
        effective_inputs: dict[str, Any],
        from_state: str,
    ) -> str:
        """Publish full pre-update history before the recovery checkpoint changes.

        The checkpoint references only a completely published snapshot. An
        interrupted publication cannot overwrite an earlier revision.
        """
        import structlog

        revision = uuid.uuid4().hex
        revisions = run_dir / "revisions"
        staging = revisions / f".{revision}.pending"
        destination = revisions / revision
        try:
            staging.mkdir(parents=True, mode=0o700)
            snapshot = {
                "thread_id": self.thread_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "from_state": from_state,
                "saved_values": saved_values,
                "run_log": run_log,
                "effective_inputs": effective_inputs,
            }
            (staging / "snapshot.json").write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # Managed result_file outputs live under data/, including nested runs.
            for data_dir in run_dir.rglob("data"):
                relative = data_dir.relative_to(run_dir)
                if "revisions" in relative.parts or not data_dir.is_dir():
                    continue
                shutil.copytree(data_dir, staging / "files" / relative)
            staging.rename(destination)
        except (OSError, TypeError, ValueError) as error:
            structlog.get_logger(__name__).error("input_revision_preservation_failed")
            raise InputRevisionError("Could not preserve input revision") from error
        return str(destination.relative_to(run_dir))

    def record_recovery(self, from_state: str) -> None:
        """Record the start of an explicit recovery jump."""
        self.recoveries.append(
            {
                "from_state": from_state,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }
        )

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
            with file_path.open(encoding="utf-8") as f:
                existing_log: dict[str, Any] = json.load(f)

            self.local_workflows = (
                existing_log.get("local_workflows", []) + self.local_workflows
            )
            self.classifier_events = (
                existing_log.get("classifier_events", []) + self.classifier_events
            )
            existing_states = existing_log.get("states", [])
            existing_states.extend(self.states)

            self.states = existing_states
            existing_recoveries = existing_log.get("recoveries", [])
            existing_recoveries.extend(self.recoveries)
            self.recoveries = existing_recoveries
            self.started_at = existing_log.get("started_at", self.started_at)

        log_data = self.to_dict()
        log_json = json.dumps(log_data, ensure_ascii=False, indent=2)

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
            "flow_path": self.flow_path,
            "started_at": self.started_at,
            "status": self.status,
            "states": self.states,
        }

        if self.local_workflows:
            result["local_workflows"] = self.local_workflows
        if self.classifier_events:
            result["classifier_events"] = self.classifier_events
        if self.completed_at is not None:
            result["completed_at"] = self.completed_at

        if self.final_variables is not None:
            result["final_variables"] = self.final_variables
        if self.recoveries:
            result["recoveries"] = self.recoveries

        return result

    def _find_state_by_name(self, state_name: str) -> dict[str, Any] | None:
        """Find a state by name, searching from the end (most recent first)."""
        for state in reversed(self.states):
            if state.get("name") == state_name:
                return state
        return None

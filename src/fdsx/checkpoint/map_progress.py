"""Item-granular continuation state, published only after an atomic save."""

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class MapProgressError(RuntimeError):
    """A map continuation could not be read or published."""


class MapProgress:
    """One owner per map visit; completion callbacks share this owner and lock.

    Missing keys are unfinished, even when another item's result is null.
    Legacy documents are converted in memory, never rewritten by reading.
    """

    def __init__(
        self,
        run_dir: str,
        name: str,
        visit: int,
        item_count: int,
        *,
        resume: bool = False,
        local: bool = False,
    ) -> None:
        self.visit = visit
        self.name = name
        self._lock = Lock()
        self._items: dict[int, dict[str, Any]] = {}
        self.path: Path | None = None
        if not run_dir:
            return
        base = Path(run_dir).resolve()
        path = (base / name / "progress.json").resolve()
        if not path.is_relative_to(base):
            log.error("map_progress_invalid_path", state=name)
            raise MapProgressError("Map progress path escapes run directory")
        self.path = path
        if not resume:
            # A legacy file has no visit marker. Remove it on a fresh entry even
            # if that entry fails before its first save; a later resume must not
            # adopt results from the previous visit.
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                log.error("map_progress_reset_failed", state=name, error=str(error))
                raise MapProgressError("Could not reset map progress") from error
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            log.error(
                "map_progress_read_failed", state=name, error_kind=type(error).__name__
            )
            raise MapProgressError("Could not read map progress") from error
        try:
            if not isinstance(data, dict):
                raise ValueError("Expected progress object")
            if data.get("state_iteration", visit) != visit:
                return
            if "version" in data:
                if data["version"] != 1:
                    raise ValueError("Unsupported map progress version")
                for key, entry in data["items"].items():
                    index = int(key)
                    if index < 0 or index >= item_count:
                        raise ValueError("Item index outside input")
                    if entry["status"] not in {"success", "failure", "unknown"}:
                        raise ValueError("Invalid item status")
                    self._items[index] = {
                        "result": entry["result"],
                        "status": entry["status"],
                    }
            else:
                results = data["results"]
                count = data["completed_iterations"]
                if not isinstance(results, list) or count != len(results):
                    raise ValueError("Invalid legacy progress")
                if count > item_count:
                    return
                for index, result in enumerate(results):
                    status = "unknown" if result is None else "success"
                    if local and isinstance(result, dict) and "exit_code" in result:
                        status = "success" if result["exit_code"] == 0 else "failure"
                    self._items[index] = {"result": result, "status": status}
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            log.error(
                "map_progress_invalid", state=name, error_kind=type(error).__name__
            )
            raise MapProgressError("Invalid map progress") from error

    def snapshot(self) -> dict[int, dict[str, Any]]:
        with self._lock:
            return deepcopy(self._items)

    def collect(self, index: int, result: Any, status: str) -> None:
        """Merge and publish under one lock; failed saves leave memory unchanged."""
        with self._lock:
            candidate = {
                **self._items,
                index: {"result": deepcopy(result), "status": status},
            }
            temporary: Path | None = None
            try:
                if self.path is not None:
                    self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    fd, filename = tempfile.mkstemp(
                        prefix=".progress-", dir=self.path.parent
                    )
                    temporary = Path(filename)
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        json.dump(
                            {
                                "version": 1,
                                "state_iteration": self.visit,
                                "items": candidate,
                            },
                            stream,
                            ensure_ascii=False,
                        )
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(self.path)
                self._items = candidate
            except (OSError, TypeError, ValueError) as error:
                log.error(
                    "map_progress_save_failed",
                    error=str(error),
                    state=self.name,
                    item_index=index,
                    error_kind=type(error).__name__,
                )
                raise MapProgressError("Could not save map item progress") from error
            finally:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        log.warning(
                            "map_progress_temporary_cleanup_failed", state=self.name
                        )

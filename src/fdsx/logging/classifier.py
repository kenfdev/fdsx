"""Classifier audit events and opt-in, bounded private request records."""

import json
import os
import uuid
from pathlib import Path
from typing import Any

import structlog

from fdsx.display.terminal import display_classifier_event

log = structlog.get_logger(__name__)
FULL_INPUT_LIMIT = 1024 * 1024


class ClassifierRecordingError(RuntimeError):
    """A private classifier request could not be recorded."""


class ClassifierAudit:
    def __init__(self, location: str, run_dir: Path | None, recorder: Any, quiet: bool):
        self.location = location
        self.run_dir = run_dir
        self.recorder = recorder
        self.quiet = quiet
        self.attempt = uuid.uuid4().hex
        self.path: Path | None = None

    def emit(self, event: str, data: dict[str, Any]) -> None:
        # File-only telemetry: quiet suppresses the separate terminal message,
        # never the ordinary audit record. Each attempt owns its append stream.
        if self.run_dir is not None:
            try:
                directory = self.run_dir / "logs"
                directory.mkdir(parents=True, exist_ok=True)
                path = directory / ("classifier-" + self.attempt + ".jsonl")
                fd = os.open(
                    path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(fd, "a", encoding="utf-8") as stream:
                    audit_log = structlog.wrap_logger(
                        structlog.PrintLogger(file=stream),
                        processors=[
                            structlog.processors.JSONRenderer(ensure_ascii=False)
                        ],
                    )
                    audit_log.info(
                        "classifier_event",
                        state=self.location,
                        attempt=self.attempt,
                        kind=event,
                        **data,
                    )
            except OSError:
                log.error("classifier_recording_failed", state=self.location)
                raise ClassifierRecordingError(
                    "classifier audit log could not be saved"
                ) from None
        if self.recorder is not None:
            self.recorder.record_classifier_event(
                self.location, self.attempt, event, data
            )
        if not self.quiet and event in {"fallback", "reason"}:
            display_classifier_event(self.location, event, data)

    def save_full(self, data: dict[str, Any]) -> None:
        try:
            content = json.dumps(
                {"state": self.location, "attempt": self.attempt, **data},
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            if len(content) > FULL_INPUT_LIMIT or self.run_dir is None:
                raise ClassifierRecordingError(
                    "classifier private record unavailable or exceeds 1MiB"
                )
            directory = self.run_dir / "classifier-inputs"
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if directory.is_symlink():
                raise ClassifierRecordingError(
                    "classifier private directory must not be a symlink"
                )
            directory.chmod(0o700)
            path = directory / (self.attempt + ".json")
            temporary = directory / (uuid.uuid4().hex + ".tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            self.path = path
        except (OSError, ValueError, TypeError, ClassifierRecordingError):
            log.error("classifier_recording_failed", state=self.location)
            raise ClassifierRecordingError(
                "classifier private record could not be saved (limit 1MiB)"
            ) from None

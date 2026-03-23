"""Task file models for batch task persistence.

Supports both flat single-task format and list multi-task format:
  # Flat (single task)
  description: "Fix the bug in the login flow"
  status: pending

  # List (multi-task)
  tasks:
    - description: "Fix the bug in the login flow"
      status: pending
    - description: "Write tests for the fix"
      status: pending
"""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class TaskEntry(BaseModel):
    """A single task within a task file."""

    description: str = Field(..., description="Human-readable task description")
    status: Literal["pending", "running", "completed", "failed"] = Field(
        default="pending", description="Task execution status"
    )
    workflow: str | None = Field(
        default=None,
        description="Workflow filename, resolved from workflows_dir",
    )
    thread_id: str | None = Field(
        default=None,
        description="Thread/connection ID for resuming a running task",
    )
    error: str | None = Field(
        default=None,
        description="Error message from last execution attempt",
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        valid = {"pending", "running", "completed", "failed"}
        if v not in valid:
            raise ValueError(
                f"status must be one of {', '.join(sorted(valid))}, got '{v}'"
            )
        return v

    @field_validator("workflow")
    @classmethod
    def validate_workflow_filename(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if "\\" in v or v in {".", ".."}:
            raise ValueError(f"workflow must be a relative path without .., got '{v}'")
        parts = Path(v).parts
        if ".." in parts or v.startswith("/") or v.startswith("\\"):
            raise ValueError(f"workflow must be a relative path without .., got '{v}'")
        if len(parts) > 2:
            raise ValueError(f"workflow nesting too deep (max 1 level), got '{v}'")
        return v


class TaskFile(BaseModel):
    """A task file containing one or more task entries.

    Internally always represented as a list of entries. On load, both flat
    (single-task) and list (multi-task) YAML formats are normalized to this
    unified representation.
    """

    entries: list[TaskEntry] = Field(
        default_factory=list,
        description="List of task entries in this file",
    )


_ALLOWED_SYSTEM_SYMLINK_ANCESTORS = {
    Path("/var"),
    Path("/tmp"),
}


def _ensure_no_symlink_ancestors(path: Path, *, include_self: bool) -> None:
    """Reject user-controlled symlink ancestors while allowing known system aliases."""
    current = path.absolute() if include_self else path.absolute().parent
    while current != current.parent:
        if (
            current.exists()
            and current.is_symlink()
            and current not in _ALLOWED_SYSTEM_SYMLINK_ANCESTORS
        ):
            raise ValueError(f"Refusing to write: ancestor is a symlink: {current}")
        current = current.parent


def load_task_file(path: Path) -> TaskFile:
    """Load a task file, normalizing flat or list format into TaskFile.

    Args:
        path: Path to the YAML task file.

    Returns:
        TaskFile with normalized entries list.

    Raises:
        FileNotFoundError: If the task file does not exist.
        ValueError: If the YAML is malformed.
    """
    if not path.exists():
        raise FileNotFoundError(f"Task file not found: {path}")

    open_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), open_flags)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ValueError(f"Refusing to read task file: {path} is a symlink") from e
        raise
    try:
        data = os.read(fd, os.fstat(fd).st_size)
        content = data.decode("utf-8")
    finally:
        os.close(fd)

    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in task file {path}: {e}") from e

    if raw is None:
        return TaskFile()

    if isinstance(raw, dict):
        if "tasks" in raw:
            tasks_raw = raw["tasks"]
            if not isinstance(tasks_raw, list):
                raise ValueError(
                    f"'tasks' key in {path} must be a list, "
                    f"got {type(tasks_raw).__name__}"
                )
            for i, t in enumerate(tasks_raw):
                if not isinstance(t, dict):
                    raise ValueError(
                        f"Task entry {i} in {path} must be a mapping, "
                        f"got {type(t).__name__}"
                    )
            entries = []
            for i, t in enumerate(tasks_raw):
                try:
                    entries.append(TaskEntry.model_validate(t))
                except ValidationError as e:
                    raise ValueError(f"Invalid task entry {i} in {path}: {e}") from e
        else:
            try:
                entries = [TaskEntry.model_validate(raw)]
            except ValidationError as e:
                raise ValueError(f"Invalid task entry in {path}: {e}") from e
    else:
        raise ValueError(
            f"Unexpected task file format at {path}: expected a YAML mapping"
        )

    return TaskFile(entries=entries)


def save_task_file(path: Path, task_file: TaskFile) -> None:
    """Save a task file to disk.

    Single-entry files are written in flat format; multi-entry files use the
    list format. None-valued fields are excluded for cleanliness.

    Args:
        path: Destination path.
        task_file: TaskFile to serialize.
    """
    # Reject user-controlled symlink ancestors before creating any directories.
    # Allow the platform temp aliases used on macOS (/var, /tmp).
    _ensure_no_symlink_ancestors(path, include_self=False)

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    os.chmod(str(path.parent), 0o700)  # tighten existing dirs too

    if len(task_file.entries) == 1:
        data = task_file.entries[0].model_dump(exclude_none=True)
    else:
        data = {"tasks": [e.model_dump(exclude_none=True) for e in task_file.entries]}

    content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)

    if path.is_symlink():
        raise ValueError(f"Refusing to write: target is a symlink: {path}")

    open_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW
    fd = os.open(str(path), open_flags, 0o600)
    try:
        os.fchmod(fd, 0o600)  # tighten existing files too
        mv = memoryview(content.encode("utf-8"))
        while mv:
            written = os.write(fd, mv)
            mv = mv[written:]
    finally:
        os.close(fd)

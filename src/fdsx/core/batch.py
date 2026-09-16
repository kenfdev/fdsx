"""Task queue persistence and display helpers."""

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import structlog

from fdsx.display.terminal import _sanitize_output
from fdsx.models.task import (
    TaskEntry,
    TaskFile,
    _ensure_no_symlink_ancestors,
    save_task_file,
)

logger = structlog.get_logger(__name__)

TASKS_DIR = ".fdsx/tasks"
COMPLETED_SUBDIR = "completed"


def _slugify(text: str, max_length: int = 40) -> str:
    """Convert text to a URL-safe slug for use in filenames."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "task"


def _scan_max_task_index(tasks_dir: Path) -> int:
    """Return the highest numbered task file in active and completed queues."""
    max_idx = 0
    for scan_dir in (tasks_dir, tasks_dir / COMPLETED_SUBDIR):
        if not scan_dir.is_dir():
            continue
        for file_path in scan_dir.glob("*.yaml"):
            match = re.match(r"^(\d+)-", file_path.name)
            if match:
                max_idx = max(max_idx, int(match.group(1)))
    return max_idx


def _publish_task_file(path: Path, task_file: TaskFile) -> None:
    """Publish a complete task file without exposing a partial YAML document."""
    temporary_path = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        save_task_file(temporary_path, task_file)
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_task_files(
    groups: list[list[TaskEntry]], tasks_dir: Path, *, source: str | None = None
) -> list[Path]:
    """Write task groups to sequentially numbered queue files."""
    _ensure_no_symlink_ancestors(tasks_dir, include_self=True)
    tasks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    base_index = _scan_max_task_index(tasks_dir)
    created_files: list[Path] = []

    for offset, group in enumerate(groups, start=1):
        if not group:
            continue
        task_file = TaskFile(entries=group, source=source)
        destination = (
            tasks_dir
            / f"{base_index + offset:03d}-{_slugify(group[0].description)}.yaml"
        )
        _publish_task_file(destination, task_file)
        created_files.append(destination)

    return created_files


def _read_task_sources(task_files: list[Path]) -> list[tuple[Path, str]]:
    """Validate and read every source before queue files are created."""
    seen_paths: set[Path] = set()
    sources: list[tuple[Path, str]] = []

    for source_path in task_files:
        resolved_path = source_path.resolve(strict=False)
        if resolved_path in seen_paths:
            raise ValueError(f"Duplicate task file: {source_path}")
        seen_paths.add(resolved_path)
        if source_path.is_symlink():
            raise ValueError(f"Task file must not be a symlink: {source_path}")
        if not source_path.exists():
            raise ValueError(f"Task file not found: {source_path}")
        if not source_path.is_file():
            raise ValueError(f"Task file must be a regular file: {source_path}")

        content = source_path.read_text(encoding="utf-8")
        if not content.strip():
            raise ValueError(f"Task file is empty: {source_path}")
        sources.append((source_path, content))

    return sources


def queue_task_files(task_files: list[Path], tasks_dir: Path) -> list[Path]:
    """Append source files to a task queue in the order provided."""
    try:
        sources = _read_task_sources(task_files)
        _ensure_no_symlink_ancestors(tasks_dir, include_self=True)
    except (OSError, UnicodeError, ValueError) as error:
        logger.warning("task_queue_validation_failed", error=str(error))
        raise

    tasks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    base_index = _scan_max_task_index(tasks_dir)
    created_files: list[Path] = []

    for offset, (source_path, content) in enumerate(sources, start=1):
        task_file = TaskFile(
            entries=[TaskEntry(description=content)],
            source=str(source_path),
        )
        destination = (
            tasks_dir / f"{base_index + offset:03d}-{_slugify(source_path.stem)}.yaml"
        )
        try:
            _publish_task_file(destination, task_file)
        except OSError as error:
            logger.error(
                "task_queue_write_failed",
                source=str(source_path),
                destination=str(destination),
                error=str(error),
            )
            raise
        created_files.append(destination)

    return created_files


def move_task_to_completed(file_path: Path) -> None:
    """Move a fully processed task file to its completed subdirectory."""
    completed_dir = file_path.parent / COMPLETED_SUBDIR
    _ensure_no_symlink_ancestors(completed_dir, include_self=True)
    completed_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = completed_dir / file_path.name
    if destination.exists():
        raise FileExistsError(
            f"Destination already exists in completed/: {destination}"
        )
    shutil.move(str(file_path), str(destination))


def display_tasks_dir_summary(results: list[dict[str, Any]]) -> None:
    """Display a summary of tasks-directory execution results."""
    print("\n" + "=" * 80, file=sys.stderr)
    print("TASKS-DIR EXECUTION SUMMARY", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(
        f"{'FILE':<30} {'ENTRY':<6} {'CAT':<8} {'STATUS':<12} "
        f"{'THREAD_ID':<36} {'TASK':<20}",
        file=sys.stderr,
    )
    print("-" * 80, file=sys.stderr)

    for result in results:
        file_name = result.get("file_name", "")[:30]
        entry_idx = result.get("entry_index", -1)
        category = result.get("category", "new")
        status = result.get("status", "unknown")
        thread_id = result.get("thread_id", "")[:36]
        entry_desc = result.get("entry_description", "")[:20]
        symbol_map = {
            "skipped": "⊘",
            "retried": "↻",
            "new": "○",
            "completed": "✓",
        }
        status_symbol = symbol_map.get(category, "?") if status == "completed" else "✗"
        entry_display = str(entry_idx + 1) if entry_idx >= 0 else "-"
        print(
            f"{_sanitize_output(file_name):<30} {entry_display:<6} {category:<8} "
            f"{status_symbol} {status:<10} {_sanitize_output(thread_id):<36} "
            f"{_sanitize_output(entry_desc):<20}",
            file=sys.stderr,
        )
        if result.get("error"):
            error_preview = result["error"][:70]
            print(f"       Error: {_sanitize_output(error_preview)}", file=sys.stderr)

    print("-" * 80, file=sys.stderr)
    skipped = sum(1 for result in results if result.get("category") == "skipped")
    retried = sum(1 for result in results if result.get("category") == "retried")
    new_total = sum(1 for result in results if result.get("category") == "new")
    failed = sum(1 for result in results if result.get("status") == "failed")
    print(
        f"Total: {len(results)} | Skipped: {skipped} | Retried: {retried} | "
        f"New: {new_total} | Failed: {failed}",
        file=sys.stderr,
    )
    print("=" * 80, file=sys.stderr)

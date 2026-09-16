from pathlib import Path

import yaml
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core import engine


def test_user_can_append_multiple_task_files_in_argument_order(
    tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "task-a.md"
    second = tmp_path / "fix-login.txt"
    first.write_text("First task\nwith details")
    second.write_text("Second task")
    tasks_dir = tmp_path / ".fdsx" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "003-existing.yaml").write_text("description: Existing task\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["add", str(first), str(second)])

    assert result.exit_code == 0, result.stderr
    queued = engine.load_tasks_dir(tasks_dir)
    assert [path.name for path, _ in queued] == [
        "003-existing.yaml",
        "004-task-a.yaml",
        "005-fix-login.yaml",
    ]
    assert [task.entries[0].description for _, task in queued] == [
        "Existing task",
        "First task\nwith details",
        "Second task",
    ]
    assert [task.source for _, task in queued[1:]] == [str(first), str(second)]


def test_invalid_later_input_fails_before_any_task_is_queued(
    tmp_path: Path, monkeypatch
) -> None:
    valid = tmp_path / "valid.md"
    valid.write_text("Valid task")
    missing = tmp_path / "missing.md"
    tasks_dir = tmp_path / ".fdsx" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "003-existing.yaml").write_text("description: Existing task\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["add", str(valid), str(missing)])

    assert result.exit_code == 2
    assert "not found" in result.stderr.lower()
    queued = engine.load_tasks_dir(tasks_dir)
    assert [path.name for path, _ in queued] == ["003-existing.yaml"]


def test_duplicate_input_paths_fail_before_any_task_is_queued(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "task.md"
    source.write_text("Task")
    tasks_dir = tmp_path / ".fdsx" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "003-existing.yaml").write_text("description: Existing task\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["add", str(source), str(source)])

    assert result.exit_code == 2
    assert "duplicate" in result.stderr.lower()
    queued = engine.load_tasks_dir(tasks_dir)
    assert [path.name for path, _ in queued] == ["003-existing.yaml"]


def test_empty_input_fails_before_any_task_is_queued(
    tmp_path: Path, monkeypatch
) -> None:
    valid = tmp_path / "valid.md"
    empty = tmp_path / "empty.md"
    valid.write_text("Valid task")
    empty.write_text(" \n\t")
    tasks_dir = tmp_path / ".fdsx" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "003-existing.yaml").write_text("description: Existing task\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["add", str(valid), str(empty)])

    assert result.exit_code == 2
    assert "empty" in result.stderr.lower()
    queued = engine.load_tasks_dir(tasks_dir)
    assert [path.name for path, _ in queued] == ["003-existing.yaml"]


def test_symlink_input_fails_before_any_task_is_queued(
    tmp_path: Path, monkeypatch
) -> None:
    real = tmp_path / "real.md"
    link = tmp_path / "link.md"
    real.write_text("Task")
    link.symlink_to(real)
    tasks_dir = tmp_path / ".fdsx" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "003-existing.yaml").write_text("description: Existing task\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["add", str(link)])

    assert result.exit_code == 2
    assert "symlink" in result.stderr.lower()
    queued = engine.load_tasks_dir(tasks_dir)
    assert [path.name for path, _ in queued] == ["003-existing.yaml"]


def test_add_uses_the_configured_default_tasks_directory(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "task.md"
    source.write_text("Configured queue")
    fdsx_dir = tmp_path / ".fdsx"
    fdsx_dir.mkdir()
    (fdsx_dir / "config.yaml").write_text(
        yaml.safe_dump({"default_tasks_dir": "custom-queue"})
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["add", str(source)])

    assert result.exit_code == 0, result.stderr
    queued = engine.load_tasks_dir(tmp_path / "custom-queue")
    assert [path.name for path, _ in queued] == ["001-task.yaml"]
    assert queued[0][1].entries[0].description == "Configured queue"

from pathlib import Path

import pytest

from fdsx.core.batch import (
    COMPLETED_SUBDIR,
    move_task_to_completed,
    write_task_files,
)
from fdsx.models.task import TaskEntry, load_task_file


class TestWriteTaskFiles:
    def test_writes_groups_as_numbered_task_files(self, tmp_path: Path) -> None:
        groups = [
            [TaskEntry(description="Task 1"), TaskEntry(description="Task 2")],
            [TaskEntry(description="Task 3")],
        ]

        created = write_task_files(groups, tmp_path / "tasks")

        assert [path.name for path in created] == [
            "001-task-1.yaml",
            "002-task-3.yaml",
        ]
        assert [entry.description for entry in load_task_file(created[0]).entries] == [
            "Task 1",
            "Task 2",
        ]

    def test_numbering_continues_after_active_and_completed_files(
        self, tmp_path: Path
    ) -> None:
        tasks_dir = tmp_path / "tasks"
        completed_dir = tasks_dir / COMPLETED_SUBDIR
        completed_dir.mkdir(parents=True)
        (tasks_dir / "003-active.yaml").write_text("")
        (completed_dir / "007-completed.yaml").write_text("")

        created = write_task_files(
            [[TaskEntry(description="New task")]],
            tasks_dir,
        )

        assert [path.name for path in created] == ["008-new-task.yaml"]

    def test_rejects_symlinked_parent(self, tmp_path: Path) -> None:
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        with pytest.raises(ValueError, match="Refusing to write"):
            write_task_files(
                [[TaskEntry(description="Task")]],
                link_dir / "tasks",
            )


class TestMoveTaskToCompleted:
    def test_moves_file_without_changing_its_contents(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        task_file = tasks_dir / "001-task.yaml"
        content = "description: important content\nstatus: completed\n"
        task_file.write_text(content)

        move_task_to_completed(task_file)

        destination = tasks_dir / COMPLETED_SUBDIR / task_file.name
        assert not task_file.exists()
        assert destination.read_text() == content

    def test_refuses_to_overwrite_completed_file(self, tmp_path: Path) -> None:
        tasks_dir = tmp_path / "tasks"
        completed_dir = tasks_dir / COMPLETED_SUBDIR
        completed_dir.mkdir(parents=True)
        task_file = tasks_dir / "001-task.yaml"
        task_file.write_text("description: new\n")
        (completed_dir / task_file.name).write_text("description: existing\n")

        with pytest.raises(FileExistsError, match="already exists"):
            move_task_to_completed(task_file)

        assert task_file.exists()

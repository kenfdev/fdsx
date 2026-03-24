"""Tests for task file models."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file


class TestTaskEntryDefaults:
    def test_default_status_pending(self):
        entry = TaskEntry(description="Do the thing")
        assert entry.status == "pending"

    def test_all_statuses_allowed(self):
        for status in ("pending", "running", "completed", "failed"):
            entry = TaskEntry(description="test", status=status)
            assert entry.status == status

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            TaskEntry(description="test", status="invalid")

    def test_thread_id_and_error_optional(self):
        entry = TaskEntry(description="test", thread_id="abc123", error="boom")
        assert entry.thread_id == "abc123"
        assert entry.error == "boom"

    def test_workflow_optional(self):
        entry = TaskEntry(description="test", workflow="plan.yaml")
        assert entry.workflow == "plan.yaml"

    def test_workflow_default_none(self):
        entry = TaskEntry(description="test")
        assert entry.workflow is None

    def test_workflow_path_traversal_rejected(self):
        with pytest.raises(ValidationError):
            TaskEntry(description="test", workflow="../../evil.yaml")

    def test_workflow_absolute_path_rejected(self):
        with pytest.raises(ValidationError):
            TaskEntry(description="test", workflow="/etc/evil.yaml")

    def test_workflow_valid_filename_accepted(self):
        entry = TaskEntry(description="test", workflow="plan-review.yaml")
        assert entry.workflow == "plan-review.yaml"

    def test_workflow_backslash_rejected(self):
        """SEC-R2-3: explicit backslash must be rejected on all platforms."""
        with pytest.raises(ValidationError):
            TaskEntry(description="test", workflow="subdir\\evil.yaml")

    def test_workflow_double_dot_filename_accepted(self):
        """CQ-R3-2: plan..review.yaml is a valid filename, not path traversal."""
        entry = TaskEntry(description="test", workflow="plan..review.yaml")
        assert entry.workflow == "plan..review.yaml"

    def test_workflow_bare_dotdot_rejected(self):
        """CQ-R4-1: bare '..' must be explicitly rejected as a special path component."""
        with pytest.raises(ValidationError):
            TaskEntry(description="test", workflow="..")

    def test_workflow_bare_dot_rejected(self):
        """CQ-R4-1: bare '.' must also be explicitly rejected."""
        with pytest.raises(ValidationError):
            TaskEntry(description="test", workflow=".")


class TestTaskFileSource:
    def test_source_defaults_to_none(self):
        tf = TaskFile(entries=[TaskEntry(description="test")])
        assert tf.source is None

    def test_source_can_be_set(self):
        tf = TaskFile(entries=[TaskEntry(description="test")], source="cli")
        assert tf.source == "cli"

    def test_source_accepts_arbitrary_string(self):
        """T001: TaskFile.source accepts an arbitrary path string."""
        tf = TaskFile(entries=[TaskEntry(description="test")], source="/path/to/tasks.yaml")
        assert tf.source == "/path/to/tasks.yaml"

    def test_source_accepts_none_explicitly(self):
        """T001: TaskFile.source=None is accepted explicitly."""
        tf = TaskFile(entries=[TaskEntry(description="test")], source=None)
        assert tf.source is None


class TestTaskFileSingleEntry:
    def test_flat_format_parsed(self):
        tf = TaskFile(entries=[TaskEntry(description="Fix the bug", status="pending")])
        assert len(tf.entries) == 1
        assert tf.entries[0].description == "Fix the bug"

    def test_flat_format_round_trip(self):
        entry = TaskEntry(description="Fix the bug", status="pending")
        tf = TaskFile(entries=[entry])
        data = tf.model_dump()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["description"] == "Fix the bug"
        assert data["entries"][0]["status"] == "pending"


class TestTaskFileMultiEntry:
    def test_multiple_entries(self):
        entries = [
            TaskEntry(description="Task 1"),
            TaskEntry(description="Task 2"),
        ]
        tf = TaskFile(entries=entries)
        assert len(tf.entries) == 2

    def test_multi_entry_format_round_trip(self):
        entries = [TaskEntry(description="Task 1"), TaskEntry(description="Task 2")]
        tf = TaskFile(entries=entries)
        data = tf.model_dump()
        assert len(data["entries"]) == 2


class TestLoadTaskFileFlat:
    def test_loads_flat_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            path.write_text(
                yaml.dump({"description": "Fix login bug", "status": "pending"})
            )

            tf = load_task_file(path)
            assert len(tf.entries) == 1
            assert tf.entries[0].description == "Fix login bug"
            assert tf.entries[0].status == "pending"

    def test_loads_flat_yaml_default_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            path.write_text(yaml.dump({"description": "Fix login bug"}))

            tf = load_task_file(path)
            assert tf.entries[0].status == "pending"

    def test_loads_flat_yaml_with_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            path.write_text(
                yaml.dump({"description": "Fix login bug", "source": "cli"})
            )

            tf = load_task_file(path)
            assert tf.source == "cli"

    def test_loads_flat_yaml_without_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            path.write_text(yaml.dump({"description": "Fix login bug"}))

            tf = load_task_file(path)
            assert tf.source is None

    def test_raises_on_invalid_source_type_flat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            path.write_text(yaml.dump({"description": "Fix bug", "source": ["bad"]}))
            with pytest.raises(ValueError, match="Invalid task file metadata"):
                load_task_file(path)

    def test_flat_yaml_source_not_leaked_into_entry(self):
        """Finding-1 regression: source key must be stripped before TaskEntry.model_validate
        in the flat branch, so it never contaminates TaskEntry fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            path.write_text(
                yaml.dump({"description": "Fix login bug", "source": "cli"})
            )

            tf = load_task_file(path)
            assert len(tf.entries) == 1
            # source goes to TaskFile.source, not into the entry's fields
            assert tf.source == "cli"
            entry_dict = tf.entries[0].model_dump(exclude_none=True)
            assert "source" not in entry_dict

    def test_flat_yaml_source_initialized_before_isinstance_block(self):
        """Finding-4 regression: source must be initialized to None before the
        isinstance(raw, dict) block so it is never unbound when TaskFile is constructed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            # Standard flat YAML without source — source must be None, not unbound
            path.write_text(yaml.dump({"description": "Simple task"}))

            tf = load_task_file(path)
            assert tf.source is None


class TestLoadTaskFileList:
    def test_loads_list_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            path.write_text(
                yaml.dump(
                    {
                        "tasks": [
                            {"description": "Fix login bug", "status": "pending"},
                            {"description": "Write tests", "status": "completed"},
                        ]
                    }
                )
            )

            tf = load_task_file(path)
            assert len(tf.entries) == 2
            assert tf.entries[0].description == "Fix login bug"
            assert tf.entries[1].description == "Write tests"
            assert tf.entries[1].status == "completed"

    def test_loads_list_yaml_default_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            path.write_text(yaml.dump({"tasks": [{"description": "Fix login bug"}]}))

            tf = load_task_file(path)
            assert tf.entries[0].status == "pending"

    def test_loads_list_yaml_with_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            path.write_text(
                yaml.dump(
                    {
                        "source": "api",
                        "tasks": [
                            {"description": "Fix login bug", "status": "pending"},
                        ],
                    }
                )
            )

            tf = load_task_file(path)
            assert tf.source == "api"

    def test_loads_list_yaml_without_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            path.write_text(
                yaml.dump(
                    {
                        "tasks": [
                            {"description": "Fix login bug", "status": "pending"},
                        ],
                    }
                )
            )

            tf = load_task_file(path)
            assert tf.source is None

    def test_raises_on_invalid_source_type_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            path.write_text(
                yaml.dump(
                    {
                        "tasks": [{"description": "Fix bug"}],
                        "source": ["bad"],
                    }
                )
            )
            with pytest.raises(ValueError, match="Invalid task file metadata"):
                load_task_file(path)


class TestLoadTaskFileRawListRejected:
    def test_raw_list_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            path.write_text(yaml.dump([{"description": "Task 1"}]))
            with pytest.raises(ValueError, match="expected a YAML mapping"):
                load_task_file(path)


class TestLoadTaskFileEdgeCases:
    def test_empty_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.yaml"
            path.write_text("")

            tf = load_task_file(path)
            assert len(tf.entries) == 0

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            load_task_file(Path("/nonexistent/task.yaml"))

    def test_invalid_yaml_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.yaml"
            path.write_text(": :\n  - [invalid yaml {{")
            with pytest.raises(ValueError, match="Invalid YAML"):
                load_task_file(path)

    def test_tasks_key_not_list_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            path.write_text(yaml.dump({"tasks": "oops"}))
            with pytest.raises(ValueError, match="must be a list"):
                load_task_file(path)

    def test_tasks_entry_not_mapping_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            path.write_text(yaml.dump({"tasks": ["just a string"]}))
            with pytest.raises(ValueError, match="must be a mapping"):
                load_task_file(path)

    def test_flat_missing_description_raises_value_error(self):
        """CQ-R2-2: Pydantic ValidationError must be wrapped as ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            path.write_text(yaml.dump({"status": "pending"}))
            with pytest.raises(ValueError, match="Invalid task entry in"):
                load_task_file(path)

    def test_list_invalid_entry_raises_value_error(self):
        """CQ-R2-2: ValidationError in list entries must be wrapped as ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            path.write_text(yaml.dump({"tasks": [{"status": "pending"}]}))
            with pytest.raises(ValueError, match="Invalid task entry 0 in"):
                load_task_file(path)


class TestSaveTaskFilePermissions:
    def test_save_sets_file_mode_0o600(self):
        """SEC-R2-1: saved file must have mode 0o600."""
        import stat

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            tf = TaskFile(entries=[TaskEntry(description="Secure task")])
            save_task_file(path, tf)
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"

    def test_save_sets_dir_mode_0o700(self):
        """SEC-R2-1: parent directory must be tightened to 0o700."""
        import stat

        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir(mode=0o755)
            path = subdir / "task.yaml"
            tf = TaskFile(entries=[TaskEntry(description="Secure task")])
            save_task_file(path, tf)
            mode = stat.S_IMODE(subdir.stat().st_mode)
            assert mode == 0o700, f"Expected 0o700, got {oct(mode)}"


class TestSaveTaskFileSymlinkProtection:
    def test_rejects_symlinked_parent(self):
        """SEC-R4-1: write to path under a symlinked parent dir must raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = Path(tmpdir) / "real"
            real_dir.mkdir()
            sym_dir = Path(tmpdir) / "sym"
            sym_dir.symlink_to(real_dir)
            path = sym_dir / "task.yaml"
            tf = TaskFile(entries=[TaskEntry(description="test")])
            with pytest.raises(ValueError, match="ancestor is a symlink"):
                save_task_file(path, tf)

    def test_rejects_symlinked_ancestor(self):
        """SEC-R4-1: ancestor symlink (not just immediate parent) must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            real_dir = Path(tmpdir) / "real"
            real_dir.mkdir()
            tasks_dir = real_dir / "tasks"
            tasks_dir.mkdir()
            sym_ancestor = Path(tmpdir) / "project" / ".fdsx"
            sym_ancestor.parent.mkdir()
            sym_ancestor.symlink_to(real_dir)
            path = sym_ancestor / "tasks" / "task.yaml"
            tf = TaskFile(entries=[TaskEntry(description="test")])
            with pytest.raises(ValueError, match="ancestor is a symlink"):
                save_task_file(path, tf)

    def test_rejects_symlinked_file(self):
        """SEC-R3-1: write to a symlinked file path must raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            real_file = Path(tmpdir) / "real.yaml"
            real_file.write_text("placeholder")
            sym_file = Path(tmpdir) / "task.yaml"
            sym_file.symlink_to(real_file)
            tf = TaskFile(entries=[TaskEntry(description="test")])
            with pytest.raises(ValueError, match="target is a symlink"):
                save_task_file(sym_file, tf)


class TestSaveTaskFileFlat:
    def test_save_flat_single_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            tf = TaskFile(
                entries=[TaskEntry(description="Fix the bug", status="pending")]
            )
            save_task_file(path, tf)

            content = path.read_text()
            data = yaml.safe_load(content)
            assert data["description"] == "Fix the bug"
            assert data["status"] == "pending"

    def test_save_excludes_none_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            tf = TaskFile(
                entries=[
                    TaskEntry(description="Fix the bug", thread_id=None, error=None)
                ]
            )
            save_task_file(path, tf)

            content = path.read_text()
            assert "null" not in content
            assert "None" not in content

    def test_save_flat_with_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            tf = TaskFile(
                entries=[TaskEntry(description="Fix the bug", status="pending")],
                source="cli",
            )
            save_task_file(path, tf)

            content = path.read_text()
            data = yaml.safe_load(content)
            assert data["source"] == "cli"

    def test_save_flat_without_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            tf = TaskFile(
                entries=[TaskEntry(description="Fix the bug", status="pending")]
            )
            save_task_file(path, tf)

            content = path.read_text()
            data = yaml.safe_load(content)
            assert "source" not in data

    def test_save_multi_with_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            tf = TaskFile(
                entries=[
                    TaskEntry(description="Task 1"),
                    TaskEntry(description="Task 2"),
                ],
                source="api",
            )
            save_task_file(path, tf)

            content = path.read_text()
            data = yaml.safe_load(content)
            assert data["source"] == "api"
            assert "tasks" in data

    def test_save_multi_without_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            tf = TaskFile(
                entries=[
                    TaskEntry(description="Task 1"),
                    TaskEntry(description="Task 2"),
                ]
            )
            save_task_file(path, tf)

            content = path.read_text()
            data = yaml.safe_load(content)
            assert "source" not in data


class TestSaveTaskFileMulti:
    def test_save_multi_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            tf = TaskFile(
                entries=[
                    TaskEntry(description="Task 1"),
                    TaskEntry(description="Task 2"),
                ]
            )
            save_task_file(path, tf)

            content = path.read_text()
            data = yaml.safe_load(content)
            assert "tasks" in data
            assert len(data["tasks"]) == 2


class TestRoundTrip:
    def test_single_entry_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            original = TaskFile(
                entries=[
                    TaskEntry(
                        description="Fix bug", status="completed", thread_id="abc"
                    )
                ]
            )
            save_task_file(path, original)

            loaded = load_task_file(path)
            assert len(loaded.entries) == 1
            assert loaded.entries[0].description == "Fix bug"
            assert loaded.entries[0].status == "completed"

    def test_multi_entry_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            original = TaskFile(
                entries=[
                    TaskEntry(description="Task 1", status="completed"),
                    TaskEntry(description="Task 2", status="failed", error="segfault"),
                ]
            )
            save_task_file(path, original)

            loaded = load_task_file(path)
            assert len(loaded.entries) == 2
            assert loaded.entries[1].description == "Task 2"
            assert loaded.entries[1].status == "failed"
            assert loaded.entries[1].error == "segfault"

    def test_single_entry_with_workflow_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            original = TaskFile(
                entries=[
                    TaskEntry(
                        description="Fix the bug",
                        status="pending",
                        workflow="plan.yaml",
                    )
                ]
            )
            save_task_file(path, original)

            loaded = load_task_file(path)
            assert len(loaded.entries) == 1
            assert loaded.entries[0].workflow == "plan.yaml"

    def test_single_entry_with_source_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "task.yaml"
            original = TaskFile(
                entries=[TaskEntry(description="Fix bug", status="pending")],
                source="cli",
            )
            save_task_file(path, original)

            loaded = load_task_file(path)
            assert len(loaded.entries) == 1
            assert loaded.source == "cli"

    def test_multi_entry_with_source_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tasks.yaml"
            original = TaskFile(
                entries=[
                    TaskEntry(description="Task 1", status="completed"),
                    TaskEntry(description="Task 2", status="failed"),
                ],
                source="api",
            )
            save_task_file(path, original)

            loaded = load_task_file(path)
            assert len(loaded.entries) == 2
            assert loaded.source == "api"


class TestLoadTaskFileSymlinkProtection:
    """SEC: TOCTOU — load_task_file() must refuse symlinks at the read call site."""

    def test_load_rejects_symlinked_task_file(self):
        """SEC-TOCTOU-1: load_task_file() must raise ValueError when target is a symlink.

        This covers the TOCTOU race window: even if a pre-check in the caller passed,
        a symlink swapped in before open() must be rejected by O_NOFOLLOW.
        """
        if not hasattr(__import__("os"), "O_NOFOLLOW"):
            pytest.skip("O_NOFOLLOW not available on this platform")

        with tempfile.TemporaryDirectory() as tmpdir:
            real_file = Path(tmpdir) / "real.yaml"
            real_file.write_text(
                yaml.dump({"description": "Real task", "status": "pending"})
            )
            sym_file = Path(tmpdir) / "task.yaml"
            sym_file.symlink_to(real_file)

            with pytest.raises(ValueError, match="symlink"):
                load_task_file(sym_file)


class TestSaveTaskFileSecurity:
    def test_symlinked_ancestor_raises_before_mkdir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            safe_dir = Path(tmpdir) / "safe"
            safe_dir.mkdir()
            external_dir = Path(tmpdir) / "external"
            external_dir.mkdir()
            symlink_dir = safe_dir / ".fdsx"
            symlink_dir.symlink_to(external_dir)

            path = symlink_dir / "tasks.yaml"
            tf = TaskFile(entries=[TaskEntry(description="Task 1")])

            with pytest.raises(ValueError, match="ancestor is a symlink"):
                save_task_file(path, tf)

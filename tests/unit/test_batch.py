import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fdsx.core.batch import (
    COMPLETED_SUBDIR,
    TASKS_DIR,
    _build_task_split_prompt,
    _parse_structured_tasks,
    _scan_max_task_index,
    _slugify,
    move_task_to_completed,
    split_tasks_to_groups,
    write_task_files,
)
from fdsx.core.config import TaskSplitterConfig
from fdsx.models.task import TaskEntry


class TestSplitTasksToGroups:
    def test_split_tasks_to_groups_parses_json(self):
        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Task A"}, {"description": "Task B"}], [{"description": "Task C"}]]',
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            groups = split_tasks_to_groups("test content", task_splitter)

        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 1
        assert groups[0][0].description == "Task A"
        assert groups[0][1].description == "Task B"
        assert groups[1][0].description == "Task C"

    def test_split_tasks_to_groups_with_optional_context(self):
        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Task 1"}]]',
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            groups = split_tasks_to_groups(
                "test content",
                task_splitter,
                state_names=["plan", "implement"],
                input_vars={"task", "context"},
            )

        assert len(groups) == 1
        assert groups[0][0].description == "Task 1"

    def test_split_tasks_to_groups_provider_failure(self):
        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=1,
            stdout="",
            stderr="Provider error",
        )

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            pytest.raises(RuntimeError, match="Task splitter failed"),
        ):
            split_tasks_to_groups("test content", task_splitter)

    def test_retry_success_on_invalid_json(self):
        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.side_effect = [
            MagicMock(exit_code=0, stdout="not valid json", stderr=""),
            MagicMock(
                exit_code=0,
                stdout='[[{"description": "Task A"}, {"description": "Task B"}]]',
                stderr="",
            ),
        ]

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            groups = split_tasks_to_groups("test content", task_splitter)

        assert len(groups) == 1
        assert len(groups[0]) == 2
        assert groups[0][0].description == "Task A"
        assert groups[0][1].description == "Task B"
        assert mock_provider.execute.call_count == 2

    def test_retry_fail_raises_both_errors(self):
        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.side_effect = [
            MagicMock(exit_code=0, stdout="first invalid json", stderr=""),
            MagicMock(exit_code=0, stdout="second invalid json", stderr=""),
        ]

        with (
            patch("fdsx.core.batch.get_provider", return_value=mock_provider),
            pytest.raises(ValueError, match="Attempt 1") as exc_info,
        ):
            split_tasks_to_groups("test content", task_splitter)

        assert "Attempt 2" in str(exc_info.value)
        assert mock_provider.execute.call_count == 2

    def test_progress_callback_messages(self):
        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Task A"}]]',
            stderr="",
        )

        progress_messages = []
        progress_callback = progress_messages.append

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            groups = split_tasks_to_groups(
                "test content",
                task_splitter,
                progress=progress_callback,
            )

        assert "Calling task splitter" in progress_messages[0]
        assert f"Splitter produced {len(groups)} task group(s)" in progress_messages[-1]
        assert mock_provider.execute.call_count == 1

    def test_progress_callback_retry_messages(self):
        task_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.side_effect = [
            MagicMock(exit_code=0, stdout="invalid json", stderr=""),
            MagicMock(
                exit_code=0,
                stdout='[[{"description": "Task A"}]]',
                stderr="",
            ),
        ]

        progress_messages = []
        progress_callback = progress_messages.append

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            split_tasks_to_groups(
                "test content",
                task_splitter,
                progress=progress_callback,
            )

        assert any("retrying" in msg.lower() for msg in progress_messages)
        assert mock_provider.execute.call_count == 2


class TestParseStructuredTasks:
    def test_parse_json_with_code_block(self):
        response = """```json
[[{"description": "Task 1"}, {"description": "Task 2"}], [{"description": "Task 3"}]]
```"""
        groups = _parse_structured_tasks(response)

        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 1
        assert groups[0][0].description == "Task 1"
        assert groups[0][1].description == "Task 2"
        assert groups[1][0].description == "Task 3"

    def test_parse_json_without_code_block(self):
        response = '[[{"description": "Task A"}, {"description": "Task B"}]]'
        groups = _parse_structured_tasks(response)

        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_parse_empty_groups(self):
        response = "[]"
        groups = _parse_structured_tasks(response)

        assert groups == []

    def test_parse_invalid_json(self):
        response = "not valid json"
        with pytest.raises(ValueError, match="Failed to parse JSON"):
            _parse_structured_tasks(response)

    def test_parse_invalid_format_not_array(self):
        response = '{"description": "Single task"}'
        with pytest.raises(ValueError, match="Expected JSON array"):
            _parse_structured_tasks(response)

    def test_parse_invalid_group_not_array(self):
        response = '[{"description": "Single task"}]'
        with pytest.raises(ValueError, match="Group 0 must be an array"):
            _parse_structured_tasks(response)

    def test_parse_missing_description(self):
        response = '[[{"name": "Task without description"}]]'
        with pytest.raises(ValueError, match="missing required 'description'"):
            _parse_structured_tasks(response)

    def test_parse_task_entries_have_correct_defaults(self):
        response = '[[{"description": "Test task"}]]'
        groups = _parse_structured_tasks(response)

        assert len(groups) == 1
        assert groups[0][0].status == "pending"
        assert groups[0][0].workflow is None
        assert groups[0][0].thread_id is None
        assert groups[0][0].error is None


class TestWriteTaskFiles:
    def test_write_task_files_creates_numbered_files(self):
        groups = [
            [TaskEntry(description="Task 1"), TaskEntry(description="Task 2")],
            [TaskEntry(description="Task 3")],
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir) / "tasks"
            created = write_task_files(groups, tasks_dir)

            assert len(created) == 2
            assert (tasks_dir / "001-task-1.yaml").exists()
            assert (tasks_dir / "002-task-3.yaml").exists()

    def test_write_task_files_skips_empty_groups(self):
        groups = [
            [TaskEntry(description="Task 1")],
            [],
            [TaskEntry(description="Task 2")],
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir) / "tasks"
            created = write_task_files(groups, tasks_dir)

            assert len(created) == 2
            assert (tasks_dir / "001-task-1.yaml").exists()
            assert not any(f.name.startswith("002-") for f in tasks_dir.iterdir())
            assert (tasks_dir / "003-task-2.yaml").exists()

    def test_write_task_files_creates_correct_yaml_content(self):
        groups = [
            [TaskEntry(description="First task"), TaskEntry(description="Second task")],
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir) / "tasks"
            write_task_files(groups, tasks_dir)

            content = (tasks_dir / "001-first-task.yaml").read_text()
            assert "First task" in content
            assert "Second task" in content

    def test_write_task_files_rejects_symlinked_parent(self, tmp_path):
        groups = [
            [TaskEntry(description="Task 1")],
        ]

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        with pytest.raises(ValueError, match="Refusing to write"):
            write_task_files(groups, link_dir / "tasks")


class TestBuildTaskSplitPrompt:
    def test_extra_instructions_inserts_section(self):
        prompt = _build_task_split_prompt(
            "test content", None, None, extra_instructions="Group by package"
        )

        assert "ADDITIONAL INSTRUCTIONS:\nGroup by package" in prompt
        assert prompt.index("ADDITIONAL INSTRUCTIONS:") < prompt.index("OUTPUT FORMAT:")

    def test_extra_instructions_none_leaves_prompt_unchanged(self):
        baseline = _build_task_split_prompt("test content", None, None)
        result = _build_task_split_prompt(
            "test content", None, None, extra_instructions=None
        )

        assert result == baseline

    def test_extra_instructions_empty_string_leaves_prompt_unchanged(self):
        baseline = _build_task_split_prompt("test content", None, None)
        result = _build_task_split_prompt(
            "test content", None, None, extra_instructions=""
        )

        assert result == baseline

    def test_build_prompt_with_state_names_and_input_vars(self):
        prompt = _build_task_split_prompt(
            "test content", ["plan", "implement"], {"task"}
        )

        assert "test content" in prompt
        assert "plan, implement" in prompt
        assert "task" in prompt
        assert "JSON" in prompt

    def test_build_prompt_without_optional_params(self):
        prompt = _build_task_split_prompt("test content", None, None)

        assert "test content" in prompt
        assert "any workflow" in prompt
        assert "task" in prompt


class TestTaskSplitterConfigValidation:
    def test_task_splitter_config_rejects_system_provider(self):
        with pytest.raises(ValueError, match="task_splitter provider must be one of"):
            TaskSplitterConfig(provider="system", model="default")

    def test_task_splitter_config_accepts_valid_llm_providers(self):
        for provider in ["claude", "opencode", "codex"]:
            ts = TaskSplitterConfig(provider=provider, model="test-model")
            assert ts.provider == provider


class TestTaskConstants:
    def test_tasks_dir_constant(self):
        assert TASKS_DIR == ".fdsx/tasks"

    def test_completed_subdir_constant(self):
        assert COMPLETED_SUBDIR == "completed"


class TestSlugify:
    def test_basic_text(self):
        assert _slugify("Hello World") == "hello-world"

    def test_lowercase_conversion(self):
        assert _slugify("IMPLEMENT Feature A") == "implement-feature-a"

    def test_special_characters_removed(self):
        assert _slugify("Task: Fix bug!") == "task-fix-bug"

    def test_multiple_spaces_collapsed(self):
        assert _slugify("Task   with   spaces") == "task-with-spaces"

    def test_long_text_truncated(self):
        long_text = "a" * 60
        result = _slugify(long_text, max_length=40)
        assert len(result) <= 40

    def test_empty_string_returns_task(self):
        assert _slugify("") == "task"

    def test_only_special_chars_returns_task(self):
        assert _slugify("!!!###") == "task"

    def test_hyphens_not_duplicated(self):
        assert _slugify("task--double") == "task-double"

    def test_leading_trailing_hyphens_stripped(self):
        result = _slugify("  leading and trailing  ")
        assert not result.startswith("-")
        assert not result.endswith("-")


class TestScanMaxTaskIndex:
    def test_returns_zero_for_empty_dir(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        assert _scan_max_task_index(tasks_dir) == 0

    def test_returns_zero_for_nonexistent_dir(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        assert _scan_max_task_index(tasks_dir) == 0

    def test_finds_max_in_tasks_dir(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-a.yaml").write_text("")
        (tasks_dir / "003-b.yaml").write_text("")
        (tasks_dir / "002-c.yaml").write_text("")
        assert _scan_max_task_index(tasks_dir) == 3

    def test_finds_max_in_completed_subdir(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-a.yaml").write_text("")
        completed_dir = tasks_dir / COMPLETED_SUBDIR
        completed_dir.mkdir()
        (completed_dir / "005-old.yaml").write_text("")
        assert _scan_max_task_index(tasks_dir) == 5

    def test_returns_max_across_both_dirs(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "003-active.yaml").write_text("")
        completed_dir = tasks_dir / COMPLETED_SUBDIR
        completed_dir.mkdir()
        (completed_dir / "007-old.yaml").write_text("")
        (completed_dir / "002-older.yaml").write_text("")
        assert _scan_max_task_index(tasks_dir) == 7

    def test_ignores_files_without_numeric_prefix(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "no-prefix.yaml").write_text("")
        (tasks_dir / "abc-task.yaml").write_text("")
        assert _scan_max_task_index(tasks_dir) == 0

    def test_ignores_non_yaml_files(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001-a.txt").write_text("")
        (tasks_dir / "002-b.json").write_text("")
        assert _scan_max_task_index(tasks_dir) == 0


class TestWriteTaskFilesIndexContinuation:
    def test_new_files_start_after_existing(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "003-existing.yaml").write_text("")

        groups = [[TaskEntry(description="New task")]]
        created = write_task_files(groups, tasks_dir)

        assert len(created) == 1
        assert created[0].name == "004-new-task.yaml"

    def test_new_files_start_after_completed_dir(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        completed_dir = tasks_dir / COMPLETED_SUBDIR
        completed_dir.mkdir()
        (completed_dir / "005-done.yaml").write_text("")

        groups = [[TaskEntry(description="Another task")]]
        created = write_task_files(groups, tasks_dir)

        assert len(created) == 1
        assert created[0].name == "006-another-task.yaml"

    def test_fresh_dir_starts_from_001(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        groups = [[TaskEntry(description="First task")]]
        created = write_task_files(groups, tasks_dir)

        assert len(created) == 1
        assert created[0].name == "001-first-task.yaml"


class TestMoveTaskToCompleted:
    def test_moves_file_to_completed_subdir(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        task_file = tasks_dir / "001-test.yaml"
        task_file.write_text("description: test\n")

        move_task_to_completed(task_file)

        assert not task_file.exists()
        assert (tasks_dir / COMPLETED_SUBDIR / "001-test.yaml").exists()

    def test_creates_completed_dir_if_absent(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        task_file = tasks_dir / "001-task.yaml"
        task_file.write_text("description: task\n")

        completed_dir = tasks_dir / COMPLETED_SUBDIR
        assert not completed_dir.exists()

        move_task_to_completed(task_file)

        assert completed_dir.exists()
        assert completed_dir.is_dir()

    def test_preserves_original_filename(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        task_file = tasks_dir / "042-my-important-task.yaml"
        task_file.write_text("description: task\n")

        move_task_to_completed(task_file)

        assert (tasks_dir / COMPLETED_SUBDIR / "042-my-important-task.yaml").exists()

    def test_raises_on_collision(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        completed_dir = tasks_dir / COMPLETED_SUBDIR
        completed_dir.mkdir()

        task_file = tasks_dir / "001-test.yaml"
        task_file.write_text("description: test\n")
        (completed_dir / "001-test.yaml").write_text("description: existing\n")

        with pytest.raises(FileExistsError, match="already exists"):
            move_task_to_completed(task_file)

        # Original file must not be removed on collision
        assert task_file.exists()

    def test_raises_on_symlinked_ancestor(self, tmp_path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        task_file = link_dir / "001-task.yaml"
        task_file.write_text("description: task\n")

        with pytest.raises(ValueError, match="symlink"):
            move_task_to_completed(task_file)

    def test_preserves_file_content(self, tmp_path):
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        task_file = tasks_dir / "001-content.yaml"
        content = "entries:\n- description: important content\n  status: completed\n"
        task_file.write_text(content)

        move_task_to_completed(task_file)

        dest = tasks_dir / COMPLETED_SUBDIR / "001-content.yaml"
        assert dest.read_text() == content

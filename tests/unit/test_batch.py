import tempfile
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from fdsx.core.batch import (
    TASKS_DIR,
    _slugify,
    split_tasks,
    split_tasks_to_groups,
    display_task_list,
    display_batch_summary,
    _parse_task_list,
    _parse_structured_tasks,
    _build_task_split_prompt,
    _extract_input_variables,
    write_task_files,
)
from fdsx.core.config import TaskSplitterConfig
from fdsx.models.flow import Flow, TaskState
from fdsx.models.task import TaskEntry


class TestSplitTasks:
    def test_split_tasks_parses_json_response(self):
        flow = Flow(
            name="Test Flow",
            description="Test flow for split tasks",
            start_at="plan",
            states={
                "plan": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.result",
                    end=True,
                )
            },
        )
        task_splitter = TaskSplitterConfig(
            provider="claude", model="claude-3-5-sonnet-20241022"
        )

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "First task"}, {"description": "Second task"}], [{"description": "Third task"}]]',
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            tasks = split_tasks("test content", flow, task_splitter)

        assert len(tasks) == 3
        assert tasks[0] == "First task"
        assert tasks[1] == "Second task"
        assert tasks[2] == "Third task"

    def test_split_tasks_fallback_to_numbered_list(self):
        """If JSON parsing fails, fall back to numbered list parsing."""
        flow = Flow(
            name="Test Flow",
            description="Test flow for fallback",
            start_at="plan",
            states={
                "plan": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.result",
                    end=True,
                )
            },
        )
        task_splitter = TaskSplitterConfig(
            provider="claude", model="claude-3-5-sonnet-20241022"
        )

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. First task\n2. Second task\n3. Third task",
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            tasks = split_tasks("test content", flow, task_splitter)

        assert len(tasks) == 3
        assert tasks[0] == "First task"
        assert tasks[1] == "Second task"
        assert tasks[2] == "Third task"

    def test_split_tasks_empty_response(self):
        flow = Flow(
            name="Test Flow",
            description="Test flow for split tasks empty",
            start_at="plan",
            states={
                "plan": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.result",
                    end=True,
                )
            },
        )
        task_splitter = TaskSplitterConfig(
            provider="claude", model="claude-3-5-sonnet-20241022"
        )

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="",
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            tasks = split_tasks("test content", flow, task_splitter)

        assert tasks == []

    def test_split_tasks_provider_failure(self):
        flow = Flow(
            name="Test Flow",
            description="Test flow for provider failure",
            start_at="plan",
            states={
                "plan": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.result",
                    end=True,
                )
            },
        )
        task_splitter = TaskSplitterConfig(
            provider="claude", model="claude-3-5-sonnet-20241022"
        )

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=1,
            stdout="",
            stderr="Provider error",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            with pytest.raises(RuntimeError, match="Task splitter failed"):
                split_tasks("test content", flow, task_splitter)


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

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            with pytest.raises(RuntimeError, match="Task splitter failed"):
                split_tasks_to_groups("test content", task_splitter)


class TestDisplayTaskList:
    def test_display_task_list_approve(self):
        tasks = ["First task", "Second task", "Third task"]

        with patch("builtins.input", return_value="y"):
            result = display_task_list(tasks)

        assert result is True

    def test_display_task_list_reject(self):
        tasks = ["First task", "Second task", "Third task"]

        with patch("builtins.input", return_value="n"):
            result = display_task_list(tasks)

        assert result is False

    def test_display_task_list_invalid_then_approve(self):
        tasks = ["First task"]

        inputs = iter(["invalid", "y"])
        with patch("builtins.input", lambda x: next(inputs)):
            result = display_task_list(tasks)

        assert result is True


class TestDisplayBatchSummary:
    def test_display_batch_summary(self, capsys):
        results = [
            {
                "task_index": 0,
                "task_description": "First task",
                "thread_id": "thread-1",
                "status": "completed",
                "error": None,
            },
            {
                "task_index": 1,
                "task_description": "Second task",
                "thread_id": "thread-2",
                "status": "failed",
                "error": "Something went wrong",
            },
        ]

        display_batch_summary(results)

        captured = capsys.readouterr()
        assert "BATCH EXECUTION SUMMARY" in captured.err
        assert "Succeeded: 1" in captured.err
        assert "Failed: 1" in captured.err


class TestParseTaskList:
    def test_parse_numbered_list(self):
        response = "1. First task\n2. Second task\n3. Third task"
        tasks = _parse_task_list(response)

        assert len(tasks) == 3
        assert tasks[0] == "First task"
        assert tasks[1] == "Second task"
        assert tasks[2] == "Third task"

    def test_parse_unnumbered_lines(self):
        response = "First task\nSecond task\nThird task"
        tasks = _parse_task_list(response)

        assert len(tasks) == 3

    def test_parse_empty_response(self):
        response = ""
        tasks = _parse_task_list(response)

        assert tasks == []

    def test_parse_with_empty_lines(self):
        response = "1. First task\n\n2. Second task\n\n"
        tasks = _parse_task_list(response)

        assert len(tasks) == 2


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
    def test_build_prompt_with_state_names_and_input_vars(self):
        prompt = _build_task_split_prompt(
            "test content", ["plan", "implement"], {"task"}
        )

        assert "test content" in prompt
        assert "plan, implement" in prompt
        assert "task" in prompt
        assert "JSON" in prompt
        assert "DEPEND on each other sequentially" in prompt

    def test_build_prompt_without_optional_params(self):
        prompt = _build_task_split_prompt("test content", None, None)

        assert "test content" in prompt
        assert "any workflow" in prompt
        assert "task" in prompt
        assert "DEPEND on each other sequentially" in prompt

    def test_build_prompt_independent_tasks_go_in_separate_groups(self):
        prompt = _build_task_split_prompt("test content", None, None)

        assert "independent tasks" in prompt.lower() or "SEPARATE groups" in prompt


class TestExtractInputVariables:
    def test_extract_from_task_states_with_prompt_template(self):
        flow = Flow(
            name="Test Flow",
            description="Test flow for extract input variables",
            start_at="plan",
            states={
                "plan": TaskState(
                    type="task",
                    provider="claude",
                    model="claude-3-5-sonnet-20241022",
                    prompt_template="Analyze {user_request} and create a plan",
                    result_path="$.plan",
                    next="implement",
                ),
                "implement": TaskState(
                    type="task",
                    provider="claude",
                    model="claude-3-5-sonnet-20241022",
                    prompt_template="Implement the plan: {plan}",
                    result_path="$.implementation",
                    end=True,
                ),
            },
        )

        input_vars = _extract_input_variables(flow)

        assert "task" in input_vars
        assert "user_request" in input_vars
        assert "plan" in input_vars

    def test_extract_returns_task_by_default(self):
        flow = Flow(
            name="Test Flow",
            description="Test flow for extract task default",
            start_at="plan",
            states={
                "plan": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.result",
                    end=True,
                ),
            },
        )

        input_vars = _extract_input_variables(flow)

        assert "task" in input_vars

    def test_extract_from_parallel_branches(self):
        from fdsx.models.flow import Branch, ParallelState

        flow = Flow(
            name="Test Flow",
            description="Test flow for extract parallel branches",
            start_at="review",
            states={
                "review": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(
                            provider="claude",
                            model="claude-3-5-sonnet-20241022",
                            prompt_template="Review {task} for quality",
                        ),
                        Branch(
                            provider="claude",
                            model="claude-3-5-sonnet-20241022",
                            prompt_template="Check {task} for security issues with {context}",
                        ),
                    ],
                    result_path="$.reviews",
                    end=True,
                ),
            },
        )

        input_vars = _extract_input_variables(flow)

        assert "task" in input_vars
        assert "context" in input_vars

    def test_extract_dotted_variable_references(self):
        flow = Flow(
            name="Test Flow",
            description="Test flow for dotted variable references",
            start_at="plan",
            states={
                "plan": TaskState(
                    type="task",
                    provider="claude",
                    model="claude-3-5-sonnet-20241022",
                    prompt_template="Analyze {review.summary} and fix {issue.description}",
                    result_path="$.result",
                    end=True,
                ),
            },
        )

        input_vars = _extract_input_variables(flow)

        assert "task" in input_vars
        assert "review" in input_vars
        assert "issue" in input_vars


class TestTaskSplitterConfigValidation:
    def test_task_splitter_config_rejects_system_provider(self):
        with pytest.raises(ValueError, match="task_splitter provider must be one of"):
            TaskSplitterConfig(provider="system", model="default")

    def test_task_splitter_config_accepts_valid_llm_providers(self):
        for provider in ["claude", "opencode", "codex"]:
            ts = TaskSplitterConfig(provider=provider, model="test-model")
            assert ts.provider == provider


class TestSplitTasksWithConfig:
    """Regression tests: split_tasks accepts TaskSplitterConfig from config (T9 migration)."""

    def test_split_tasks_accepts_task_splitter_config(self):
        """split_tasks must work with TaskSplitterConfig from load_config()."""
        flow = Flow(
            name="Config Test Flow",
            description="Test that split_tasks accepts TaskSplitterConfig",
            start_at="plan",
            states={
                "plan": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.result",
                    end=True,
                )
            },
        )
        config_splitter = TaskSplitterConfig(
            provider="claude", model="claude-sonnet-4-6"
        )

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout='[[{"description": "Task A"}, {"description": "Task B"}]]',
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            tasks = split_tasks("test content", flow, config_splitter)

        assert tasks == ["Task A", "Task B"]
        # Verify the model from TaskSplitterConfig was forwarded to the provider
        call_kwargs = mock_provider.execute.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6"


class TestTaskConstants:
    def test_tasks_dir_constant(self):
        assert TASKS_DIR == ".fdsx/tasks"


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

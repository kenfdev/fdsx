import pytest
from unittest.mock import MagicMock, patch

from fdsx.core.batch import (
    split_tasks,
    display_task_list,
    display_batch_summary,
    _parse_task_list,
    _build_task_split_prompt,
    _extract_input_variables,
)
from fdsx.core.config import TaskSplitterConfig
from fdsx.models.flow import Flow, TaskState


class TestSplitTasks:
    def test_split_tasks_parses_numbered_list(self):
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


class TestBuildTaskSplitPrompt:
    def test_build_prompt(self):
        prompt = _build_task_split_prompt(
            "test content", ["plan", "implement"], {"task"}
        )

        assert "test content" in prompt
        assert "plan, implement" in prompt
        assert "task" in prompt


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
        config_splitter = TaskSplitterConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="1. Task A\n2. Task B",
            stderr="",
        )

        with patch("fdsx.core.batch.get_provider", return_value=mock_provider):
            tasks = split_tasks("test content", flow, config_splitter)

        assert tasks == ["Task A", "Task B"]
        # Verify the model from TaskSplitterConfig was forwarded to the provider
        call_kwargs = mock_provider.execute.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6"

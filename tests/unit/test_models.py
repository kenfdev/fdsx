import pytest

from fdsx.models.flow import (
    Branch,
    ChoiceState,
    ChoiceRule,
    Flow,
    ParallelState,
    PassState,
    TaskState,
    WaitState,
)


class TestPydanticModels:
    def test_valid_task_state_system(self):
        state = TaskState(
            type="task",
            provider="system",
            command="echo test",
            result_path="$.result",
        )
        assert state.type == "task"
        assert state.provider == "system"
        assert state.command == "echo test"

    def test_valid_task_state_claude(self):
        state = TaskState(
            type="task",
            provider="claude",
            model="opus",
            prompt_template="Hello {name}",
            result_path="$.result",
        )
        assert state.type == "task"
        assert state.provider == "claude"
        assert state.model == "opus"

    def test_valid_choice_state(self):
        state = ChoiceState(
            type="choice",
            choices=[
                ChoiceRule(
                    variable="$.status",
                    operator="equals",
                    value="ready",
                    next="proceed",
                )
            ],
            default="error",
        )
        assert state.type == "choice"
        assert len(state.choices) == 1

    def test_valid_parallel_state(self):
        state = ParallelState(
            type="parallel",
            branches=[],
            result_path="$.results",
        )
        assert state.type == "parallel"

    def test_valid_pass_state(self):
        state = PassState(
            type="pass",
            parameters={"$.output": "$.input"},
        )
        assert state.type == "pass"

    def test_valid_wait_state(self):
        state = WaitState(
            type="wait",
            mode="prompt",
            message="Continue?",
            choices=["yes", "no"],
            result_path="$.choice",
        )
        assert state.type == "wait"

    def test_valid_flow(self):
        flow = Flow(
            name="Test Flow",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo test",
                    result_path="$.result",
                    end=True,
                )
            },
        )
        assert flow.name == "Test Flow"
        assert flow.start_at == "start"

    def test_validation_missing_start_at(self):
        with pytest.raises(ValueError, match="start_at"):
            Flow(
                name="Test Flow",
                start_at="nonexistent",
                states={
                    "start": TaskState(
                        type="task",
                        provider="system",
                        command="echo test",
                        result_path="$.result",
                        end=True,
                    )
                },
            )

    def test_validation_invalid_next_reference(self):
        with pytest.raises(ValueError, match="does not exist"):
            Flow(
                name="Test Flow",
                start_at="start",
                states={
                    "start": TaskState(
                        type="task",
                        provider="system",
                        command="echo test",
                        result_path="$.result",
                        next="nonexistent",
                    )
                },
            )

    def test_validation_prompt_template_and_file_mutual_exclusive(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            TaskState(
                type="task",
                provider="claude",
                model="opus",
                prompt_template="Hello",
                prompt_file="hello.txt",
                result_path="$.result",
            )

    def test_validation_next_and_end_mutual_exclusive(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            TaskState(
                type="task",
                provider="system",
                command="echo test",
                result_path="$.result",
                next="next_state",
                end=True,
            )

    def test_validation_system_forbids_prompt_template(self):
        with pytest.raises(ValueError, match="forbids prompt_template"):
            TaskState(
                type="task",
                provider="system",
                prompt_template="Hello",
                result_path="$.result",
            )

    def test_validation_system_requires_command(self):
        with pytest.raises(ValueError, match="requires command"):
            TaskState(
                type="task",
                provider="system",
                result_path="$.result",
            )

    def test_validation_claude_requires_prompt(self):
        with pytest.raises(ValueError, match="requires prompt_template"):
            TaskState(
                type="task",
                provider="claude",
                model="opus",
                result_path="$.result",
            )

    def test_validation_claude_requires_model(self):
        with pytest.raises(ValueError, match="requires model"):
            TaskState(
                type="task",
                provider="claude",
                prompt_template="Hello",
                result_path="$.result",
            )

    def test_validation_branch_requires_model_for_non_system(self):
        with pytest.raises(ValueError, match="requires model"):
            Branch(
                provider="claude",
                prompt_template="Hello",
            )

    def test_validation_termination_required(self):
        with pytest.raises(ValueError, match="termination"):
            Flow(
                name="Test Flow",
                start_at="start",
                states={
                    "start": TaskState(
                        type="task",
                        provider="system",
                        command="echo test",
                        result_path="$.result",
                        next="middle",
                    ),
                    "middle": TaskState(
                        type="task",
                        provider="system",
                        command="echo test",
                        result_path="$.result2",
                        next="start",
                    ),
                },
            )

    def test_discriminated_union_task(self):
        data = {
            "type": "task",
            "provider": "system",
            "command": "echo test",
            "result_path": "$.result",
        }
        state = TaskState(**data)
        assert state.type == "task"

    def test_discriminated_union_choice(self):
        data = {
            "type": "choice",
            "choices": [
                {
                    "variable": "$.status",
                    "operator": "equals",
                    "value": "ready",
                    "next": "proceed",
                }
            ],
        }
        state = ChoiceState(**data)
        assert state.type == "choice"

    def test_choice_rule_invalid_operator_rejected(self):
        """F1 regression: ChoiceRule.operator must be one of the valid literals."""
        with pytest.raises(Exception):
            ChoiceRule(
                variable="$.x",
                operator="typo",  # invalid operator
                value="a",
                next="b",
            )

    def test_choice_rule_valid_operators_accepted(self):
        """F1: all documented operators must be accepted."""
        valid_operators = [
            "equals",
            "not_equals",
            "greater_than",
            "less_than",
            "contains",
        ]
        for op in valid_operators:
            rule = ChoiceRule(variable="$.x", operator=op, value="a", next="b")
            assert rule.operator == op

    def test_discriminated_union_dispatch_through_flow_parsing(self):
        """Test that all 5 state types dispatch to the correct Pydantic model
        through Flow's discriminated union using raw dicts (not pre-typed instances)."""
        flow = Flow(
            name="All State Types",
            start_at="task_step",
            states={
                "task_step": {
                    "type": "task",
                    "provider": "system",
                    "command": "echo hello",
                    "result_path": "$.task_out",
                    "next": "choice_step",
                },
                "choice_step": {
                    "type": "choice",
                    "choices": [
                        {
                            "variable": "$.task_out",
                            "operator": "equals",
                            "value": "hello",
                            "next": "parallel_step",
                        }
                    ],
                    "default": "pass_step",
                },
                "parallel_step": {
                    "type": "parallel",
                    "branches": [
                        {"provider": "system", "command": "echo b"},
                    ],
                    "result_path": "$.parallel_out",
                    "next": "pass_step",
                },
                "pass_step": {
                    "type": "pass",
                    "parameters": {"$.forwarded": "$.task_out"},
                    "next": "wait_step",
                },
                "wait_step": {
                    "type": "wait",
                    "mode": "prompt",
                    "message": "Continue?",
                    "choices": ["yes", "no"],
                    "result_path": "$.user_choice",
                    "end": True,
                },
            },
        )

        assert isinstance(flow.states["task_step"], TaskState)
        assert isinstance(flow.states["choice_step"], ChoiceState)
        assert isinstance(flow.states["parallel_step"], ParallelState)
        assert isinstance(flow.states["pass_step"], PassState)
        assert isinstance(flow.states["wait_step"], WaitState)

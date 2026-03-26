import pytest
from pydantic import ValidationError

from fdsx.models.flow import (
    Branch,
    ChoiceState,
    ChoiceRule,
    ExtractRule,
    Flow,
    HookEntry,
    LLMClassifyFallback,
    ParallelState,
    PassState,
    TaskState,
    WaitState,
    WebhookConfig,
)


class TestPydanticModels:
    def test_validation_missing_start_at(self):
        with pytest.raises(ValueError, match="start_at"):
            Flow(
                name="Test Flow",
                description="Test flow with missing start_at",
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
                description="Test flow with invalid next reference",
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
                description="Test flow with no termination",
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
            description="Test flow with all state types",
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


class TestLLMClassifyFallbackValidation:
    # F4 regression: system provider must be rejected at model construction time
    def test_system_provider_rejected(self):
        """F4: LLMClassifyFallback with provider='system' must raise ValidationError."""
        with pytest.raises(ValidationError, match="system"):
            LLMClassifyFallback(
                type="llm_classify",
                provider="system",
                prompt="Classify: {output}",
            )


class TestExtractPathValidation:
    """CQ-1, CQ-2: Regression tests for extract path validation."""

    def test_task_state_rejects_overlapping_extract_result_path(self):
        """CQ-1: TaskState with overlapping result_path and extract.result_path must raise."""
        with pytest.raises(ValidationError, match="must not overlap"):
            TaskState(
                type="task",
                provider="system",
                command="echo hi",
                result_path="$.result",
                extract=ExtractRule(
                    strategy=["keyword"], pattern="A|B", result_path="$.result"
                ),
            )

    def test_task_state_rejects_ancestor_descendant_extract_path(self):
        """CQ-1: TaskState with ancestor/descendant paths must raise."""
        with pytest.raises(ValidationError, match="must not overlap"):
            TaskState(
                type="task",
                provider="system",
                command="echo hi",
                result_path="$.result",
                extract=ExtractRule(
                    strategy=["keyword"],
                    pattern="A|B",
                    result_path="$.result.decision",
                ),
            )

    def test_task_state_rejects_bracket_notation_overlap(self):
        """Regression: $.result vs $.result[0] must be rejected as overlapping."""
        with pytest.raises(ValidationError, match="must not overlap"):
            TaskState(
                type="task",
                provider="system",
                command="echo hi",
                result_path="$.result",
                extract=ExtractRule(
                    strategy=["keyword"], pattern="A|B", result_path="$.result[0]"
                ),
            )

    def test_branch_rejects_extract_path_reserved_output(self):
        """CQ-2: Branch extract.result_path with reserved key 'output' must raise."""
        with pytest.raises(ValidationError, match="reserved key"):
            Branch(
                provider="system",
                command="echo hi",
                extract=ExtractRule(
                    strategy=["keyword"], pattern="A|B", result_path="$.output"
                ),
            )

    def test_branch_rejects_extract_path_reserved_exit_code(self):
        """CQ-2: Branch extract.result_path with reserved key 'exit_code' must raise."""
        with pytest.raises(ValidationError, match="reserved key"):
            Branch(
                provider="system",
                command="echo hi",
                extract=ExtractRule(
                    strategy=["keyword"], pattern="A|B", result_path="$.exit_code"
                ),
            )

    def test_branch_rejects_extract_path_reserved_error_descendant(self):
        """CQ-2: Branch extract.result_path with reserved key 'error' must raise."""
        with pytest.raises(ValidationError, match="reserved key"):
            Branch(
                provider="system",
                command="echo hi",
                extract=ExtractRule(
                    strategy=["keyword"],
                    pattern="A|B",
                    result_path="$.error.detail",
                ),
            )

    def test_branch_rejects_extract_path_reserved_error_bracket(self):
        """Regression: bracket notation must not bypass reserved key check."""
        with pytest.raises(ValidationError, match="reserved key"):
            Branch(
                provider="system",
                command="echo hi",
                extract=ExtractRule(
                    strategy=["keyword"], pattern="A|B", result_path="$.error[0]"
                ),
            )

    def test_branch_rejects_extract_path_reserved_output_bracket(self):
        """Regression: bracket notation must not bypass reserved key check."""
        with pytest.raises(ValidationError, match="reserved key"):
            Branch(
                provider="system",
                command="echo hi",
                extract=ExtractRule(
                    strategy=["keyword"], pattern="A|B", result_path="$.output[0]"
                ),
            )


class TestWaitStateValidation:
    """CQ-2: Regression tests for WaitState.choices validation."""

    def test_wait_state_rejects_empty_choices(self):
        """CQ-2: WaitState with empty choices list must raise ValidationError."""
        with pytest.raises(ValidationError, match="too_short|at least 1"):
            WaitState(
                type="wait",
                mode="prompt",
                message="Continue?",
                choices=[],
                result_path="$.choice",
            )


class TestWebhookConfigValidation:
    """SEC-3: Regression tests for WebhookConfig URL validation."""

    def test_webhook_rejects_http_non_localhost(self):
        """SEC-3: Webhook URL with http:// (non-localhost) must raise ValidationError."""
        with pytest.raises(ValidationError, match="HTTPS"):
            WebhookConfig(
                url="http://evil.com/hook",
                template="Test",
            )


class TestFlowDescriptionField:
    """T8: Tests for the new required description field in Flow model."""

    def test_flow_requires_description(self):
        """T8: Flow model requires description field."""
        with pytest.raises(ValidationError, match="description"):
            Flow(
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

    def test_flow_rejects_empty_description(self):
        """T8: Flow model must reject empty string description (min_length=1)."""
        with pytest.raises(ValidationError, match="description"):
            Flow(
                name="Test Flow",
                description="",
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


class TestTaskSplitterRemoval:
    """T9: Tests for task_splitter removal from Flow model."""

    def test_task_splitter_rejected_in_constructor(self):
        """T9: task_splitter field must be rejected with migration error."""
        with pytest.raises(ValidationError, match="task_splitter"):
            Flow(
                name="Test Flow",
                description="Test flow",
                start_at="start",
                task_splitter={
                    "provider": "claude",
                    "model": "claude-3-5-sonnet-20241022",
                },
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

    def test_task_splitter_null_also_rejected(self):
        """T9: task_splitter: null must also be rejected with migration error."""
        with pytest.raises(ValidationError, match="task_splitter"):
            Flow(
                name="Test Flow",
                description="Test flow",
                start_at="start",
                task_splitter=None,
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

    def test_task_splitter_migration_error_message(self):
        """T9: Error message should guide users to config file."""
        with pytest.raises(ValidationError, match="config"):
            Flow(
                name="Test Flow",
                description="Test flow",
                start_at="start",
                task_splitter={"provider": "claude", "model": "opus"},
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


class TestHookEntryAndHookConfig:
    """T016: Tests for HookEntry and HookConfig models."""

    def test_hook_entry_rejects_empty_command(self):
        """T016: HookEntry.command must not be empty."""
        with pytest.raises(ValidationError):
            HookEntry(command="")

    def test_hook_entry_rejects_invalid_on_failure(self):
        """T016: HookEntry.on_failure must be 'abort' or 'warn'."""
        with pytest.raises(ValidationError):
            HookEntry(command="echo hello", on_failure="ignore")

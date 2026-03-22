import pytest
from pydantic import ValidationError

from fdsx.models.flow import (
    Branch,
    ChoiceState,
    ChoiceRule,
    ExtractRule,
    Flow,
    HookConfig,
    HookEntry,
    LLMClassifyFallback,
    ParallelState,
    PassState,
    TaskState,
    WaitState,
    WebhookConfig,
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
            description="Test flow for unit testing",
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

    def test_non_system_provider_accepted(self):
        """F4: LLMClassifyFallback with a non-system provider must be accepted."""
        fb = LLMClassifyFallback(
            type="llm_classify",
            provider="claude",
            prompt="Classify: {output}",
        )
        assert fb.provider == "claude"

    def test_opencode_provider_accepted(self):
        """F4: opencode is a valid LLM provider for classify fallback."""
        fb = LLMClassifyFallback(
            type="llm_classify",
            provider="opencode",
            prompt="Classify: {output}",
        )
        assert fb.provider == "opencode"


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

    def test_task_state_allows_non_overlapping_extract_path(self):
        """CQ-1: TaskState with non-overlapping paths must be accepted."""
        state = TaskState(
            type="task",
            provider="system",
            command="echo hi",
            result_path="$.raw_output",
            extract=ExtractRule(
                strategy=["keyword"], pattern="A|B", result_path="$.decision"
            ),
        )
        assert state.result_path == "$.raw_output"

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

    def test_branch_allows_extract_path_non_reserved(self):
        """CQ-2: Branch extract.result_path with non-reserved key must be accepted."""
        branch = Branch(
            provider="system",
            command="echo hi",
            extract=ExtractRule(
                strategy=["keyword"], pattern="A|B", result_path="$.decision"
            ),
        )
        assert branch.extract.result_path == "$.decision"

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

    def test_wait_state_accepts_single_choice(self):
        """WaitState with one choice must be accepted."""
        state = WaitState(
            type="wait",
            mode="prompt",
            message="Continue?",
            choices=["yes"],
            result_path="$.choice",
        )
        assert state.choices == ["yes"]

    def test_wait_state_accepts_multiple_choices(self):
        """WaitState with multiple choices must be accepted."""
        state = WaitState(
            type="wait",
            mode="prompt",
            message="Choose:",
            choices=["approve", "reject", "retry"],
            result_path="$.choice",
        )
        assert len(state.choices) == 3


class TestWebhookConfigValidation:
    """SEC-3: Regression tests for WebhookConfig URL validation."""

    def test_webhook_rejects_http_non_localhost(self):
        """SEC-3: Webhook URL with http:// (non-localhost) must raise ValidationError."""
        with pytest.raises(ValidationError, match="HTTPS"):
            WebhookConfig(
                url="http://evil.com/hook",
                template="Test",
            )

    def test_webhook_accepts_https(self):
        """SEC-3: Webhook URL with https:// must be accepted."""
        config = WebhookConfig(
            url="https://hooks.example.com/services/TOKEN",
            template="Test message",
        )
        assert config.url == "https://hooks.example.com/services/TOKEN"

    def test_webhook_accepts_http_localhost(self):
        """SEC-3: Webhook URL with http://localhost must be accepted for testing."""
        config = WebhookConfig(
            url="http://localhost:8080/webhook",
            template="Test",
        )
        assert config.url == "http://localhost:8080/webhook"

    def test_webhook_accepts_http_127_0_0_1(self):
        """SEC-3: Webhook URL with http://127.0.0.1 must be accepted for testing."""
        config = WebhookConfig(
            url="http://127.0.0.1:9000/webhook",
            template="Test",
        )
        assert config.url == "http://127.0.0.1:9000/webhook"


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

    def test_flow_accepts_valid_description(self):
        """T8: Flow model accepts valid description."""
        flow = Flow(
            name="Test Flow",
            description="A test flow with description",
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
        assert flow.description == "A test flow with description"

    def test_flow_description_can_be_multiline(self):
        """T8: Flow description can be a multiline string."""
        flow = Flow(
            name="Test Flow",
            description="Line 1\nLine 2\nLine 3",
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
        assert "Line 1" in flow.description

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

    def test_hook_entry_defaults_on_failure_to_warn(self):
        """T016: HookEntry.on_failure defaults to 'warn'."""
        entry = HookEntry(command="echo hello")
        assert entry.command == "echo hello"
        assert entry.on_failure == "warn"

    def test_hook_entry_accepts_abort(self):
        """T016: HookEntry.on_failure accepts 'abort'."""
        entry = HookEntry(command="./check.sh", on_failure="abort")
        assert entry.on_failure == "abort"

    def test_hook_entry_rejects_empty_command(self):
        """T016: HookEntry.command must not be empty."""
        with pytest.raises(ValidationError):
            HookEntry(command="")

    def test_hook_entry_rejects_invalid_on_failure(self):
        """T016: HookEntry.on_failure must be 'abort' or 'warn'."""
        with pytest.raises(ValidationError):
            HookEntry(command="echo hello", on_failure="ignore")

    def test_hook_config_defaults_to_empty_lists(self):
        """T016: HookConfig.on_start and on_complete default to empty lists."""
        config = HookConfig()
        assert config.on_start == []
        assert config.on_complete == []

    def test_hook_config_accepts_entries(self):
        """T016: HookConfig accepts HookEntry objects in both lists."""
        config = HookConfig(
            on_start=[HookEntry(command="echo start")],
            on_complete=[HookEntry(command="echo done", on_failure="abort")],
        )
        assert len(config.on_start) == 1
        assert config.on_start[0].command == "echo start"
        assert len(config.on_complete) == 1
        assert config.on_complete[0].on_failure == "abort"


class TestHooksFieldOnStates:
    """T017: Tests for hooks field on state types and Flow."""

    def _base_task_state(self, **kwargs) -> TaskState:
        return TaskState(
            type="task",
            provider="system",
            command="echo test",
            result_path="$.result",
            end=True,
            **kwargs,
        )

    def test_task_state_hooks_defaults_to_none(self):
        """T017: TaskState.hooks is None when not specified."""
        assert self._base_task_state().hooks is None

    def test_task_state_accepts_hooks(self):
        """T017: TaskState accepts a HookConfig."""
        state = self._base_task_state(
            hooks=HookConfig(on_start=[HookEntry(command="echo pre")])
        )
        assert state.hooks is not None
        assert state.hooks.on_start[0].command == "echo pre"

    def test_choice_state_hooks_defaults_to_none(self):
        """T017: ChoiceState.hooks is None when not specified."""
        state = ChoiceState(
            type="choice",
            choices=[ChoiceRule(variable="$.x", operator="equals", value="a", next="b")],
        )
        assert state.hooks is None

    def test_choice_state_accepts_hooks(self):
        """T017: ChoiceState accepts a HookConfig."""
        state = ChoiceState(
            type="choice",
            choices=[ChoiceRule(variable="$.x", operator="equals", value="a", next="b")],
            hooks=HookConfig(on_complete=[HookEntry(command="echo done")]),
        )
        assert state.hooks is not None

    def test_parallel_state_hooks_defaults_to_none(self):
        """T017: ParallelState.hooks is None when not specified."""
        state = ParallelState(type="parallel", branches=[], result_path="$.r", end=True)
        assert state.hooks is None

    def test_parallel_state_accepts_hooks(self):
        """T017: ParallelState accepts a HookConfig."""
        state = ParallelState(
            type="parallel",
            branches=[],
            result_path="$.r",
            end=True,
            hooks=HookConfig(on_start=[HookEntry(command="init.sh", on_failure="abort")]),
        )
        assert state.hooks is not None

    def test_pass_state_hooks_defaults_to_none(self):
        """T017: PassState.hooks is None when not specified."""
        state = PassState(type="pass", end=True)
        assert state.hooks is None

    def test_pass_state_accepts_hooks(self):
        """T017: PassState accepts a HookConfig."""
        state = PassState(
            type="pass",
            end=True,
            hooks=HookConfig(on_complete=[HookEntry(command="cleanup.sh")]),
        )
        assert state.hooks is not None

    def test_wait_state_hooks_defaults_to_none(self):
        """T017: WaitState.hooks is None when not specified."""
        state = WaitState(
            type="wait",
            mode="prompt",
            message="Go?",
            choices=["yes"],
            result_path="$.c",
            end=True,
        )
        assert state.hooks is None

    def test_wait_state_accepts_hooks(self):
        """T017: WaitState accepts a HookConfig."""
        state = WaitState(
            type="wait",
            mode="prompt",
            message="Go?",
            choices=["yes"],
            result_path="$.c",
            end=True,
            hooks=HookConfig(on_start=[HookEntry(command="notify.sh")]),
        )
        assert state.hooks is not None

    def test_flow_hooks_defaults_to_none(self):
        """T017: Flow.hooks is None when not specified."""
        flow = Flow(
            name="Test",
            description="Test flow",
            start_at="s",
            states={"s": self._base_task_state()},
        )
        assert flow.hooks is None

    def test_flow_accepts_hooks(self):
        """T017: Flow accepts a HookConfig at flow level."""
        flow = Flow(
            name="Test",
            description="Test flow",
            start_at="s",
            states={"s": self._base_task_state()},
            hooks=HookConfig(
                on_start=[HookEntry(command="setup.sh")],
                on_complete=[HookEntry(command="teardown.sh")],
            ),
        )
        assert flow.hooks is not None
        assert flow.hooks.on_start[0].command == "setup.sh"
        assert flow.hooks.on_complete[0].command == "teardown.sh"


class TestFlowModelExtension:
    """T012-T013: Tests for Flow.providers, TaskState.provider_options, Branch.provider_options."""

    def _make_base_flow(self, **kwargs) -> Flow:
        return Flow(
            name="Test Flow",
            description="Flow model extension test",
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
            **kwargs,
        )

    # T013: Flow.providers field

    def test_flow_providers_defaults_to_none(self):
        """T013: Flow.providers is None when not specified."""
        flow = self._make_base_flow()
        assert flow.providers is None

    def test_flow_providers_accepts_dict(self):
        """T013: Flow.providers accepts a dict of provider name -> options."""
        flow = self._make_base_flow(
            providers={
                "claude": {"model": "claude-opus-4-5", "temperature": 0.7},
                "opencode": {"model": "gpt-4o"},
            }
        )
        assert flow.providers is not None
        assert flow.providers["claude"]["model"] == "claude-opus-4-5"
        assert flow.providers["opencode"]["model"] == "gpt-4o"

    def test_flow_providers_accepts_unknown_provider_names(self):
        """T013: Unknown provider names must be accepted at parse time."""
        flow = self._make_base_flow(
            providers={"future-provider": {"endpoint": "https://api.example.com"}}
        )
        assert flow.providers is not None
        assert "future-provider" in flow.providers

    def test_flow_providers_accepts_empty_dict(self):
        """T013: Flow.providers accepts an empty dict."""
        flow = self._make_base_flow(providers={})
        assert flow.providers == {}

    # T012: TaskState.provider_options field

    def test_task_state_provider_options_defaults_to_none(self):
        """T012: TaskState.provider_options is None when not specified."""
        state = TaskState(
            type="task",
            provider="system",
            command="echo test",
            result_path="$.result",
        )
        assert state.provider_options is None

    def test_task_state_provider_options_accepts_dict(self):
        """T012: TaskState.provider_options accepts arbitrary key-value pairs."""
        state = TaskState(
            type="task",
            provider="claude",
            model="opus",
            prompt_template="Hello",
            result_path="$.result",
            provider_options={"temperature": 0.5, "max_tokens": 1000},
        )
        assert state.provider_options is not None
        assert state.provider_options["temperature"] == 0.5
        assert state.provider_options["max_tokens"] == 1000

    # T012: Branch.provider_options field

    def test_branch_provider_options_defaults_to_none(self):
        """T012: Branch.provider_options is None when not specified."""
        branch = Branch(
            provider="system",
            command="echo test",
        )
        assert branch.provider_options is None

    def test_branch_provider_options_accepts_dict(self):
        """T012: Branch.provider_options accepts arbitrary key-value pairs."""
        branch = Branch(
            provider="system",
            command="echo test",
            provider_options={"timeout_override": 30, "retry_delay": 1.5},
        )
        assert branch.provider_options is not None
        assert branch.provider_options["timeout_override"] == 30

    def test_flow_with_all_extension_fields(self):
        """T012-T013: Flow with providers + TaskState with provider_options round-trips correctly."""
        flow = Flow(
            name="Extended Flow",
            description="Flow with all extension fields",
            start_at="start",
            providers={"claude": {"model": "claude-opus-4-5"}},
            states={
                "start": TaskState(
                    type="task",
                    provider="claude",
                    model="opus",
                    prompt_template="Hello",
                    result_path="$.result",
                    provider_options={"temperature": 0.0},
                    end=True,
                )
            },
        )
        assert flow.providers == {"claude": {"model": "claude-opus-4-5"}}
        task = flow.states["start"]
        assert isinstance(task, TaskState)
        assert task.provider_options == {"temperature": 0.0}

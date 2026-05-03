from fdsx.core.batch import _build_task_split_prompt


class TestSingleTaskPromptDirective:
    def test_single_task_true_prepends_critical_directive(self):
        prompt = _build_task_split_prompt("test content", None, None, single_task=True)

        assert "CRITICAL" in prompt
        assert "exactly ONE group" in prompt
        assert "exactly ONE task object" in prompt
        assert "Do NOT split into multiple groups" in prompt

    def test_single_task_false_has_no_critical_directive(self):
        prompt = _build_task_split_prompt("test content", None, None, single_task=False)

        assert "CRITICAL" not in prompt
        assert "exactly ONE group" not in prompt

    def test_single_task_default_is_false(self):
        prompt_without_param = _build_task_split_prompt("test content", None, None)
        prompt_with_false = _build_task_split_prompt(
            "test content", None, None, single_task=False
        )

        assert prompt_without_param == prompt_with_false
        assert "CRITICAL" not in prompt_without_param

    def test_single_task_directive_comes_before_workflow_description(self):
        prompt = _build_task_split_prompt(
            "test content", ["plan", "implement"], {"task"}, single_task=True
        )

        critical_pos = prompt.index("CRITICAL")
        workflow_pos = prompt.index("The workflow has these states")
        assert critical_pos < workflow_pos

    def test_single_task_true_with_extra_instructions(self):
        prompt = _build_task_split_prompt(
            "test content",
            None,
            None,
            extra_instructions="Focus on security",
            single_task=True,
        )

        assert "CRITICAL" in prompt
        assert "Focus on security" in prompt
        assert "ADDITIONAL INSTRUCTIONS" in prompt


class TestNewPromptRules:
    def test_prompt_contains_pr_sized_rule(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "PR-sized" in prompt

    def test_prompt_contains_one_task_per_user_story_rule(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "one task per user story" in prompt or "one user story" in prompt

    def test_prompt_contains_borderline_sizing_rule(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "sizing is borderline" in prompt or "borderline" in prompt

    def test_prompt_contains_single_file_default_rule(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "Single-file default" in prompt

    def test_prompt_contains_non_overlapping_gate(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "non-overlapping" in prompt

    def test_prompt_contains_cross_cutting_rule(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "cross-cutting" in prompt

    def test_prompt_contains_never_split_story_rule(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "single user story is never split" in prompt or "never split" in prompt

    def test_prompt_contains_spec_alone_rule(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "spec alone" in prompt or "input alone" in prompt

    def test_prompt_contains_auto_collapse_rule(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "collapse" in prompt

    def test_prompt_contains_proposed_partition_preamble(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "PROPOSED PARTITION:" in prompt

    def test_prompt_contains_independence_rationale_field(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "independence_rationale" in prompt

    def test_prompt_contains_collapse_reason_field(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "collapse_reason" in prompt

    def test_prompt_contains_user_stories_input_shape(self):
        prompt = _build_task_split_prompt("content", None, None)
        assert "User Stories" in prompt

    def test_single_task_true_does_not_contain_proposed_partition(self):
        prompt = _build_task_split_prompt("content", None, None, single_task=True)
        assert "PROPOSED PARTITION:" not in prompt

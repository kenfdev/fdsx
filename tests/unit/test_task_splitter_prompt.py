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

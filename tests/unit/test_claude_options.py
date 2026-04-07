"""Unit tests for ClaudeOptions system_prompt and append_system_prompt fields."""

from fdsx.providers.claude import ClaudeOptions


class TestClaudeOptionsSystemPromptFlags:
    """Verify to_cli_flags() emits --system-prompt and --append-system-prompt correctly."""

    def test_system_prompt_flag_emitted(self):
        """to_cli_flags() with system_prompt set emits --system-prompt."""
        options = ClaudeOptions(system_prompt="You are a helpful assistant.")
        flags = options.to_cli_flags()
        assert "--system-prompt" in flags
        idx = flags.index("--system-prompt")
        assert flags[idx + 1] == "You are a helpful assistant."

    def test_append_system_prompt_flag_emitted(self):
        """to_cli_flags() with append_system_prompt set emits --append-system-prompt."""
        options = ClaudeOptions(append_system_prompt="Additional context here.")
        flags = options.to_cli_flags()
        assert "--append-system-prompt" in flags
        idx = flags.index("--append-system-prompt")
        assert flags[idx + 1] == "Additional context here."

    def test_neither_set_no_system_prompt_flags(self):
        """to_cli_flags() with neither field set emits no system-prompt flags."""
        options = ClaudeOptions()
        flags = options.to_cli_flags()
        assert "--system-prompt" not in flags
        assert "--append-system-prompt" not in flags

    def test_both_fields_set_to_cli_flags_still_works(self):
        """to_cli_flags() works even when both fields are set (mutex is elsewhere)."""
        options = ClaudeOptions(
            system_prompt="Base prompt.",
            append_system_prompt="Appended text.",
        )
        flags = options.to_cli_flags()
        assert "--system-prompt" in flags
        assert "--append-system-prompt" in flags

    def test_permission_mode_still_works_with_system_prompt(self):
        """permission_mode and system_prompt can coexist in to_cli_flags()."""
        options = ClaudeOptions(
            permission_mode="bypassPermissions",
            system_prompt="You are a helpful assistant.",
        )
        flags = options.to_cli_flags()
        assert "--permission-mode" in flags
        assert "--system-prompt" in flags

"""Unit tests for _merge_provider_options() in compiler (T016)."""

import pytest

from fdsx.core.compiler import _merge_provider_options
from fdsx.core.config import FdsxConfig, ProviderConfigs
from fdsx.models.flow import Flow, TaskState
from fdsx.providers.claude import ClaudeOptions
from fdsx.providers.codex import CodexOptions


def _make_simple_flow(
    providers: dict | None = None,
    task_provider_options: dict | None = None,
) -> Flow:
    """Build a minimal single-task flow for testing."""
    return Flow(
        name="test",
        description="Test flow",
        start_at="step1",
        states={
            "step1": TaskState(
                type="task",
                provider="claude",
                model="claude-sonnet",
                prompt_template="test prompt",
                result_path="$.result",
                end=True,
                provider_options=task_provider_options,
            )
        },
        providers=providers,
    )


def _make_config(claude_options: dict | None = None) -> FdsxConfig:
    """Build an FdsxConfig with optional claude provider options."""
    if claude_options is None:
        return FdsxConfig()
    return FdsxConfig(
        providers=ProviderConfigs(
            claude=ClaudeOptions.model_validate(claude_options),
        )
    )


class TestMergeProviderOptionsConfigOnly:
    """Config-level options with no workflow or task overrides."""

    def test_config_only_returns_config_options(self):
        """Config-level options are returned when no workflow/task options set."""
        config = _make_config({"permission_mode": "bypassPermissions"})
        flow = _make_simple_flow()

        result = _merge_provider_options(config, flow, "claude", None)

        assert result is not None
        assert result["permission_mode"] == "bypassPermissions"

    def test_config_defaults_excluded(self):
        """ClaudeOptions defaults (False/[]) are not included in the merge result
        so they cannot override workflow or task-level settings."""
        config = _make_config({"permission_mode": "acceptEdits"})
        flow = _make_simple_flow()

        result = _merge_provider_options(config, flow, "claude", None)

        assert result is not None
        # Defaults (dangerously_skip_permissions=False, allowed_tools=[]) must not appear
        assert "dangerously_skip_permissions" not in result
        assert "allowed_tools" not in result

    def test_no_config_providers_returns_none(self):
        """When config has no providers section, returns None."""
        config = FdsxConfig()  # no providers
        flow = _make_simple_flow()

        result = _merge_provider_options(config, flow, "claude", None)

        assert result is None

    def test_none_config_returns_none(self):
        """When config is None, returns None (no options from any level)."""
        flow = _make_simple_flow()

        result = _merge_provider_options(None, flow, "claude", None)

        assert result is None


class TestMergeProviderOptionsWorkflowOverrides:
    """Workflow-level options override config-level options."""

    def test_workflow_overrides_config(self):
        """Workflow-level options override config-level options for same key."""
        config = _make_config({"permission_mode": "acceptEdits"})
        flow = _make_simple_flow(providers={"claude": {"permission_mode": "bypassPermissions"}})

        result = _merge_provider_options(config, flow, "claude", None)

        assert result is not None
        assert result["permission_mode"] == "bypassPermissions"

    def test_workflow_only_no_config(self):
        """Workflow-level options are used even when config has no providers."""
        config = FdsxConfig()
        flow = _make_simple_flow(providers={"claude": {"dangerously_skip_permissions": True}})

        result = _merge_provider_options(config, flow, "claude", None)

        assert result is not None
        assert result["dangerously_skip_permissions"] is True

    def test_workflow_adds_to_config(self):
        """Workflow can set different keys from config (both keys appear in result)."""
        config = _make_config({"permission_mode": "acceptEdits"})
        flow = _make_simple_flow(providers={"claude": {"dangerously_skip_permissions": True}})

        result = _merge_provider_options(config, flow, "claude", None)

        assert result is not None
        assert result["permission_mode"] == "acceptEdits"
        assert result["dangerously_skip_permissions"] is True

    def test_no_workflow_providers_key_means_no_override(self):
        """When flow.providers is None, config options pass through unchanged."""
        config = _make_config({"permission_mode": "plan"})
        flow = _make_simple_flow(providers=None)

        result = _merge_provider_options(config, flow, "claude", None)

        assert result is not None
        assert result["permission_mode"] == "plan"

    def test_workflow_providers_missing_provider_key(self):
        """When flow.providers exists but not for this provider, config wins."""
        config = _make_config({"permission_mode": "plan"})
        flow = _make_simple_flow(providers={"codex": {"sandbox": "read-only"}})

        result = _merge_provider_options(config, flow, "claude", None)

        assert result is not None
        assert result["permission_mode"] == "plan"
        assert "sandbox" not in result


class TestMergeProviderOptionsTaskOverrides:
    """Task-level options override workflow and config options."""

    def test_task_overrides_workflow(self):
        """Task-level options override workflow-level options for same key."""
        config = FdsxConfig()
        flow = _make_simple_flow(providers={"claude": {"permission_mode": "acceptEdits"}})
        task_opts = {"permission_mode": "bypassPermissions"}

        result = _merge_provider_options(config, flow, "claude", task_opts)

        assert result is not None
        assert result["permission_mode"] == "bypassPermissions"

    def test_task_overrides_config(self):
        """Task-level options override config-level options."""
        config = _make_config({"permission_mode": "acceptEdits"})
        flow = _make_simple_flow()
        task_opts = {"permission_mode": "bypassPermissions"}

        result = _merge_provider_options(config, flow, "claude", task_opts)

        assert result is not None
        assert result["permission_mode"] == "bypassPermissions"

    def test_task_only_no_higher_levels(self):
        """Task-level options work when config and workflow have nothing."""
        config = FdsxConfig()
        flow = _make_simple_flow()
        task_opts = {"dangerously_skip_permissions": True}

        result = _merge_provider_options(config, flow, "claude", task_opts)

        assert result is not None
        assert result["dangerously_skip_permissions"] is True


class TestMergeProviderOptionsFourLevel:
    """Full 3-level merge (config → workflow → task) tests."""

    def test_full_three_level_merge(self):
        """All three levels are merged correctly with proper precedence."""
        config = _make_config({"permission_mode": "acceptEdits"})
        flow = _make_simple_flow(providers={"claude": {"allowed_tools": ["Bash"]}})
        task_opts = {"disallowed_tools": ["Write"]}

        result = _merge_provider_options(config, flow, "claude", task_opts)

        assert result is not None
        assert result["permission_mode"] == "acceptEdits"  # from config
        assert result["allowed_tools"] == ["Bash"]  # from workflow
        assert result["disallowed_tools"] == ["Write"]  # from task

    def test_task_wins_over_all(self):
        """Task-level wins when all three levels set the same key."""
        config = _make_config({"permission_mode": "acceptEdits"})
        flow = _make_simple_flow(providers={"claude": {"permission_mode": "plan"}})
        task_opts = {"permission_mode": "bypassPermissions"}

        result = _merge_provider_options(config, flow, "claude", task_opts)

        assert result is not None
        assert result["permission_mode"] == "bypassPermissions"


class TestMergeProviderOptionsAllNone:
    """Tests when all levels produce no options."""

    def test_all_none_returns_none(self):
        """When all levels are absent, returns None."""
        config = FdsxConfig()
        flow = _make_simple_flow()

        result = _merge_provider_options(config, flow, "claude", None)

        assert result is None

    def test_empty_task_options_with_no_other_levels(self):
        """Empty task_options dict with no higher-level options returns None."""
        config = FdsxConfig()
        flow = _make_simple_flow()

        result = _merge_provider_options(config, flow, "claude", {})

        assert result is None


class TestMergeProviderOptionsDifferentProviders:
    """Tests with different provider names."""

    def test_codex_provider_merge(self):
        """Merge works for codex provider."""
        config = FdsxConfig(
            providers=ProviderConfigs(
                codex=CodexOptions.model_validate({"sandbox": "read-only"}),
            )
        )
        flow = _make_simple_flow()

        result = _merge_provider_options(config, flow, "codex", None)

        assert result is not None
        assert result["sandbox"] == "read-only"

    def test_system_provider_returns_none_when_no_options(self):
        """System provider has no config entry → returns None."""
        config = _make_config({"permission_mode": "acceptEdits"})
        flow = _make_simple_flow()

        result = _merge_provider_options(config, flow, "system", None)

        # config.providers has no 'system' attribute → no config-level options
        assert result is None

    def test_unknown_provider_returns_none_when_no_options(self):
        """Unknown provider name with no task options → returns None gracefully."""
        config = _make_config({"permission_mode": "acceptEdits"})
        flow = _make_simple_flow()

        result = _merge_provider_options(config, flow, "unknown_provider", None)

        assert result is None

    def test_config_for_one_provider_doesnt_affect_another(self):
        """Config options for claude do not bleed into codex merge."""
        config = _make_config({"permission_mode": "acceptEdits"})
        flow = _make_simple_flow()

        result = _merge_provider_options(config, flow, "codex", None)

        assert result is None

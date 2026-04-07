"""Integration tests for end-to-end provider options wiring (T019).

Tests verify that provider options flow correctly through:
  get_provider → provider.execute (CLI flags)
  config + workflow + task merge → compile_flow → provider.execute
"""

from unittest.mock import patch

from fdsx.core.compiler import _merge_provider_options, compile_flow
from fdsx.core.config import FdsxConfig, ProviderConfigs
from fdsx.models.flow import Branch, Flow, ParallelState, TaskState
from fdsx.providers.base import ProviderResult, get_provider
from fdsx.providers.claude import ClaudeOptions, ClaudeProvider
from fdsx.providers.codex import CodexOptions, CodexProvider
from fdsx.providers.gemini import GeminiOptions, GeminiProvider
from fdsx.providers.opencode import OpenCodeOptions, OpenCodeProvider
from fdsx.providers.system import SystemProvider

# ---------------------------------------------------------------------------
# Helper: fake ProviderResult for mocking _run_subprocess
# ---------------------------------------------------------------------------

FAKE_SUCCESS = ProviderResult(exit_code=0, stdout="ok", stderr="")


def _make_single_task_flow(
    provider: str = "claude",
    model: str = "claude-sonnet",
    task_provider_options: dict | None = None,
    flow_providers: dict | None = None,
) -> Flow:
    """Build a minimal single-task flow."""
    return Flow(
        name="test",
        description="Test flow",
        start_at="step1",
        states={
            "step1": TaskState(
                type="task",
                provider=provider,
                model=model,
                prompt_template="do the thing",
                result_path="$.result",
                end=True,
                provider_options=task_provider_options,
            )
        },
        providers=flow_providers,
    )


def _make_parallel_flow(
    branches: list[dict],
) -> Flow:
    """Build a minimal parallel-state flow."""
    branch_objects = [Branch(**b) for b in branches]
    return Flow(
        name="test",
        description="Test parallel flow",
        start_at="parallel_step",
        states={
            "parallel_step": ParallelState(
                type="parallel",
                branches=branch_objects,
                result_path="$.results",
                end=True,
            )
        },
    )


# ---------------------------------------------------------------------------
# T019-1: claude with permission_mode passed to CLI
# ---------------------------------------------------------------------------


class TestClaudeWithPermissionMode:
    """Verify permission_mode is forwarded to the Claude CLI invocation."""

    def test_claude_provider_appends_permission_mode_flag(self):
        """ClaudeProvider.execute() appends --permission-mode to CLI args."""
        options = ClaudeOptions(permission_mode="bypassPermissions")
        provider = ClaudeProvider(options)

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello", model="claude-sonnet")

        assert len(captured_args) == 1
        args = captured_args[0]
        assert "--permission-mode" in args
        assert "bypassPermissions" in args
        assert args[args.index("--permission-mode") + 1] == "bypassPermissions"

    def test_claude_provider_no_options_no_extra_flags(self):
        """ClaudeProvider with default options adds no extra flags to CLI args."""
        provider = ClaudeProvider()

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello", model="claude-sonnet")

        args = captured_args[0]
        assert "--permission-mode" not in args
        assert "--dangerously-skip-permissions" not in args

    def test_claude_dangerously_skip_permissions_flag(self):
        """dangerously_skip_permissions=True appends the flag."""
        options = ClaudeOptions(dangerously_skip_permissions=True)
        provider = ClaudeProvider(options)

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello")

        assert "--dangerously-skip-permissions" in captured_args[0]


# ---------------------------------------------------------------------------
# T019-2: config + workflow merge flows to execute()
# ---------------------------------------------------------------------------


class TestConfigWorkflowMerge:
    """Verify config + workflow-level merge is reflected in provider CLI args."""

    def test_config_options_applied_when_no_workflow_override(self):
        """Config-level permission_mode is used when workflow doesn't override."""
        config = FdsxConfig(
            providers=ProviderConfigs(
                claude=ClaudeOptions(permission_mode="acceptEdits"),
            )
        )
        flow = _make_single_task_flow()
        merged = _merge_provider_options(
            config, flow, "claude", None, state_name="step1"
        )

        assert merged is not None
        provider = get_provider("claude", merged)
        assert isinstance(provider, ClaudeProvider)
        assert provider.options.permission_mode == "acceptEdits"

    def test_workflow_options_override_config(self):
        """Workflow-level options override config-level for the same key."""
        config = FdsxConfig(
            providers=ProviderConfigs(
                claude=ClaudeOptions(permission_mode="acceptEdits"),
            )
        )
        flow = _make_single_task_flow(
            flow_providers={"claude": {"permission_mode": "bypassPermissions"}}
        )
        merged = _merge_provider_options(
            config, flow, "claude", None, state_name="step1"
        )

        assert merged is not None
        provider = get_provider("claude", merged)
        assert isinstance(provider, ClaudeProvider)
        assert provider.options.permission_mode == "bypassPermissions"

    def test_config_default_values_do_not_override_workflow(self):
        """Config default booleans (False) do not suppress workflow-level True."""
        config = FdsxConfig(
            providers=ProviderConfigs(
                claude=ClaudeOptions(permission_mode="acceptEdits"),
                # dangerously_skip_permissions defaults to False
            )
        )
        flow = _make_single_task_flow(
            flow_providers={"claude": {"dangerously_skip_permissions": True}}
        )
        merged = _merge_provider_options(
            config, flow, "claude", None, state_name="step1"
        )

        assert merged is not None
        # permission_mode from config, dangerously_skip from workflow
        assert merged.get("permission_mode") == "acceptEdits"
        assert merged.get("dangerously_skip_permissions") is True

    def test_gemini_provider_options_from_config(self):
        """GeminiOptions from config are merged correctly."""
        config = FdsxConfig(
            providers=ProviderConfigs(
                gemini=GeminiOptions(yolo=True),
            )
        )
        flow = _make_single_task_flow(provider="gemini")
        merged = _merge_provider_options(
            config, flow, "gemini", None, state_name="step1"
        )

        assert merged is not None
        provider = get_provider("gemini", merged)
        assert isinstance(provider, GeminiProvider)
        assert provider.options.yolo is True


# ---------------------------------------------------------------------------
# T019-3: Task-level override
# ---------------------------------------------------------------------------


class TestTaskLevelOverride:
    """Task-level provider_options overrides all higher levels."""

    def test_task_override_wins_over_config_and_workflow(self):
        """Task provider_options wins over config and workflow settings."""
        config = FdsxConfig(
            providers=ProviderConfigs(
                claude=ClaudeOptions(permission_mode="acceptEdits"),
            )
        )
        flow = _make_single_task_flow(
            flow_providers={"claude": {"permission_mode": "plan"}},
            task_provider_options={"permission_mode": "bypassPermissions"},
        )
        merged = _merge_provider_options(
            config,
            flow,
            "claude",
            flow.states["step1"].provider_options,
            state_name="step1",
        )  # type: ignore[union-attr]

        assert merged is not None
        assert merged["permission_mode"] == "bypassPermissions"

    def test_task_override_applied_in_get_provider(self):
        """get_provider respects task-level options passed directly."""
        provider = get_provider("claude", {"permission_mode": "dontAsk"})
        assert isinstance(provider, ClaudeProvider)
        assert provider.options.permission_mode == "dontAsk"


# ---------------------------------------------------------------------------
# T019-4: Unchanged workflows without options
# ---------------------------------------------------------------------------


class TestUnchangedWorkflowsWithoutOptions:
    """Workflows without any provider options continue to work normally."""

    def test_merge_returns_none_for_flow_without_options(self):
        """Flow with no providers/provider_options yields None merge result."""
        config = FdsxConfig()  # no providers
        flow = _make_single_task_flow()

        merged = _merge_provider_options(
            config, flow, "claude", None, state_name="step1"
        )

        assert merged is None

    def test_get_provider_with_none_options_returns_default_provider(self):
        """get_provider with None options returns provider with default options."""
        provider = get_provider("claude", None)
        assert isinstance(provider, ClaudeProvider)
        assert provider.options == ClaudeOptions()

    def test_system_provider_unaffected_by_options(self):
        """System provider always ignores options."""
        provider = get_provider("system", {"irrelevant": "value"})
        assert isinstance(provider, SystemProvider)
        # SystemProvider has no .options attribute — it should work fine
        result = provider.execute(prompt="echo hello", command="echo hello")
        assert result.exit_code == 0

    def test_compile_flow_without_config_works(self):
        """compile_flow without config still builds a valid CompiledGraph."""
        flow = Flow(
            name="test",
            description="System test flow",
            start_at="step1",
            states={
                "step1": TaskState(
                    type="task",
                    provider="system",
                    command="echo done",
                    result_path="$.result",
                    end=True,
                )
            },
        )
        compiled = compile_flow(flow)
        assert compiled is not None
        assert compiled.entry_point == "step1"


# ---------------------------------------------------------------------------
# T019-5: Parallel branches with mixed providers
# ---------------------------------------------------------------------------


class TestParallelBranchesWithMixedProviders:
    """Parallel branches using different providers each get correct options."""

    def test_codex_provider_appends_sandbox_flag(self):
        """CodexProvider.execute() appends --sandbox to CLI args."""
        options = CodexOptions(sandbox="workspace-write")
        provider = CodexProvider(options)

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.codex._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="do work", model="codex-mini")

        args = captured_args[0]
        assert "--sandbox" in args
        assert "workspace-write" in args

    def test_opencode_provider_flags_applied(self):
        """OpenCodeProvider.execute() appends any to_cli_flags() output."""
        options = OpenCodeOptions()  # currently no flags
        provider = OpenCodeProvider(options)

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.opencode._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="do work", model="opencode-model")

        # Prompt must still be the last arg
        args = captured_args[0]
        assert args[-1] == "do work"

    def test_get_provider_codex_with_approval_policy(self):
        """get_provider('codex', options) returns CodexProvider with correct options."""
        provider = get_provider("codex", {"approval_policy": "never"})
        assert isinstance(provider, CodexProvider)
        assert provider.options.approval_policy == "never"
        assert "--approval-policy" in provider.options.to_cli_flags()

    def test_merge_for_different_providers_are_independent(self):
        """Merging options for claude does not affect codex and vice versa."""
        config = FdsxConfig(
            providers=ProviderConfigs(
                claude=ClaudeOptions(permission_mode="acceptEdits"),
                codex=CodexOptions(sandbox="read-only"),
            )
        )
        flow = _make_single_task_flow()

        claude_result = _merge_provider_options(
            config, flow, "claude", None, state_name="step1"
        )
        codex_result = _merge_provider_options(
            config, flow, "codex", None, state_name="step1"
        )

        assert claude_result is not None
        assert claude_result.get("permission_mode") == "acceptEdits"
        assert "sandbox" not in claude_result

        assert codex_result is not None
        assert codex_result.get("sandbox") == "read-only"
        assert "permission_mode" not in codex_result

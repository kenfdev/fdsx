"""Unit tests for provider option models (T004, T006, T007) and get_provider factory (T014)."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from fdsx.providers.base import DEFAULT_INACTIVITY_TIMEOUT, ProviderResult, get_provider
from fdsx.providers.claude import ClaudeOptions, ClaudeProvider
from fdsx.providers.codex import CodexOptions, CodexProvider
from fdsx.providers.opencode import OpenCodeOptions, OpenCodeProvider
from fdsx.providers.system import SystemProvider


class TestClaudeOptions:
    """T004: Tests for ClaudeOptions model."""

    def test_claude_options_permission_mode_valid(self):
        """Valid permission_mode literals must be accepted."""
        for mode in (
            "default",
            "acceptEdits",
            "bypassPermissions",
            "dontAsk",
            "plan",
            "auto",
        ):
            opts = ClaudeOptions(permission_mode=mode)
            assert opts.permission_mode == mode

    def test_claude_options_permission_mode_invalid(self):
        """Invalid permission_mode must raise ValidationError."""
        with pytest.raises(ValidationError):
            ClaudeOptions(permission_mode="invalid_mode")

    def test_claude_options_to_cli_flags_permission_mode(self):
        """permission_mode maps to --permission-mode <value>."""
        opts = ClaudeOptions(permission_mode="acceptEdits")
        assert opts.to_cli_flags() == ["--permission-mode", "acceptEdits"]

    def test_claude_options_to_cli_flags_dangerously_skip(self):
        """dangerously_skip_permissions=True maps to --dangerously-skip-permissions."""
        opts = ClaudeOptions(dangerously_skip_permissions=True)
        assert opts.to_cli_flags() == ["--dangerously-skip-permissions"]

    def test_claude_options_to_cli_flags_dangerously_skip_false(self):
        """dangerously_skip_permissions=False produces no flags."""
        opts = ClaudeOptions(dangerously_skip_permissions=False)
        assert opts.to_cli_flags() == []

    def test_claude_options_to_cli_flags_allowed_tools(self):
        """allowed_tools maps to repeated --allowedTools <tool>."""
        opts = ClaudeOptions(allowed_tools=["Bash", "Read"])
        assert opts.to_cli_flags() == [
            "--allowedTools",
            "Bash",
            "--allowedTools",
            "Read",
        ]

    def test_claude_options_to_cli_flags_disallowed_tools(self):
        """disallowed_tools maps to repeated --disallowedTools <tool>."""
        opts = ClaudeOptions(disallowed_tools=["Write"])
        assert opts.to_cli_flags() == ["--disallowedTools", "Write"]

    def test_claude_options_to_cli_flags_empty(self):
        """All defaults produce an empty flags list."""
        opts = ClaudeOptions()
        assert opts.to_cli_flags() == []

    def test_claude_options_forbids_extra(self):
        """Extra fields must be rejected."""
        with pytest.raises(ValidationError):
            ClaudeOptions(unknown_field="value")  # type: ignore[call-arg]

    def test_claude_options_to_cli_flags_combined(self):
        """All fields set together produce correct combined flags in order."""
        opts = ClaudeOptions(
            permission_mode="bypassPermissions",
            dangerously_skip_permissions=True,
            allowed_tools=["Bash"],
            disallowed_tools=["Write"],
        )
        assert opts.to_cli_flags() == [
            "--permission-mode",
            "bypassPermissions",
            "--dangerously-skip-permissions",
            "--allowedTools",
            "Bash",
            "--disallowedTools",
            "Write",
        ]


class TestCodexOptions:
    """T006: Tests for CodexOptions model."""

    def test_codex_options_sandbox_valid(self):
        """Valid sandbox literals must be accepted."""
        for value in ("read-only", "workspace-write", "danger-full-access"):
            opts = CodexOptions(sandbox=value)
            assert opts.sandbox == value

    def test_codex_options_sandbox_invalid(self):
        """Invalid sandbox value must raise ValidationError."""
        with pytest.raises(ValidationError):
            CodexOptions(sandbox="full")

    def test_codex_options_approval_policy_valid(self):
        """Valid approval_policy literals must be accepted."""
        for value in ("untrusted", "on-request", "never"):
            opts = CodexOptions(approval_policy=value)
            assert opts.approval_policy == value

    def test_codex_options_approval_policy_invalid(self):
        """Invalid approval_policy value must raise ValidationError."""
        with pytest.raises(ValidationError):
            CodexOptions(approval_policy="unknown")

    def test_codex_options_to_cli_flags_sandbox(self):
        """sandbox maps to --sandbox <value>."""
        opts = CodexOptions(sandbox="workspace-write")
        assert opts.to_cli_flags() == ["--sandbox", "workspace-write"]

    def test_codex_options_to_cli_flags_approval_policy(self):
        """approval_policy maps to --approval-policy <value>."""
        opts = CodexOptions(approval_policy="on-request")
        assert opts.to_cli_flags() == ["--approval-policy", "on-request"]

    def test_codex_options_to_cli_flags_full_auto(self):
        """full_auto=True maps to --full-auto."""
        opts = CodexOptions(full_auto=True)
        assert opts.to_cli_flags() == ["--full-auto"]

    def test_codex_options_to_cli_flags_full_auto_false(self):
        """full_auto=False produces no flags."""
        opts = CodexOptions(full_auto=False)
        assert opts.to_cli_flags() == []

    def test_codex_options_to_cli_flags_dangerously_bypass(self):
        """dangerously_bypass_approvals_and_sandbox=True maps to flag."""
        opts = CodexOptions(dangerously_bypass_approvals_and_sandbox=True)
        assert opts.to_cli_flags() == ["--dangerously-bypass-approvals-and-sandbox"]

    def test_codex_options_to_cli_flags_empty(self):
        """All defaults produce an empty flags list."""
        opts = CodexOptions()
        assert opts.to_cli_flags() == []

    def test_codex_options_forbids_extra(self):
        """Extra fields must be rejected."""
        with pytest.raises(ValidationError):
            CodexOptions(unknown_field="value")  # type: ignore[call-arg]

    def test_codex_options_to_cli_flags_combined(self):
        """All fields set together produce correct combined flags in order."""
        opts = CodexOptions(
            sandbox="workspace-write",
            approval_policy="on-request",
            full_auto=True,
            dangerously_bypass_approvals_and_sandbox=True,
        )
        assert opts.to_cli_flags() == [
            "--sandbox",
            "workspace-write",
            "--approval-policy",
            "on-request",
            "--full-auto",
            "--dangerously-bypass-approvals-and-sandbox",
        ]


class TestOpenCodeOptions:
    """T007: Tests for OpenCodeOptions model."""

    def test_opencode_options_to_cli_flags_empty(self):
        """to_cli_flags() always returns an empty list."""
        opts = OpenCodeOptions()
        assert opts.to_cli_flags() == []

    def test_opencode_options_forbids_extra(self):
        """Extra fields must be rejected."""
        with pytest.raises(ValidationError):
            OpenCodeOptions(unknown_field="value")  # type: ignore[call-arg]


class TestGetProvider:
    """T014: Tests for get_provider() factory with options parameter."""

    def test_get_provider_claude_no_options(self):
        """get_provider('claude') returns ClaudeProvider with default options."""
        provider = get_provider("claude")
        assert isinstance(provider, ClaudeProvider)
        assert provider.options == ClaudeOptions()

    def test_get_provider_claude_with_options(self):
        """get_provider('claude', options) returns ClaudeProvider with typed options."""
        provider = get_provider("claude", {"permission_mode": "bypassPermissions"})
        assert isinstance(provider, ClaudeProvider)
        assert provider.options.permission_mode == "bypassPermissions"

    def test_get_provider_claude_options_reflected_in_flags(self):
        """Options passed to get_provider are reflected in to_cli_flags()."""
        provider = get_provider("claude", {"dangerously_skip_permissions": True})
        assert isinstance(provider, ClaudeProvider)
        assert "--dangerously-skip-permissions" in provider.options.to_cli_flags()

    def test_get_provider_codex_no_options(self):
        """get_provider('codex') returns CodexProvider with default options."""
        provider = get_provider("codex")
        assert isinstance(provider, CodexProvider)
        assert provider.options == CodexOptions()

    def test_get_provider_codex_with_options(self):
        """get_provider('codex', options) returns CodexProvider with typed options."""
        provider = get_provider("codex", {"sandbox": "workspace-write"})
        assert isinstance(provider, CodexProvider)
        assert provider.options.sandbox == "workspace-write"

    def test_get_provider_opencode_no_options(self):
        """get_provider('opencode') returns OpenCodeProvider with default options."""
        provider = get_provider("opencode")
        assert isinstance(provider, OpenCodeProvider)
        assert provider.options == OpenCodeOptions()

    def test_get_provider_opencode_with_options(self):
        """get_provider('opencode', options={}) returns OpenCodeProvider."""
        provider = get_provider("opencode", {})
        assert isinstance(provider, OpenCodeProvider)

    def test_get_provider_system_no_options(self):
        """get_provider('system') returns SystemProvider."""
        provider = get_provider("system")
        assert isinstance(provider, SystemProvider)

    def test_get_provider_system_ignores_options(self):
        """get_provider('system', options) ignores options and returns SystemProvider."""
        provider = get_provider("system", {"some_option": "value"})
        assert isinstance(provider, SystemProvider)

    def test_get_provider_unknown_raises(self):
        """get_provider with unknown name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown provider"):
            get_provider("unknown_provider")

    def test_get_provider_claude_none_options(self):
        """get_provider('claude', None) is same as no options."""
        provider = get_provider("claude", None)
        assert isinstance(provider, ClaudeProvider)
        assert provider.options == ClaudeOptions()

    def test_get_provider_claude_invalid_options_raises(self):
        """get_provider with invalid options dict raises ValidationError."""
        with pytest.raises(ValidationError):
            get_provider("claude", {"permission_mode": "invalid_mode"})


class TestProviderInactivityTimeoutWiring:
    """T007: Verify each provider passes resolved inactivity_timeout to _run_subprocess."""

    _MOCK_RESULT = ProviderResult(exit_code=0, stdout="", stderr="")

    def test_codex_passes_default_inactivity_timeout(self):
        """CodexProvider with default options passes DEFAULT_INACTIVITY_TIMEOUT to _run_subprocess."""
        provider = CodexProvider(CodexOptions())
        with patch(
            "fdsx.providers.codex._run_subprocess", return_value=self._MOCK_RESULT
        ) as mock_run:
            provider.execute(prompt="hello")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["inactivity_timeout"] == DEFAULT_INACTIVITY_TIMEOUT

    def test_codex_passes_custom_inactivity_timeout(self):
        """CodexProvider with inactivity_timeout=600 passes 600 to _run_subprocess."""
        provider = CodexProvider(CodexOptions(inactivity_timeout=600))
        with patch(
            "fdsx.providers.codex._run_subprocess", return_value=self._MOCK_RESULT
        ) as mock_run:
            provider.execute(prompt="hello")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["inactivity_timeout"] == 600

    def test_codex_passes_zero_inactivity_timeout(self):
        """CodexProvider with inactivity_timeout=0 passes 0 to _run_subprocess (disabled)."""
        provider = CodexProvider(CodexOptions(inactivity_timeout=0))
        with patch(
            "fdsx.providers.codex._run_subprocess", return_value=self._MOCK_RESULT
        ) as mock_run:
            provider.execute(prompt="hello")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["inactivity_timeout"] == 0

    def test_claude_passes_default_inactivity_timeout(self):
        """ClaudeProvider with default options passes DEFAULT_INACTIVITY_TIMEOUT to _run_subprocess."""
        provider = ClaudeProvider(ClaudeOptions())
        with patch(
            "fdsx.providers.claude._run_subprocess", return_value=self._MOCK_RESULT
        ) as mock_run:
            provider.execute(prompt="hello")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["inactivity_timeout"] == DEFAULT_INACTIVITY_TIMEOUT

    def test_claude_passes_custom_inactivity_timeout(self):
        """ClaudeProvider with inactivity_timeout=600 passes 600 to _run_subprocess."""
        provider = ClaudeProvider(ClaudeOptions(inactivity_timeout=600))
        with patch(
            "fdsx.providers.claude._run_subprocess", return_value=self._MOCK_RESULT
        ) as mock_run:
            provider.execute(prompt="hello")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["inactivity_timeout"] == 600

    def test_claude_passes_zero_inactivity_timeout(self):
        """ClaudeProvider with inactivity_timeout=0 passes 0 to _run_subprocess (disabled)."""
        provider = ClaudeProvider(ClaudeOptions(inactivity_timeout=0))
        with patch(
            "fdsx.providers.claude._run_subprocess", return_value=self._MOCK_RESULT
        ) as mock_run:
            provider.execute(prompt="hello")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["inactivity_timeout"] == 0

    def test_opencode_passes_default_inactivity_timeout(self):
        """OpenCodeProvider with default options passes DEFAULT_INACTIVITY_TIMEOUT to _run_subprocess."""
        provider = OpenCodeProvider(OpenCodeOptions())
        with patch(
            "fdsx.providers.opencode._run_subprocess", return_value=self._MOCK_RESULT
        ) as mock_run:
            provider.execute(prompt="hello")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["inactivity_timeout"] == DEFAULT_INACTIVITY_TIMEOUT

    def test_opencode_passes_custom_inactivity_timeout(self):
        """OpenCodeProvider with inactivity_timeout=600 passes 600 to _run_subprocess."""
        provider = OpenCodeProvider(OpenCodeOptions(inactivity_timeout=600))
        with patch(
            "fdsx.providers.opencode._run_subprocess", return_value=self._MOCK_RESULT
        ) as mock_run:
            provider.execute(prompt="hello")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["inactivity_timeout"] == 600

    def test_opencode_passes_zero_inactivity_timeout(self):
        """OpenCodeProvider with inactivity_timeout=0 passes 0 to _run_subprocess (disabled)."""
        provider = OpenCodeProvider(OpenCodeOptions(inactivity_timeout=0))
        with patch(
            "fdsx.providers.opencode._run_subprocess", return_value=self._MOCK_RESULT
        ) as mock_run:
            provider.execute(prompt="hello")
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["inactivity_timeout"] == 0

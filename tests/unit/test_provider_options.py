"""Unit tests for provider option models (T004, T006, T007)."""

import pytest
from pydantic import ValidationError

from fdsx.providers.claude import ClaudeOptions
from fdsx.providers.codex import CodexOptions
from fdsx.providers.opencode import OpenCodeOptions


class TestClaudeOptions:
    """T004: Tests for ClaudeOptions model."""

    def test_claude_options_defaults(self):
        """All fields default to None/False/[] when not provided."""
        opts = ClaudeOptions()
        assert opts.permission_mode is None
        assert opts.dangerously_skip_permissions is False
        assert opts.allowed_tools == []
        assert opts.disallowed_tools == []

    def test_claude_options_permission_mode_valid(self):
        """Valid permission_mode literals must be accepted."""
        for mode in ("default", "acceptEdits", "bypassPermissions", "dontAsk", "plan", "auto"):
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
        assert opts.to_cli_flags() == ["--allowedTools", "Bash", "--allowedTools", "Read"]

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
            "--permission-mode", "bypassPermissions",
            "--dangerously-skip-permissions",
            "--allowedTools", "Bash",
            "--disallowedTools", "Write",
        ]


class TestCodexOptions:
    """T006: Tests for CodexOptions model."""

    def test_codex_options_defaults(self):
        """All fields default to None/False when not provided."""
        opts = CodexOptions()
        assert opts.sandbox is None
        assert opts.approval_policy is None
        assert opts.full_auto is False
        assert opts.dangerously_bypass_approvals_and_sandbox is False

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
            "--sandbox", "workspace-write",
            "--approval-policy", "on-request",
            "--full-auto",
            "--dangerously-bypass-approvals-and-sandbox",
        ]


class TestOpenCodeOptions:
    """T007: Tests for OpenCodeOptions model."""

    def test_opencode_options_defaults(self):
        """OpenCodeOptions can be instantiated with no arguments."""
        opts = OpenCodeOptions()
        assert opts is not None

    def test_opencode_options_to_cli_flags_empty(self):
        """to_cli_flags() always returns an empty list."""
        opts = OpenCodeOptions()
        assert opts.to_cli_flags() == []

    def test_opencode_options_forbids_extra(self):
        """Extra fields must be rejected."""
        with pytest.raises(ValidationError):
            OpenCodeOptions(unknown_field="value")  # type: ignore[call-arg]

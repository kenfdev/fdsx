"""Unit tests for generate_config_yaml and profile assignment logic."""

import pytest
from pydantic import ValidationError

from fdsx.core.init import generate_config_yaml
from fdsx.models.init import InitConfig, ProviderSelection


class TestGenerateConfigYaml:
    def test_single_provider_all_profiles(self):
        """All 5 profiles map to same provider when given one provider selection."""
        profile_assignments = {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }
        providers = [ProviderSelection(provider="claude", model="claude-sonnet-4-7")]
        config_yaml = generate_config_yaml(profile_assignments, providers)

        for profile_name in ["smarty", "doer", "specialist", "generalist", "behemoth"]:
            assert f"  {profile_name}:" in config_yaml
            assert "    provider: claude" in config_yaml
            assert "    model: claude-sonnet-4-7" in config_yaml

    def test_multiple_profiles_different_providers(self):
        """Different profiles can map to different providers."""
        profile_assignments = {
            "smarty": ProviderSelection(provider="claude", model="claude-sonnet-4-7"),
            "doer": ProviderSelection(provider="codex", model="codex-model"),
            "specialist": ProviderSelection(provider="claude", model="claude-opus-4"),
            "generalist": ProviderSelection(provider="gemini", model="gemini-model"),
            "behemoth": ProviderSelection(provider="opencode", model="opencode-model"),
        }
        providers = [
            ProviderSelection(provider="claude", model="claude-sonnet-4-7"),
            ProviderSelection(provider="codex", model="codex-model"),
            ProviderSelection(provider="gemini", model="gemini-model"),
            ProviderSelection(provider="opencode", model="opencode-model"),
        ]
        config_yaml = generate_config_yaml(profile_assignments, providers)

        assert "  smarty:" in config_yaml
        assert "    provider: claude" in config_yaml
        assert "    model: claude-sonnet-4-7" in config_yaml

        assert "  doer:" in config_yaml
        assert "    provider: codex" in config_yaml
        assert "    model: codex-model" in config_yaml

        assert "  generalist:" in config_yaml
        assert "    provider: gemini" in config_yaml
        assert "    model: gemini-model" in config_yaml

    def test_workflow_selector_with_generalist_profile(self):
        """Generated YAML contains workflow_selector section with profile: generalist."""
        profile_assignments = {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }
        providers = [ProviderSelection(provider="claude", model="claude-sonnet-4-7")]
        config_yaml = generate_config_yaml(profile_assignments, providers)

        assert "workflow_selector:" in config_yaml
        assert "  profile: generalist" in config_yaml

    def test_yaml_role_comments_present(self):
        """Raw string output includes role comment before smarty profile."""
        profile_assignments = {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }
        providers = [ProviderSelection(provider="claude", model="claude-sonnet-4-7")]
        config_yaml = generate_config_yaml(profile_assignments, providers)

        assert "# Deep reasoning and analysis" in config_yaml
        assert "# Fast execution" in config_yaml
        assert "# Domain-focused tasks" in config_yaml
        assert "# Broad capability tasks" in config_yaml
        assert "# Heavy/large-scale tasks" in config_yaml

    def test_profile_ordering(self):
        """Profiles appear in fixed order: smarty, doer, specialist, generalist, behemoth."""
        profile_assignments = {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }
        providers = [ProviderSelection(provider="claude", model="claude-sonnet-4-7")]
        config_yaml = generate_config_yaml(profile_assignments, providers)

        smarty_pos = config_yaml.find("smarty")
        doer_pos = config_yaml.find("doer")
        specialist_pos = config_yaml.find("specialist")
        generalist_pos = config_yaml.find("generalist")
        behemoth_pos = config_yaml.find("behemoth")

        assert smarty_pos < doer_pos < specialist_pos < generalist_pos < behemoth_pos

    def test_providers_section_with_max_permissions(self):
        """Providers section includes max-permission options for selected providers."""
        profile_assignments = {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }
        providers = [
            ProviderSelection(provider="claude", model="claude-sonnet-4-7"),
            ProviderSelection(provider="codex", model="codex-model"),
        ]
        config_yaml = generate_config_yaml(profile_assignments, providers)

        assert "providers:" in config_yaml
        assert "claude:" in config_yaml
        assert "dangerously_skip_permissions: True" in config_yaml
        assert "codex:" in config_yaml
        assert "dangerously_bypass_approvals_and_sandbox: True" in config_yaml


class TestProfileAssignmentValidation:
    def test_partial_profile_assignments_rejected(self):
        """InitConfig rejects profile_assignments with missing profiles."""
        with pytest.raises(ValidationError, match="missing"):
            InitConfig(
                providers=[ProviderSelection(provider="claude", model="sonnet")],
                profile_assignments={
                    "smarty": ProviderSelection(provider="claude", model="sonnet"),
                },
            )

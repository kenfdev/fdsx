"""Tests for the configuration system."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from fdsx.core.config import (
    FdsxConfig,
    ProviderConfigs,
    TaskSplitterConfig,
    WorkflowSelectorConfig,
    _deep_merge,
    load_config,
)
from fdsx.models.flow import HookConfig, HookEntry


class TestTaskSplitterConfigDefaults:
    def test_default_provider(self):
        cfg = TaskSplitterConfig()
        assert cfg.provider == "claude"

    def test_default_model(self):
        cfg = TaskSplitterConfig()
        assert cfg.model == "claude-sonnet-4-6"

    def test_custom_values(self):
        cfg = TaskSplitterConfig(provider="opencode", model="gpt-4o")
        assert cfg.provider == "opencode"
        assert cfg.model == "gpt-4o"

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValidationError):
            TaskSplitterConfig(provider="invalid")

    def test_extra_instructions_parses(self):
        cfg = TaskSplitterConfig(extra_instructions="foo")
        assert cfg.extra_instructions == "foo"

    def test_extra_instructions_defaults_none(self):
        cfg = TaskSplitterConfig()
        assert cfg.extra_instructions is None

    def test_extra_instructions_with_profile(self):
        cfg = TaskSplitterConfig(profile="smarty", extra_instructions="custom")
        assert cfg.profile == "smarty"
        assert cfg.extra_instructions == "custom"


class TestWorkflowSelectorConfigDefaults:
    def test_default_provider(self):
        cfg = WorkflowSelectorConfig()
        assert cfg.provider == "claude"

    def test_default_model(self):
        cfg = WorkflowSelectorConfig()
        assert cfg.model == "claude-sonnet-4-6"

    def test_custom_values(self):
        cfg = WorkflowSelectorConfig(provider="codex", model="o3")
        assert cfg.provider == "codex"
        assert cfg.model == "o3"

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValidationError):
            WorkflowSelectorConfig(provider="invalid")

    def test_extra_instructions_parses(self):
        cfg = WorkflowSelectorConfig(extra_instructions="foo")
        assert cfg.extra_instructions == "foo"

    def test_extra_instructions_defaults_none(self):
        cfg = WorkflowSelectorConfig()
        assert cfg.extra_instructions is None

    def test_extra_instructions_with_profile(self):
        cfg = WorkflowSelectorConfig(profile="smarty", extra_instructions="custom")
        assert cfg.profile == "smarty"
        assert cfg.extra_instructions == "custom"


class TestFdsxConfigDefaults:
    def test_default_task_splitter(self):
        cfg = FdsxConfig()
        assert cfg.task_splitter is None

    def test_default_workflow_selector(self):
        cfg = FdsxConfig()
        assert isinstance(cfg.workflow_selector, WorkflowSelectorConfig)
        assert cfg.workflow_selector.provider == "claude"

    def test_default_workflows_dir(self):
        cfg = FdsxConfig()
        assert cfg.workflows_dir == ".fdsx/workflows"

    def test_default_auto_workflow(self):
        cfg = FdsxConfig()
        assert cfg.auto_workflow is False

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            FdsxConfig.model_validate({"unknown_field": "value"})

    def test_workflows_dir_rejects_absolute(self):
        with pytest.raises(ValidationError):
            FdsxConfig(workflows_dir="/etc/workflows")

    def test_workflows_dir_accepts_relative(self):
        cfg = FdsxConfig(workflows_dir="my-workflows")
        assert cfg.workflows_dir == "my-workflows"

    def test_workflows_dir_rejects_traversal(self):
        """SEC-R2-2: paths with '..' components must be rejected."""
        with pytest.raises(ValidationError):
            FdsxConfig(workflows_dir="../shared-workflows")

    def test_workflows_dir_rejects_nested_traversal(self):
        """SEC-R2-2: nested '..' traversal must also be rejected."""
        with pytest.raises(ValidationError):
            FdsxConfig(workflows_dir="subdir/../../escape")


class TestLoadConfigNoFiles:
    def test_returns_defaults_when_no_files(self):
        cfg = load_config(load_global=False, load_project=False)
        assert cfg.task_splitter is None
        assert cfg.workflow_selector.provider == "claude"
        assert cfg.auto_workflow is False


class TestLoadConfigGlobal:
    def test_loads_global_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            config_file = config_dir / "config.yaml"
            config_file.write_text(yaml.dump({"task_splitter": {"provider": "codex"}}))

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmpdir
            try:
                cfg = load_config(load_global=True, load_project=False)
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg

            assert cfg.task_splitter.provider == "codex"
            assert cfg.task_splitter.model == "claude-sonnet-4-6"


class TestLoadConfigProject:
    def test_loads_project_config(self):
        """Project-level .fdsx/config.yaml overrides global and defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            fdsx_dir = project_dir / ".fdsx"
            fdsx_dir.mkdir()
            config_file = fdsx_dir / "config.yaml"
            config_file.write_text(yaml.dump({"auto_workflow": True}))

            cfg = load_config(
                project_dir=project_dir, load_global=False, load_project=True
            )
            assert cfg.auto_workflow is True


class TestLoadConfigMergePrecedence:
    def test_project_overrides_global(self):
        """Project-level config takes precedence over global config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            config_dir = Path(tmpdir) / "global_fdsx"
            config_dir.mkdir()
            global_file = config_dir / "config.yaml"
            global_file.write_text(
                yaml.dump({"task_splitter": {"model": "global-model"}})
            )

            fdsx_dir = project_dir / ".fdsx"
            fdsx_dir.mkdir()
            project_file = fdsx_dir / "config.yaml"
            project_file.write_text(
                yaml.dump({"task_splitter": {"model": "project-model"}})
            )

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = str(config_dir.parent)
            try:
                cfg = load_config(
                    project_dir=project_dir, load_global=True, load_project=True
                )
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg

            assert cfg.task_splitter.model == "project-model"

    def test_global_overrides_defaults(self):
        """Global config overrides built-in defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            global_file = config_dir / "config.yaml"
            global_file.write_text(
                yaml.dump({"workflow_selector": {"model": "override-model"}})
            )

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmpdir
            try:
                cfg = load_config(load_global=True, load_project=False)
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg

            assert cfg.workflow_selector.model == "override-model"


class TestLoadConfigValidation:
    def test_invalid_provider_in_global_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            config_file = config_dir / "config.yaml"
            config_file.write_text(yaml.dump({"task_splitter": {"provider": "bad"}}))

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmpdir
            try:
                with pytest.raises(ValidationError):
                    load_config(load_global=True, load_project=False)
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg

    def test_partial_global_override(self):
        """Partial override in global config preserves other defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            config_file = config_dir / "config.yaml"
            config_file.write_text(
                yaml.dump({"task_splitter": {"provider": "opencode"}})
            )

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmpdir
            try:
                cfg = load_config(load_global=True, load_project=False)
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg

            assert cfg.task_splitter.provider == "opencode"
            assert cfg.task_splitter.model == "claude-sonnet-4-6"

    def test_malformed_yaml_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            config_file = config_dir / "config.yaml"
            config_file.write_text(": :\n  - [invalid yaml {{")

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmpdir
            try:
                with pytest.raises(ValueError, match="Invalid YAML"):
                    load_config(load_global=True, load_project=False)
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg

    def test_non_mapping_config_raises_value_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            config_file = config_dir / "config.yaml"
            config_file.write_text("- item1\n- item2\n")

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmpdir
            try:
                with pytest.raises(ValueError, match="must be a YAML mapping"):
                    load_config(load_global=True, load_project=False)
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg


# T008: Unit tests for _deep_merge()
class TestDeepMerge:
    def test_flat_dict_override(self):
        """Override value replaces base value for the same key."""
        result = _deep_merge({"a": 1, "b": 2}, {"b": 3, "c": 4})
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_recursive_merge(self):
        """Nested dicts are merged field-by-field, not replaced wholesale."""
        base = {"outer": {"a": 1, "b": 2}}
        override = {"outer": {"b": 99, "c": 3}}
        result = _deep_merge(base, override)
        assert result == {"outer": {"a": 1, "b": 99, "c": 3}}

    def test_scalar_to_dict_override(self):
        """When base has a scalar and override has a dict, override wins."""
        result = _deep_merge({"key": "scalar"}, {"key": {"nested": True}})
        assert result["key"] == {"nested": True}

    def test_dict_to_scalar_override(self):
        """When base has a dict and override has a scalar, override wins."""
        result = _deep_merge({"key": {"nested": True}}, {"key": "scalar"})
        assert result["key"] == "scalar"

    def test_empty_override_preserves_base(self):
        """An empty override leaves the base unchanged."""
        base = {"a": 1, "b": {"c": 2}}
        result = _deep_merge(base, {})
        assert result == base

    def test_none_base_value_replaced_by_dict_override(self):
        """None base value is replaced when override provides a dict."""
        result = _deep_merge({"key": None}, {"key": {"nested": True}})
        assert result["key"] == {"nested": True}

    def test_base_not_mutated(self):
        """The base dict is not mutated by the merge."""
        base = {"outer": {"a": 1}}
        override = {"outer": {"b": 2}}
        _deep_merge(base, override)
        assert base == {"outer": {"a": 1}}

    def test_providers_merge_across_levels(self):
        """Provider config fields from global and project are merged, not replaced."""
        global_raw = {"providers": {"claude": {"permission_mode": "bypassPermissions"}}}
        project_raw = {"providers": {"claude": {"dangerously_skip_permissions": True}}}
        intermediate = _deep_merge({}, global_raw)
        result = _deep_merge(intermediate, project_raw)
        assert result["providers"]["claude"]["permission_mode"] == "bypassPermissions"
        assert result["providers"]["claude"]["dangerously_skip_permissions"] is True


# T010: Unit tests for FdsxConfig with providers section
class TestFdsxConfigProviders:
    def test_default_providers_is_none(self):
        cfg = FdsxConfig()
        assert cfg.providers is None

    def test_valid_claude_options_parsed(self):
        cfg = FdsxConfig.model_validate(
            {"providers": {"claude": {"permission_mode": "bypassPermissions"}}}
        )
        assert cfg.providers is not None
        assert cfg.providers.claude is not None
        assert cfg.providers.claude.permission_mode == "bypassPermissions"

    def test_invalid_claude_permission_mode_rejected(self):
        with pytest.raises(ValidationError):
            FdsxConfig.model_validate(
                {"providers": {"claude": {"permission_mode": "invalid_mode"}}}
            )

    def test_extra_provider_name_rejected(self):
        """Unknown provider names in the providers section are rejected."""
        with pytest.raises(ValidationError):
            FdsxConfig.model_validate({"providers": {"unknown_provider": {}}})

    def test_extra_claude_field_rejected(self):
        """Unknown fields inside a provider's options are rejected."""
        with pytest.raises(ValidationError):
            FdsxConfig.model_validate({"providers": {"claude": {"unknown_flag": True}}})

    def test_backward_compat_without_providers(self):
        """Existing configs without a providers section still parse correctly."""
        cfg = FdsxConfig.model_validate({"auto_workflow": True})
        assert cfg.providers is None
        assert cfg.auto_workflow is True

    def test_valid_codex_options_parsed(self):
        cfg = FdsxConfig.model_validate(
            {"providers": {"codex": {"sandbox": "read-only"}}}
        )
        assert cfg.providers is not None
        assert cfg.providers.codex is not None
        assert cfg.providers.codex.sandbox == "read-only"

    def test_valid_opencode_options_parsed(self):
        cfg = FdsxConfig.model_validate({"providers": {"opencode": {}}})
        assert cfg.providers is not None
        assert cfg.providers.opencode is not None

    def test_provider_configs_extra_field_rejected(self):
        """ProviderConfigs itself rejects unknown keys."""
        with pytest.raises(ValidationError):
            ProviderConfigs.model_validate({"unknown_key": {}})


class TestFdsxConfigProfiles:
    def test_default_profiles_is_none(self):
        cfg = FdsxConfig()
        assert cfg.profiles is None

    def test_valid_profile_parsed(self):
        cfg = FdsxConfig.model_validate(
            {
                "profiles": {
                    "my_profile": {"provider": "claude", "model": "claude-sonnet-4-6"}
                }
            }
        )
        assert cfg.profiles is not None
        assert "my_profile" in cfg.profiles
        assert cfg.profiles["my_profile"].provider == "claude"
        assert cfg.profiles["my_profile"].model == "claude-sonnet-4-6"

    def test_multiple_profiles_parsed(self):
        cfg = FdsxConfig.model_validate(
            {
                "profiles": {
                    "profile_a": {"provider": "claude", "model": "claude-sonnet-4-6"},
                    "profile_b": {"provider": "codex", "model": "gpt-4o"},
                }
            }
        )
        assert cfg.profiles is not None
        assert "profile_a" in cfg.profiles
        assert "profile_b" in cfg.profiles
        assert cfg.profiles["profile_a"].provider == "claude"
        assert cfg.profiles["profile_b"].provider == "codex"

    def test_backward_compat_without_profiles(self):
        """Existing configs without a profiles section still parse correctly."""
        cfg = FdsxConfig.model_validate({"auto_workflow": True})
        assert cfg.profiles is None
        assert cfg.auto_workflow is True

    def test_invalid_profile_name_key_rejected(self):
        """Profile name that doesn't match pattern is rejected."""
        with pytest.raises(ValidationError):
            FdsxConfig.model_validate(
                {
                    "profiles": {
                        "123bad": {"provider": "claude", "model": "claude-sonnet-4-6"}
                    }
                }
            )

    def test_invalid_provider_in_profile_rejected(self):
        """Profile with invalid provider is rejected."""
        with pytest.raises(ValidationError):
            FdsxConfig.model_validate(
                {
                    "profiles": {
                        "valid_name": {
                            "provider": "invalid_provider",
                            "model": "some-model",
                        }
                    }
                }
            )

    def test_profile_with_extra_fields_allowed(self):
        """ProfileConfig allows extra fields."""
        cfg = FdsxConfig.model_validate(
            {
                "profiles": {
                    "my_profile": {
                        "provider": "claude",
                        "model": "claude-sonnet-4-6",
                        "extra_option": True,
                    }
                }
            }
        )
        assert cfg.profiles is not None
        assert cfg.profiles["my_profile"].provider == "claude"


class TestLoadConfigWithProviders:
    def test_deep_merge_providers_across_global_and_project(self):
        """Global sets claude.permission_mode; project adds dangerously_skip_permissions; both preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            global_file = config_dir / "config.yaml"
            global_file.write_text(
                yaml.dump(
                    {"providers": {"claude": {"permission_mode": "bypassPermissions"}}}
                )
            )

            project_dir = Path(tmpdir)
            fdsx_dir = project_dir / ".fdsx"
            fdsx_dir.mkdir()
            project_file = fdsx_dir / "config.yaml"
            project_file.write_text(
                yaml.dump(
                    {"providers": {"claude": {"dangerously_skip_permissions": True}}}
                )
            )

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmpdir
            try:
                cfg = load_config(
                    project_dir=project_dir, load_global=True, load_project=True
                )
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg

            assert cfg.providers is not None
            assert cfg.providers.claude is not None
            assert cfg.providers.claude.permission_mode == "bypassPermissions"
            assert cfg.providers.claude.dangerously_skip_permissions is True

    def test_project_providers_key_overrides_global(self):
        """Project-level overrides the same key in global for the providers section."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            global_file = config_dir / "config.yaml"
            global_file.write_text(
                yaml.dump({"providers": {"claude": {"permission_mode": "default"}}})
            )

            project_dir = Path(tmpdir)
            fdsx_dir = project_dir / ".fdsx"
            fdsx_dir.mkdir()
            project_file = fdsx_dir / "config.yaml"
            project_file.write_text(
                yaml.dump(
                    {"providers": {"claude": {"permission_mode": "bypassPermissions"}}}
                )
            )

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmpdir
            try:
                cfg = load_config(
                    project_dir=project_dir, load_global=True, load_project=True
                )
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg

            assert cfg.providers is not None
            assert cfg.providers.claude is not None
            assert cfg.providers.claude.permission_mode == "bypassPermissions"

    def test_invalid_provider_option_in_config_file_raises(self):
        """Invalid option value in a config file raises ValidationError at load time."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            config_file = config_dir / "config.yaml"
            config_file.write_text(
                yaml.dump({"providers": {"claude": {"permission_mode": "bad_mode"}}})
            )

            original_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = tmpdir
            try:
                with pytest.raises(ValidationError):
                    load_config(load_global=True, load_project=False)
            finally:
                if original_xdg is None:
                    os.environ.pop("XDG_CONFIG_HOME", None)
                else:
                    os.environ["XDG_CONFIG_HOME"] = original_xdg


# T018: Unit tests for FdsxConfig.hooks field and _deep_merge hook list concatenation
class TestFdsxConfigHooks:
    def test_hooks_defaults_to_none(self):
        """T018: FdsxConfig.hooks is None when not specified."""
        cfg = FdsxConfig()
        assert cfg.hooks is None

    def test_hooks_accepts_hook_config(self):
        """T018: FdsxConfig.hooks accepts a valid HookConfig."""
        cfg = FdsxConfig(
            hooks=HookConfig(
                on_start=[HookEntry(command="setup.sh")],
                on_complete=[HookEntry(command="teardown.sh", on_failure="abort")],
            )
        )
        assert cfg.hooks is not None
        assert cfg.hooks.on_start[0].command == "setup.sh"
        assert cfg.hooks.on_complete[0].on_failure == "abort"

    def test_hooks_parsed_from_dict(self):
        """T018: FdsxConfig parses hooks from raw dict via model_validate."""
        cfg = FdsxConfig.model_validate(
            {
                "hooks": {
                    "on_start": [{"command": "init.sh"}],
                    "on_complete": [],
                }
            }
        )
        assert cfg.hooks is not None
        assert cfg.hooks.on_start[0].command == "init.sh"
        assert cfg.hooks.on_complete == []

    def test_hooks_invalid_on_failure_rejected(self):
        """T018: Invalid on_failure value in hooks config must be rejected."""
        with pytest.raises(ValidationError):
            FdsxConfig.model_validate(
                {"hooks": {"on_start": [{"command": "x", "on_failure": "skip"}]}}
            )

    def test_hooks_empty_command_rejected(self):
        """T018: Empty command in hook entry must be rejected."""
        with pytest.raises(ValidationError):
            FdsxConfig.model_validate({"hooks": {"on_start": [{"command": ""}]}})


class TestDeepMergeHookListConcatenation:
    """T018: Tests for _deep_merge list concatenation for on_start/on_complete."""

    def test_on_start_lists_are_concatenated(self):
        """T018: on_start lists from base and override are concatenated."""
        base = {"hooks": {"on_start": [{"command": "a.sh"}], "on_complete": []}}
        override = {"hooks": {"on_start": [{"command": "b.sh"}], "on_complete": []}}
        result = _deep_merge(base, override)
        assert len(result["hooks"]["on_start"]) == 2
        assert result["hooks"]["on_start"][0]["command"] == "a.sh"
        assert result["hooks"]["on_start"][1]["command"] == "b.sh"

    def test_on_complete_lists_are_concatenated(self):
        """T018: on_complete lists from base and override are concatenated."""
        base = {"hooks": {"on_start": [], "on_complete": [{"command": "cleanup.sh"}]}}
        override = {
            "hooks": {"on_start": [], "on_complete": [{"command": "notify.sh"}]}
        }
        result = _deep_merge(base, override)
        assert len(result["hooks"]["on_complete"]) == 2
        assert result["hooks"]["on_complete"][0]["command"] == "cleanup.sh"
        assert result["hooks"]["on_complete"][1]["command"] == "notify.sh"

    def test_empty_base_hook_list_with_override(self):
        """T018: Empty base list + override list yields only override entries."""
        base = {"hooks": {"on_start": [], "on_complete": []}}
        override = {"hooks": {"on_start": [{"command": "x.sh"}], "on_complete": []}}
        result = _deep_merge(base, override)
        assert result["hooks"]["on_start"] == [{"command": "x.sh"}]

    def test_empty_override_hook_list_with_base(self):
        """T018: Base list + empty override list yields only base entries."""
        base = {"hooks": {"on_start": [{"command": "base.sh"}], "on_complete": []}}
        override = {"hooks": {"on_start": [], "on_complete": []}}
        result = _deep_merge(base, override)
        assert result["hooks"]["on_start"] == [{"command": "base.sh"}]

    def test_non_hook_list_is_replaced_not_concatenated(self):
        """T018: Regular list values (not on_start/on_complete) are replaced, not concatenated."""
        base = {"items": [1, 2, 3]}
        override = {"items": [4, 5]}
        result = _deep_merge(base, override)
        assert result["items"] == [4, 5]

    def test_hook_list_concatenation_preserves_order(self):
        """T018: Concatenation preserves global-first, project-second ordering."""
        base = {
            "hooks": {
                "on_start": [{"command": "global1.sh"}, {"command": "global2.sh"}],
                "on_complete": [],
            }
        }
        override = {
            "hooks": {
                "on_start": [{"command": "project.sh"}],
                "on_complete": [],
            }
        }
        result = _deep_merge(base, override)
        commands = [e["command"] for e in result["hooks"]["on_start"]]
        assert commands == ["global1.sh", "global2.sh", "project.sh"]


class TestDeepMergeProfilesShallowReplacement:
    """T016: Tests for _deep_merge profiles key shallow replacement behavior."""

    def test_profiles_same_name_replaced_not_deep_merged(self):
        """Base has profiles.fast with claude/haiku, override has profiles.fast with codex/gpt. Result is override entirely."""
        base = {"profiles": {"fast": {"provider": "claude", "model": "haiku"}}}
        override = {"profiles": {"fast": {"provider": "codex", "model": "gpt"}}}
        result = _deep_merge(base, override)
        assert result["profiles"]["fast"] == {"provider": "codex", "model": "gpt"}
        assert "haiku" not in str(result["profiles"]["fast"])

    def test_profiles_different_names_both_preserved(self):
        """Base has profile 'a', override has profile 'b'. Both present in result."""
        base = {"profiles": {"a": {"provider": "claude", "model": "sonnet"}}}
        override = {"profiles": {"b": {"provider": "codex", "model": "gpt"}}}
        result = _deep_merge(base, override)
        assert "a" in result["profiles"]
        assert "b" in result["profiles"]
        assert result["profiles"]["a"] == {"provider": "claude", "model": "sonnet"}
        assert result["profiles"]["b"] == {"provider": "codex", "model": "gpt"}

    def test_profiles_override_drops_extra_fields_from_base(self):
        """Base profile has extra fields, override profile doesn't have them. Extra fields should NOT appear in result."""
        base = {
            "profiles": {
                "smart": {
                    "provider": "claude",
                    "model": "sonnet",
                    "extra_field": "should_be_dropped",
                }
            }
        }
        override = {"profiles": {"smart": {"provider": "codex", "model": "gpt"}}}
        result = _deep_merge(base, override)
        assert result["profiles"]["smart"] == {"provider": "codex", "model": "gpt"}
        assert "extra_field" not in result["profiles"]["smart"]

    def test_profiles_shallow_merge_preserves_unmodified_profiles(self):
        """Override only changes 'fast', leaves 'slow' profile from base untouched."""
        base = {
            "profiles": {
                "fast": {"provider": "claude", "model": "sonnet"},
                "slow": {"provider": "opencode", "model": "o1"},
            }
        }
        override = {"profiles": {"fast": {"provider": "codex", "model": "gpt"}}}
        result = _deep_merge(base, override)
        assert result["profiles"]["fast"] == {"provider": "codex", "model": "gpt"}
        assert result["profiles"]["slow"] == {"provider": "opencode", "model": "o1"}


class TestTaskSplitterConfigProfile:
    """Tests for TaskSplitterConfig profile field."""

    def test_profile_field_accepted(self):
        """TaskSplitterConfig(profile='my_profile') is accepted."""
        cfg = TaskSplitterConfig(profile="my_profile")
        assert cfg.profile == "my_profile"
        assert cfg.provider == "claude"
        assert cfg.model == "claude-sonnet-4-6"

    def test_profile_xor_with_explicit_provider(self):
        """Providing both profile and non-default provider raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSplitterConfig(profile="my_profile", provider="codex")
        assert "mutually exclusive" in str(exc_info.value)

    def test_profile_xor_with_explicit_model(self):
        """Providing both profile and non-default model raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSplitterConfig(profile="my_profile", model="gpt-4o")
        assert "mutually exclusive" in str(exc_info.value)

    def test_profile_xor_with_default_provider_raises(self):
        """Providing profile and provider='claude' (default) still raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSplitterConfig(profile="fast", provider="claude")
        assert "mutually exclusive" in str(exc_info.value)

    def test_profile_xor_with_default_model_raises(self):
        """Providing profile and model='claude-sonnet-4-6' (default) still raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            TaskSplitterConfig(profile="fast", model="claude-sonnet-4-6")
        assert "mutually exclusive" in str(exc_info.value)

    def test_profile_only_valid(self):
        """Profile alone is valid."""
        cfg = TaskSplitterConfig(profile="fast")
        assert cfg.profile == "fast"
        assert cfg.provider == "claude"
        assert cfg.model == "claude-sonnet-4-6"

    def test_no_profile_with_provider_valid(self):
        """Provider alone (no profile) is valid."""
        cfg = TaskSplitterConfig(provider="claude")
        assert cfg.provider == "claude"


class TestWorkflowSelectorConfigProfile:
    """Tests for WorkflowSelectorConfig profile field."""

    def test_profile_field_accepted(self):
        """WorkflowSelectorConfig(profile='my_profile') is accepted."""
        cfg = WorkflowSelectorConfig(profile="my_profile")
        assert cfg.profile == "my_profile"
        assert cfg.provider == "claude"
        assert cfg.model == "claude-sonnet-4-6"

    def test_profile_xor_with_explicit_provider(self):
        """Providing both profile and non-default provider raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowSelectorConfig(profile="my_profile", provider="codex")
        assert "mutually exclusive" in str(exc_info.value)

    def test_profile_xor_with_explicit_model(self):
        """Providing both profile and non-default model raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowSelectorConfig(profile="my_profile", model="gpt-4o")
        assert "mutually exclusive" in str(exc_info.value)

    def test_profile_xor_with_default_provider_raises(self):
        """Providing profile and provider='claude' (default) still raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowSelectorConfig(profile="fast", provider="claude")
        assert "mutually exclusive" in str(exc_info.value)

    def test_profile_xor_with_default_model_raises(self):
        """Providing profile and model='claude-sonnet-4-6' (default) still raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            WorkflowSelectorConfig(profile="fast", model="claude-sonnet-4-6")
        assert "mutually exclusive" in str(exc_info.value)

    def test_profile_only_valid(self):
        """Profile alone is valid."""
        cfg = WorkflowSelectorConfig(profile="fast")
        assert cfg.profile == "fast"
        assert cfg.provider == "claude"
        assert cfg.model == "claude-sonnet-4-6"

    def test_no_profile_with_provider_valid(self):
        """Provider alone (no profile) is valid."""
        cfg = WorkflowSelectorConfig(provider="claude")
        assert cfg.provider == "claude"


class TestResolveProfilesInConfig:
    """Tests for resolve_profiles_in_config function."""

    def test_resolve_profiles_in_config_task_splitter(self):
        """Raw dict with task_splitter.profile gets resolved."""
        from fdsx.core.profiles import resolve_profiles_in_config

        data = {
            "task_splitter": {"profile": "fast"},
            "profiles": {"fast": {"provider": "claude", "model": "haiku"}},
        }
        data, errors = resolve_profiles_in_config(data)

        assert errors == []
        assert data["task_splitter"]["provider"] == "claude"
        assert data["task_splitter"]["model"] == "haiku"
        assert "profile" not in data["task_splitter"]

    def test_resolve_profiles_in_config_workflow_selector(self):
        """Raw dict with workflow_selector.profile gets resolved."""
        from fdsx.core.profiles import resolve_profiles_in_config

        data = {
            "workflow_selector": {"profile": "smart"},
            "profiles": {"smart": {"provider": "claude", "model": "sonnet-4-6"}},
        }
        data, errors = resolve_profiles_in_config(data)

        assert errors == []
        assert data["workflow_selector"]["provider"] == "claude"
        assert data["workflow_selector"]["model"] == "sonnet-4-6"
        assert "profile" not in data["workflow_selector"]

    def test_resolve_profiles_in_config_missing_profile_error(self):
        """Profile not in profiles dict returns error."""
        from fdsx.core.profiles import resolve_profiles_in_config

        data = {
            "task_splitter": {"profile": "nonexistent"},
            "profiles": {"fast": {"provider": "claude", "model": "haiku"}},
        }
        data, errors = resolve_profiles_in_config(data)

        assert len(errors) == 1
        assert "not found" in errors[0]
        assert "nonexistent" in errors[0]

    def test_resolve_profiles_in_config_no_profiles_no_reference_noop(self):
        """No profiles section and no profile references is a no-op."""
        from fdsx.core.profiles import resolve_profiles_in_config

        data = {"task_splitter": {"provider": "claude", "model": "haiku"}}
        original = dict(data["task_splitter"])
        data, errors = resolve_profiles_in_config(data)

        assert errors == []
        assert data["task_splitter"] == original

    def test_resolve_profiles_in_config_missing_profiles_section_with_reference_errors(
        self,
    ):
        """Missing profiles section with a profile reference produces an error."""
        from fdsx.core.profiles import resolve_profiles_in_config

        data = {"task_splitter": {"profile": "fast"}}
        data, errors = resolve_profiles_in_config(data)

        assert len(errors) == 1
        assert "not found" in errors[0]
        assert "fast" in errors[0]

    def test_resolve_profiles_in_config_missing_profiles_section_workflow_selector_errors(
        self,
    ):
        """Missing profiles section with workflow_selector profile reference produces an error."""
        from fdsx.core.profiles import resolve_profiles_in_config

        data = {"workflow_selector": {"profile": "smart"}}
        data, errors = resolve_profiles_in_config(data)

        assert len(errors) == 1
        assert "not found" in errors[0]
        assert "smart" in errors[0]


class TestLoadConfigResolvesProfiles:
    """Tests that profile resolution works end-to-end through load_config()."""

    def test_load_config_resolves_workflow_selector_profile(
        self, tmp_path, monkeypatch
    ):
        """workflow_selector.profile resolves through load_config() without false XOR."""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".fdsx"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text(
            "profiles:\n"
            "  fast:\n"
            "    provider: codex\n"
            "    model: codex-mini\n"
            "workflow_selector:\n"
            "  profile: fast\n"
        )
        cfg = load_config(project_dir=tmp_path, load_global=False)
        assert cfg.workflow_selector.provider == "codex"
        assert cfg.workflow_selector.model == "codex-mini"
        assert cfg.workflow_selector.profile is None  # resolved and removed

    def test_load_config_resolves_task_splitter_profile(self, tmp_path, monkeypatch):
        """task_splitter.profile resolves through load_config() without false XOR."""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".fdsx"
        config_dir.mkdir()
        config_file = config_dir / "config.yaml"
        config_file.write_text(
            "profiles:\n"
            "  fast:\n"
            "    provider: codex\n"
            "    model: codex-mini\n"
            "task_splitter:\n"
            "  profile: fast\n"
        )
        cfg = load_config(project_dir=tmp_path, load_global=False)
        assert cfg.task_splitter is not None
        assert cfg.task_splitter.provider == "codex"
        assert cfg.task_splitter.model == "codex-mini"

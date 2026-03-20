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
            FdsxConfig.model_validate(
                {"providers": {"claude": {"unknown_flag": True}}}
            )

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


class TestLoadConfigWithProviders:
    def test_deep_merge_providers_across_global_and_project(self):
        """Global sets claude.permission_mode; project adds dangerously_skip_permissions; both preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "fdsx"
            config_dir.mkdir()
            global_file = config_dir / "config.yaml"
            global_file.write_text(
                yaml.dump({"providers": {"claude": {"permission_mode": "bypassPermissions"}}})
            )

            project_dir = Path(tmpdir)
            fdsx_dir = project_dir / ".fdsx"
            fdsx_dir.mkdir()
            project_file = fdsx_dir / "config.yaml"
            project_file.write_text(
                yaml.dump({"providers": {"claude": {"dangerously_skip_permissions": True}}})
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
                yaml.dump({"providers": {"claude": {"permission_mode": "bypassPermissions"}}})
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

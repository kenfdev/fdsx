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
    TaskSplitterConfig,
    WorkflowSelectorConfig,
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
        assert isinstance(cfg.task_splitter, TaskSplitterConfig)
        assert cfg.task_splitter.provider == "claude"
        assert cfg.task_splitter.model == "claude-sonnet-4-6"

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
        assert cfg.task_splitter.provider == "claude"
        assert cfg.task_splitter.model == "claude-sonnet-4-6"
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

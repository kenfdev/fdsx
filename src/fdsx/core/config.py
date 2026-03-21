"""Configuration system for fdsx.

Loads config from XDG global (~/.config/fdsx/config.yaml) and project-level
.fdsx/config.yaml, deep-merging with project taking precedence.
Built-in defaults are used when no config files exist.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from fdsx.models.flow import HookConfig
from fdsx.models.validators import validate_llm_provider
from fdsx.providers.claude import ClaudeOptions
from fdsx.providers.codex import CodexOptions
from fdsx.providers.opencode import OpenCodeOptions

# Keys within HookConfig whose list values are concatenated (not replaced) during deep merge
_HOOK_LIST_KEYS: frozenset[str] = frozenset({"on_start", "on_complete"})


class TaskSplitterConfig(BaseModel):
    """Configuration for batch task splitting (formerly TaskSplitter in flow.py)."""

    provider: str = Field(
        default="claude",
        description="Provider name (claude/opencode/codex)",
    )
    model: str = Field(
        default="claude-sonnet-4-6",
        description="Model name",
    )

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        return validate_llm_provider(v, "task_splitter")


class WorkflowSelectorConfig(BaseModel):
    """Configuration for workflow auto-selection."""

    provider: str = Field(
        default="claude",
        description="Provider for workflow selection",
    )
    model: str = Field(
        default="claude-sonnet-4-6",
        description="Model for workflow selection",
    )

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        return validate_llm_provider(v, "workflow_selector")


class ProviderConfigs(BaseModel):
    """Configuration for provider-specific options."""

    claude: ClaudeOptions | None = Field(
        default=None,
        description="Claude provider options",
    )
    codex: CodexOptions | None = Field(
        default=None,
        description="Codex provider options",
    )
    opencode: OpenCodeOptions | None = Field(
        default=None,
        description="OpenCode provider options",
    )

    model_config = {"extra": "forbid"}


class FdsxConfig(BaseModel):
    """Top-level fdsx configuration."""

    task_splitter: TaskSplitterConfig | None = Field(
        default=None,
        description="Batch task splitting configuration (must be explicitly configured)",
    )
    workflow_selector: WorkflowSelectorConfig = Field(
        default_factory=WorkflowSelectorConfig,
        description="Workflow auto-selection configuration",
    )
    workflows_dir: str = Field(
        default=".fdsx/workflows",
        description="Directory to discover workflow files",
    )
    auto_workflow: bool = Field(
        default=False,
        description="Skip confirmation for auto-selected workflows",
    )
    providers: ProviderConfigs | None = Field(
        default=None,
        description="Provider-specific configuration options",
    )
    hooks: HookConfig | None = Field(
        default=None,
        description="Global hook configuration applied to all flows",
    )

    @field_validator("workflows_dir")
    @classmethod
    def validate_workflows_dir(cls, v: str) -> str:
        p = Path(v)
        if p.is_absolute():
            raise ValueError(f"workflows_dir must be a relative path, got '{v}'")
        if ".." in p.parts:
            raise ValueError(
                f"workflows_dir must not contain '..' components, got '{v}'"
            )
        return v

    model_config = {"extra": "forbid"}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base, with override taking precedence.

    When both base and override have a dict value for the same key, the dicts
    are merged recursively. For keys in _HOOK_LIST_KEYS (on_start, on_complete),
    list values are concatenated (base + override) rather than replaced.
    Otherwise, the override value wins outright.

    Args:
        base: Base dictionary (lower priority).
        override: Override dictionary (higher priority).

    Returns:
        New merged dictionary (does not mutate either input).
    """
    result: dict[str, Any] = dict(base)
    for key, override_val in override.items():
        base_val = result.get(key)
        if isinstance(base_val, dict) and isinstance(override_val, dict):
            result[key] = _deep_merge(base_val, override_val)
        elif (
            key in _HOOK_LIST_KEYS
            and isinstance(base_val, list)
            and isinstance(override_val, list)
        ):
            result[key] = base_val + override_val
        else:
            result[key] = override_val
    return result


def _load_yaml(path: Path | None) -> dict[str, Any]:
    """Load YAML file, returning empty dict if file doesn't exist."""
    if path is None or not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in config file {path}: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"Config file {path} must be a YAML mapping, got {type(data).__name__}"
        )
    return data


def _resolve_xdg_config_dir() -> Path | None:
    """Resolve XDG_CONFIG_HOME or fallback to ~/.config."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        config_dir = Path(xdg)
    else:
        config_dir = Path.home() / ".config"
    fdsx_dir = config_dir / "fdsx"
    return fdsx_dir if fdsx_dir.exists() else None


def _resolve_project_config_dir(cwd: Path | None = None) -> Path | None:
    """Resolve project-level .fdsx config directory."""
    if cwd is None:
        cwd = Path.cwd()
    project_dir = cwd / ".fdsx"
    return project_dir if project_dir.exists() else None


def load_config(
    project_dir: Path | None = None,
    *,
    load_global: bool = True,
    load_project: bool = True,
) -> FdsxConfig:
    """Load and merge fdsx configuration.

    Resolution order (later wins):
    1. Built-in defaults (FdsxConfig defaults)
    2. Global config: $XDG_CONFIG_HOME/fdsx/config.yaml (or ~/.config/fdsx/config.yaml)
    3. Project config: <project_dir>/.fdsx/config.yaml

    Args:
        project_dir: Project root directory. Defaults to CWD.
        load_global: Whether to load global config. Defaults to True.
        load_project: Whether to load project config. Defaults to True.

    Returns:
        Merged FdsxConfig with all three layers applied.
    """
    defaults = FdsxConfig()

    raw_global: dict[str, Any] = {}
    if load_global:
        global_dir = _resolve_xdg_config_dir()
        if global_dir is not None:
            raw_global = _load_yaml(global_dir / "config.yaml")

    raw_project: dict[str, Any] = {}
    if load_project:
        proj_dir = project_dir or Path.cwd()
        proj_config_dir = _resolve_project_config_dir(proj_dir)
        if proj_config_dir is not None:
            raw_project = _load_yaml(proj_config_dir / "config.yaml")

    merged: dict[str, Any] = _deep_merge(
        _deep_merge(defaults.model_dump(), raw_global), raw_project
    )

    return FdsxConfig.model_validate(merged)

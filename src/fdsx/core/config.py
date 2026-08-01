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
from pydantic import BaseModel, Field, field_validator, model_validator

from fdsx.core.profiles import resolve_profiles_in_config
from fdsx.models.flow import (
    EscalationConfig,
    ExtractionFallback,
    HookConfig,
    HookEntry,
    ProfileConfig,
)
from fdsx.models.validators import validate_llm_provider, validate_profile_name
from fdsx.providers.claude import ClaudeOptions
from fdsx.providers.codex import CodexOptions
from fdsx.providers.cursor import CursorOptions
from fdsx.providers.gemini import GeminiOptions
from fdsx.providers.grok import GrokOptions
from fdsx.providers.opencode import OpenCodeOptions

# Keys within HookConfig whose list values are concatenated (not replaced) during deep merge
_HOOK_LIST_KEYS: frozenset[str] = frozenset(
    {
        "on_state_start",
        "on_state_end",
        "on_workflow_start",
        "on_workflow_end",
        "on_run_start",
        "on_run_end",
        "on_wait_start",
        "on_wait_end",
    }
)

# Keys whose dict values are shallow-merged instead of deep-merged
_SHALLOW_MERGE_KEYS: frozenset[str] = frozenset({"profiles"})

# Keys whose dict values are fully replaced by the override (no field-level merging)
_FULL_REPLACE_KEYS: frozenset[str] = frozenset(
    {"retry_escalation", "extraction_fallback"}
)


class WorkflowSelectorConfig(BaseModel):
    """Configuration for workflow auto-selection."""

    profile: str | None = Field(
        default=None,
        description="Profile name for provider/model configuration",
    )
    provider: str = Field(
        default="claude",
        description="Provider for workflow selection",
    )
    model: str = Field(
        default="claude-sonnet-4-6",
        description="Model for workflow selection",
    )
    extra_instructions: str | None = Field(
        default=None,
        description="Additional instructions appended to the workflow selection prompt",
    )

    @model_validator(mode="before")
    @classmethod
    def validate_profile_xor(cls, values: dict[str, Any]) -> dict[str, Any]:
        if isinstance(values, dict):
            has_profile = "profile" in values and values["profile"] is not None
            has_provider = "provider" in values
            has_model = "model" in values
            if has_profile and (has_provider or has_model):
                raise ValueError(
                    "profile and (provider|model) are mutually exclusive. "
                    "Use either profile reference or explicit provider/model, not both."
                )
        return values

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
    cursor: CursorOptions | None = Field(
        default=None,
        description="Cursor provider options",
    )
    codex: CodexOptions | None = Field(
        default=None,
        description="Codex provider options",
    )
    opencode: OpenCodeOptions | None = Field(
        default=None,
        description="OpenCode provider options",
    )
    gemini: GeminiOptions | None = Field(
        default=None,
        description="Gemini provider options",
    )
    grok: GrokOptions | None = Field(
        default=None,
        description="Grok provider options",
    )

    model_config = {"extra": "forbid"}


class RunHookConfig(BaseModel):
    """Hook configuration for run-level lifecycle events (fired once per CLI invocation)."""

    on_run_start: list[HookEntry] = Field(
        default_factory=list,
        description="Hooks to run at the start of a CLI invocation",
    )
    on_run_end: list[HookEntry] = Field(
        default_factory=list,
        description="Hooks to run at the end of a CLI invocation",
    )

    model_config = {"extra": "forbid"}


class FdsxConfig(BaseModel):
    """Top-level fdsx configuration."""

    @model_validator(mode="before")
    @classmethod
    def reject_removed_task_splitter(cls, values: Any) -> Any:
        if isinstance(values, dict) and "task_splitter" in values:
            raise ValueError(
                "task_splitter has been removed. Delete the task_splitter section; "
                "fdsx add now queues each input file directly."
            )
        return values

    workflow_selector: WorkflowSelectorConfig = Field(
        default_factory=WorkflowSelectorConfig,
        description="Workflow auto-selection configuration",
    )
    workflows_dir: str = Field(
        default=".fdsx/workflows",
        description="Directory to discover workflow files",
    )
    default_tasks_dir: str | None = Field(
        default=None,
        description="Default tasks directory for no-arg fdsx run",
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
    run_hooks: RunHookConfig | None = Field(
        default=None,
        description="Run-level hooks fired once per CLI invocation",
    )
    profiles: dict[str, ProfileConfig] | None = Field(
        default=None,
        description="Named provider/model profiles",
    )
    extraction_fallback: ExtractionFallback | None = Field(
        default=None,
        description="Global default extraction fallback applied when no per-rule fallback is set.",
    )
    retry_escalation: EscalationConfig | None = Field(
        default=None,
        description="Global default retry escalation applied when no workflow-level escalation is set.",
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

    @field_validator("profiles", mode="before")
    @classmethod
    def validate_profile_names(cls, v: Any) -> Any:
        if v is None:
            return v
        if not isinstance(v, dict):
            raise ValueError(f"profiles must be a dict, got {type(v).__name__}")
        for key in v:
            validate_profile_name(key)
        return v

    model_config = {"extra": "forbid"}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base, with override taking precedence.

    When both base and override have a dict value for the same key, the dicts
    are merged recursively. For keys in _HOOK_LIST_KEYS (on_state_start, on_state_end),
    list values are concatenated (base + override) rather than replaced.
    For keys in _SHALLOW_MERGE_KEYS (profiles), dict values are shallow-merged
    (full replacement per name, not deep merge of individual profile fields).
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
            if key in _FULL_REPLACE_KEYS:
                result[key] = override_val
            elif key in _SHALLOW_MERGE_KEYS:
                result[key] = {**base_val, **override_val}
            else:
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
    with path.open(encoding="utf-8") as f:
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
    config_dir = Path(xdg) if xdg else Path.home() / ".config"
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

    # Merge user configs first (without defaults) so profile resolution
    # sees only explicitly-provided keys — no false XOR from defaults.
    user_merged: dict[str, Any] = _deep_merge(raw_global, raw_project)

    user_merged, profile_errors = resolve_profiles_in_config(user_merged)
    if profile_errors:
        raise ValueError("; ".join(profile_errors))

    # Now merge with defaults to fill in missing fields
    merged: dict[str, Any] = _deep_merge(defaults.model_dump(), user_merged)

    return FdsxConfig.model_validate(merged)

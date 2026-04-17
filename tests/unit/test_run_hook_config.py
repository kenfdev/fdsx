"""Tests for RunHookConfig model, _deep_merge run_hooks concatenation, and reject_run_scope_keys validators.

Covers T001 (RunHookConfig + FdsxConfig.run_hooks), T002 (_deep_merge concatenation),
and T003 (reject_run_scope_keys validators).
"""

import pytest
from pydantic import ValidationError

from fdsx.core.config import FdsxConfig, RunHookConfig, _deep_merge
from fdsx.models.flow import HookConfig, HookEntry, StateHookConfig


class TestRunHookConfig:
    """T001: RunHookConfig model and FdsxConfig.run_hooks field."""

    def test_run_hook_config_accepts_valid_on_run_start(self):
        """RunHookConfig accepts valid on_run_start list of HookEntry."""
        config = RunHookConfig(on_run_start=[HookEntry(command="echo start")])
        assert len(config.on_run_start) == 1
        assert config.on_run_start[0].command == "echo start"

    def test_run_hook_config_accepts_valid_on_run_end(self):
        """RunHookConfig accepts valid on_run_end list of HookEntry."""
        config = RunHookConfig(
            on_run_end=[HookEntry(command="echo end", on_failure="abort")]
        )
        assert len(config.on_run_end) == 1
        assert config.on_run_end[0].command == "echo end"
        assert config.on_run_end[0].on_failure == "abort"

    def test_run_hook_config_defaults_to_empty_lists(self):
        """RunHookConfig defaults to empty lists for both fields."""
        config = RunHookConfig()
        assert config.on_run_start == []
        assert config.on_run_end == []

    def test_run_hook_config_rejects_unknown_keys(self):
        """RunHookConfig rejects unknown keys via extra='forbid'."""
        with pytest.raises(ValidationError):
            RunHookConfig(on_run_start=[], unknown_key="bad")

    def test_run_hook_config_rejects_on_state_start(self):
        """RunHookConfig rejects on_state_start (wrong scope key)."""
        with pytest.raises(ValidationError):
            RunHookConfig(on_state_start=[{"command": "x"}])

    def test_fdsx_config_run_hooks_is_none_by_default(self):
        """FdsxConfig.run_hooks defaults to None."""
        config = FdsxConfig()
        assert config.run_hooks is None

    def test_fdsx_config_accepts_run_hooks(self):
        """FdsxConfig validates successfully with run_hooks set."""
        config = FdsxConfig(
            run_hooks=RunHookConfig(
                on_run_start=[HookEntry(command="setup.sh")],
                on_run_end=[HookEntry(command="teardown.sh")],
            )
        )
        assert config.run_hooks is not None
        assert len(config.run_hooks.on_run_start) == 1
        assert config.run_hooks.on_run_start[0].command == "setup.sh"

    def test_fdsx_config_accepts_run_hooks_from_dict(self):
        """FdsxConfig validates run_hooks from dict (model_validate path)."""
        config = FdsxConfig.model_validate(
            {
                "run_hooks": {
                    "on_run_start": [{"command": "init.sh"}],
                    "on_run_end": [{"command": "cleanup.sh"}],
                }
            }
        )
        assert config.run_hooks is not None
        assert config.run_hooks.on_run_start[0].command == "init.sh"
        assert config.run_hooks.on_run_end[0].command == "cleanup.sh"


class TestDeepMergeRunHooks:
    """T002: _deep_merge concatenates on_run_start/on_run_end lists."""

    def test_on_run_start_global_and_project_concatenated(self):
        """Global on_run_start + project on_run_start are concatenated (global-first)."""
        base = {
            "run_hooks": {"on_run_start": [{"command": "global.sh"}], "on_run_end": []}
        }
        override = {
            "run_hooks": {"on_run_start": [{"command": "project.sh"}], "on_run_end": []}
        }
        result = _deep_merge(base, override)
        assert len(result["run_hooks"]["on_run_start"]) == 2
        assert result["run_hooks"]["on_run_start"][0]["command"] == "global.sh"
        assert result["run_hooks"]["on_run_start"][1]["command"] == "project.sh"

    def test_on_run_end_global_and_project_concatenated(self):
        """Global on_run_end + project on_run_end are concatenated (global-first)."""
        base = {
            "run_hooks": {
                "on_run_start": [],
                "on_run_end": [{"command": "global-end.sh"}],
            }
        }
        override = {
            "run_hooks": {
                "on_run_start": [],
                "on_run_end": [{"command": "project-end.sh"}],
            }
        }
        result = _deep_merge(base, override)
        assert len(result["run_hooks"]["on_run_end"]) == 2
        assert result["run_hooks"]["on_run_end"][0]["command"] == "global-end.sh"
        assert result["run_hooks"]["on_run_end"][1]["command"] == "project-end.sh"

    def test_project_only_run_hooks_used_as_is(self):
        """Project-only run_hooks are used as-is when global has none."""
        base = {}
        override = {
            "run_hooks": {
                "on_run_start": [{"command": "project.sh"}],
                "on_run_end": [],
            }
        }
        result = _deep_merge(base, override)
        assert result["run_hooks"]["on_run_start"] == [{"command": "project.sh"}]

    def test_global_only_run_hooks_used_as_is(self):
        """Global-only run_hooks are used as-is when project has none."""
        base = {
            "run_hooks": {
                "on_run_start": [{"command": "global.sh"}],
                "on_run_end": [],
            }
        }
        override = {}
        result = _deep_merge(base, override)
        assert result["run_hooks"]["on_run_start"] == [{"command": "global.sh"}]

    def test_on_run_start_order_preserved(self):
        """Concatenation preserves global-first, project-second ordering."""
        base = {
            "run_hooks": {
                "on_run_start": [
                    {"command": "global1.sh"},
                    {"command": "global2.sh"},
                ],
                "on_run_end": [],
            }
        }
        override = {
            "run_hooks": {
                "on_run_start": [{"command": "project.sh"}],
                "on_run_end": [],
            }
        }
        result = _deep_merge(base, override)
        commands = [e["command"] for e in result["run_hooks"]["on_run_start"]]
        assert commands == ["global1.sh", "global2.sh", "project.sh"]


class TestRejectRunScopeKeys:
    """T003: reject_run_scope_keys validators on HookConfig and StateHookConfig."""

    def test_hook_config_rejects_on_run_start(self):
        """HookConfig raises ValidationError when on_run_start is provided."""
        with pytest.raises(ValidationError, match="global or project configuration"):
            HookConfig(on_run_start=[HookEntry(command="x")])

    def test_hook_config_rejects_on_run_end(self):
        """HookConfig raises ValidationError when on_run_end is provided."""
        with pytest.raises(ValidationError, match="global or project configuration"):
            HookConfig(on_run_end=[HookEntry(command="x")])

    def test_hook_config_rejects_on_run_start_from_dict(self):
        """HookConfig rejects on_run_start when passed as dict."""
        with pytest.raises(ValidationError, match="global or project configuration"):
            HookConfig.model_validate({"on_run_start": [{"command": "x"}]})

    def test_hook_config_rejects_on_run_end_from_dict(self):
        """HookConfig rejects on_run_end when passed as dict."""
        with pytest.raises(ValidationError, match="global or project configuration"):
            HookConfig.model_validate({"on_run_end": [{"command": "x"}]})

    def test_state_hook_config_rejects_on_run_start(self):
        """StateHookConfig raises ValidationError when on_run_start is provided."""
        with pytest.raises(ValidationError, match="global or project configuration"):
            StateHookConfig(on_run_start=[HookEntry(command="x")])

    def test_state_hook_config_rejects_on_run_end(self):
        """StateHookConfig raises ValidationError when on_run_end is provided."""
        with pytest.raises(ValidationError, match="global or project configuration"):
            StateHookConfig(on_run_end=[HookEntry(command="x")])

    def test_hook_config_valid_keys_unaffected(self):
        """HookConfig(on_state_start=..., on_workflow_start=...) still validates fine."""
        config = HookConfig(
            on_state_start=[HookEntry(command="state-start.sh")],
            on_workflow_start=[HookEntry(command="workflow-start.sh")],
        )
        assert len(config.on_state_start) == 1
        assert len(config.on_workflow_start) == 1

    def test_state_hook_config_valid_keys_unaffected(self):
        """StateHookConfig(on_state_start=...) still validates fine."""
        config = StateHookConfig(on_state_start=[HookEntry(command="state-start.sh")])
        assert len(config.on_state_start) == 1

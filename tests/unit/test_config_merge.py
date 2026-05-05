"""Unit tests for _deep_merge full-replace behavior for retry_escalation and extraction_fallback.

T003: Project-scope override of global default retry escalation.

These tests verify that when _deep_merge encounters a key in _FULL_REPLACE_KEYS
(retry_escalation, extraction_fallback), the override value fully replaces the
base value — no field-by-field merging. Tests fail until _FULL_REPLACE_KEYS is
added to config.py and the guard is inserted in _deep_merge.
"""

from __future__ import annotations

from fdsx.core.config import _deep_merge


class TestFullReplaceKeys:
    def test_retry_escalation_override_excludes_provider_options(self):
        """Global retry_escalation.provider_options must not leak into project override."""
        base = {
            "retry_escalation": {
                "provider": "claude",
                "model": "claude-opus-4-7",
                "provider_options": {"x": 1},
            }
        }
        override = {
            "retry_escalation": {
                "provider": "codex",
                "model": "gpt-4o",
            }
        }
        result = _deep_merge(base, override)
        assert result["retry_escalation"] == {"provider": "codex", "model": "gpt-4o"}
        assert "provider_options" not in result["retry_escalation"]

    def test_extraction_fallback_override_excludes_extra_fields(self):
        """Global extraction_fallback.extra_instructions must not leak into project override."""
        base = {
            "extraction_fallback": {
                "provider": "claude",
                "extra_instructions": "be concise",
            }
        }
        override = {
            "extraction_fallback": {
                "provider": "codex",
            }
        }
        result = _deep_merge(base, override)
        assert result["extraction_fallback"] == {"provider": "codex"}
        assert "extra_instructions" not in result["extraction_fallback"]

    def test_providers_key_still_deep_merges_field_by_field(self):
        """Regression: providers (not in _FULL_REPLACE_KEYS) merges field-by-field."""
        base = {"providers": {"claude": {"permission_mode": "bypassPermissions"}}}
        override = {"providers": {"claude": {"dangerously_skip_permissions": True}}}
        result = _deep_merge(base, override)
        assert result["providers"]["claude"]["permission_mode"] == "bypassPermissions"
        assert result["providers"]["claude"]["dangerously_skip_permissions"] is True

    def test_hooks_key_still_concatenates_lists(self):
        """Regression: hooks (not in _FULL_REPLACE_KEYS) still deep-merges and concatenates hook lists."""
        base = {"hooks": {"on_state_start": [{"command": "a.sh"}]}}
        override = {"hooks": {"on_state_start": [{"command": "b.sh"}]}}
        result = _deep_merge(base, override)
        commands = [e["command"] for e in result["hooks"]["on_state_start"]]
        assert commands == ["a.sh", "b.sh"]

    def test_only_global_has_retry_escalation_passes_through(self):
        """When only global has retry_escalation, project omits it — global value is preserved."""
        base = {
            "retry_escalation": {
                "provider": "claude",
                "model": "claude-opus-4-7",
                "provider_options": {"x": 1},
            }
        }
        override = {}
        result = _deep_merge(base, override)
        assert result["retry_escalation"]["provider"] == "claude"
        assert result["retry_escalation"]["provider_options"] == {"x": 1}

    def test_only_project_has_retry_escalation_appears_in_output(self):
        """When only project has retry_escalation, global omits it — project value appears."""
        base = {}
        override = {
            "retry_escalation": {
                "provider": "codex",
                "model": "gpt-4o",
            }
        }
        result = _deep_merge(base, override)
        assert result["retry_escalation"] == {"provider": "codex", "model": "gpt-4o"}

"""Unit tests for retry_escalation profile resolution (T001).

Tests exercise resolve_profiles_in_flow's handling of the flow-level
retry_escalation field. These fail until _resolve_escalation_profile
is wired into resolve_profiles_in_flow in core/profiles.py.
"""

from fdsx.core.profiles import resolve_profiles_in_flow


def _make_flow(retry_escalation, profiles=None):
    """Minimal flow dict with retry_escalation and a no-op system task."""
    data = {
        "name": "Escalation Test",
        "description": "Test escalation resolution",
        "start_at": "task1",
        "states": {
            "task1": {
                "type": "task",
                "provider": "system",
                "command": "echo hi",
                "result_path": "$.output",
                "end": True,
            }
        },
        "retry_escalation": retry_escalation,
    }
    if profiles is not None:
        data["profiles"] = profiles
    return data


class TestRetryEscalationProfileResolution:
    def test_profile_resolves_to_provider_model(self):
        """retry_escalation.profile is expanded to provider+model in-place."""
        data = _make_flow(
            {"profile": "fast"},
            profiles={"fast": {"provider": "codex", "model": "gpt-4o"}},
        )
        data, errors = resolve_profiles_in_flow(data)
        assert errors == []
        esc = data["retry_escalation"]
        assert esc["provider"] == "codex"
        assert esc["model"] == "gpt-4o"
        assert "profile" not in esc

    def test_extra_profile_fields_become_provider_options(self):
        """Extra fields in the profile are moved into provider_options."""
        data = _make_flow(
            {"profile": "smart"},
            profiles={
                "smart": {
                    "provider": "codex",
                    "model": "gpt-4o",
                    "system_prompt": "be sharp",
                }
            },
        )
        data, errors = resolve_profiles_in_flow(data)
        assert errors == []
        assert data["retry_escalation"]["provider_options"] == {
            "system_prompt": "be sharp"
        }

    def test_unknown_profile_error_mentions_retry_escalation(self):
        """Error for unknown profile references 'retry_escalation' in message."""
        data = _make_flow({"profile": "no-such-profile"})
        _data, errors = resolve_profiles_in_flow(data)
        assert len(errors) >= 1
        assert any("retry_escalation" in e for e in errors)

    def test_flow_profiles_override_config_profiles(self):
        """flow.profiles wins over config-level profiles for retry_escalation."""
        data = _make_flow(
            {"profile": "fast"},
            profiles={"fast": {"provider": "opencode", "model": "o4-mini"}},
        )
        config_profiles = {"fast": {"provider": "claude", "model": "haiku"}}
        data, errors = resolve_profiles_in_flow(data, config_profiles)
        assert errors == []
        assert data["retry_escalation"]["provider"] == "opencode"

    def test_provider_model_shape_left_untouched(self):
        """retry_escalation with explicit provider/model is not mutated."""
        data = _make_flow({"provider": "claude", "model": "claude-3-haiku"})
        original = dict(data["retry_escalation"])
        data, errors = resolve_profiles_in_flow(data)
        assert errors == []
        assert data["retry_escalation"] == original

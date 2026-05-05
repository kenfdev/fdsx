"""Unit tests for EscalationConfig model validation (T001 retry escalation).

All tests fail with ImportError until EscalationConfig is added to
fdsx.models.flow — that ImportError is the expected RED signal.
"""

import pytest
from pydantic import ValidationError

from fdsx.core.config import FdsxConfig
from fdsx.models.flow import EscalationConfig, Flow


class TestEscalationConfigXOR:
    """EscalationConfig requires explicit provider+model; profile field is forbidden."""

    def test_profile_field_is_rejected_as_extra(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            EscalationConfig(profile="p")

    def test_both_profile_and_provider_raises(self):
        with pytest.raises(ValidationError):
            EscalationConfig(profile="p", provider="claude", model="m")

    def test_provider_without_model_raises(self):
        with pytest.raises(ValidationError):
            EscalationConfig(provider="claude")

    def test_provider_and_model_is_valid(self):
        cfg = EscalationConfig(provider="claude", model="claude-3")
        assert cfg.provider == "claude"
        assert cfg.model == "claude-3"

    def test_provider_model_with_provider_options_is_valid(self):
        cfg = EscalationConfig(
            provider="claude", model="claude-3", provider_options={"k": "v"}
        )
        assert cfg.provider_options == {"k": "v"}

    def test_unknown_provider_raises(self):
        with pytest.raises(ValidationError):
            EscalationConfig(provider="unknown-xyz", model="m")

    def test_system_provider_raises(self):
        """system is not a valid LLM escalation target."""
        with pytest.raises(ValidationError):
            EscalationConfig(provider="system", model="m")


class TestFdsxConfigRetryEscalation:
    """FdsxConfig.retry_escalation accepts EscalationConfig and validates it (T002)."""

    def test_round_trips_without_error(self):
        cfg = FdsxConfig(
            retry_escalation=EscalationConfig(provider="claude", model="m")
        )
        assert cfg.retry_escalation is not None
        assert cfg.retry_escalation.provider == "claude"
        assert cfg.retry_escalation.model == "m"

    def test_model_validate_from_dict(self):
        cfg = FdsxConfig.model_validate(
            {"retry_escalation": {"provider": "claude", "model": "m"}}
        )
        assert cfg.retry_escalation is not None
        assert cfg.retry_escalation.provider == "claude"

    def test_missing_model_raises(self):
        with pytest.raises(ValidationError, match="model"):
            FdsxConfig(retry_escalation={"provider": "claude"})

    def test_system_provider_raises(self):
        with pytest.raises(ValidationError, match="system"):
            FdsxConfig(retry_escalation={"provider": "system", "model": "m"})

    def test_extra_key_in_nested_config_raises(self):
        with pytest.raises(ValidationError, match="bogus"):
            FdsxConfig(
                retry_escalation={"provider": "claude", "model": "m", "bogus": 1}
            )


def test_flow_retry_escalation_false_literal():
    """Flow accepts retry_escalation: false (opt-out sentinel) without error."""
    flow = Flow.model_validate(
        {
            "name": "opt-out-test",
            "description": "Test opt-out sentinel",
            "start_at": "step1",
            "states": {
                "step1": {
                    "type": "task",
                    "provider": "system",
                    "command": "echo done",
                    "result_path": "$.result",
                    "end": True,
                }
            },
            "retry_escalation": False,
        }
    )
    assert flow.retry_escalation is False

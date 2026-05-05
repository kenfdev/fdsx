"""Unit tests for EscalationConfig model validation (T001 retry escalation).

All tests fail with ImportError until EscalationConfig is added to
fdsx.models.flow — that ImportError is the expected RED signal.
"""

import pytest
from pydantic import ValidationError

from fdsx.models.flow import EscalationConfig


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

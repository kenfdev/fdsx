"""Unit tests for CursorOptions model (T004)."""

import pytest
from fdsx.providers.cursor import CursorOptions
from pydantic import ValidationError


class TestCursorOptions:
    """T004: Tests for CursorOptions model."""

    def test_defaults_emit_no_flags(self):
        """Default CursorOptions produces an empty flags list."""
        assert CursorOptions().to_cli_flags() == []

    def test_force_true_emits_force(self):
        """force=True maps to --force."""
        assert CursorOptions(force=True).to_cli_flags() == ["--force"]

    def test_sandbox_enabled(self):
        """sandbox='enabled' maps to ['--sandbox', 'enabled']."""
        assert CursorOptions(sandbox="enabled").to_cli_flags() == [
            "--sandbox",
            "enabled",
        ]

    def test_sandbox_disabled(self):
        """sandbox='disabled' maps to ['--sandbox', 'disabled']."""
        assert CursorOptions(sandbox="disabled").to_cli_flags() == [
            "--sandbox",
            "disabled",
        ]

    def test_sandbox_none_emits_no_flag(self):
        """sandbox=None emits no --sandbox flag."""
        flags = CursorOptions(sandbox=None).to_cli_flags()
        assert "--sandbox" not in flags

    def test_sandbox_invalid_literal_rejected(self):
        """Invalid sandbox value raises ValidationError."""
        with pytest.raises(ValidationError):
            CursorOptions(sandbox="bad")  # type: ignore[arg-type]

    def test_approve_mcps_true_emits_flag(self):
        """approve_mcps=True maps to ['--approve-mcps']."""
        assert CursorOptions(approve_mcps=True).to_cli_flags() == ["--approve-mcps"]

    def test_approve_mcps_default_false_no_flag(self):
        """approve_mcps defaults to False; '--approve-mcps' not in default flags."""
        assert "--approve-mcps" not in CursorOptions().to_cli_flags()

    def test_inactivity_timeout_not_in_cli_flags(self):
        """inactivity_timeout is never emitted as a CLI flag."""
        flags = CursorOptions(inactivity_timeout=30).to_cli_flags()
        assert not any("inactivity" in f for f in flags)
        assert not any("timeout" in f for f in flags)

    def test_extra_fields_rejected(self):
        """Extra fields must be rejected (extra='forbid')."""
        with pytest.raises(ValidationError):
            CursorOptions(unknown="x")  # type: ignore[call-arg]

    def test_yolo_field_does_not_exist(self):
        """CursorOptions has no 'yolo' field (regression guard)."""
        assert not hasattr(CursorOptions(), "yolo")

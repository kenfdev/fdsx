"""Unit tests for GeminiOptions model (T001, T002)."""

import pytest
from pydantic import ValidationError

from fdsx.providers.gemini import GeminiOptions


class TestGeminiOptions:
    """T001: Tests for GeminiOptions model."""

    def test_approval_mode_valid_literals(self):
        """Valid approval_mode literals must be accepted."""
        for mode in ("default", "auto_edit", "yolo", "plan"):
            opts = GeminiOptions(approval_mode=mode)
            assert opts.approval_mode == mode

    def test_approval_mode_invalid(self):
        """Invalid approval_mode must raise ValidationError."""
        with pytest.raises(ValidationError):
            GeminiOptions(approval_mode="bad")

    def test_approval_mode(self):
        """approval_mode="plan" maps to --approval-mode plan."""
        opts = GeminiOptions(approval_mode="plan")
        assert opts.to_cli_flags() == ["--approval-mode", "plan"]

    def test_yolo(self):
        """yolo=True maps to --yolo."""
        opts = GeminiOptions(yolo=True)
        assert opts.to_cli_flags() == ["--yolo"]

    def test_yolo_overrides_approval_mode(self):
        """yolo=True silently overrides approval_mode."""
        opts = GeminiOptions(yolo=True, approval_mode="plan")
        assert opts.to_cli_flags() == ["--yolo"]

    def test_sandbox(self):
        """sandbox=True maps to --sandbox."""
        opts = GeminiOptions(sandbox=True)
        assert opts.to_cli_flags() == ["--sandbox"]

    def test_include_directories_comma_separated(self):
        """include_directories=["a", "b"] maps to --include-directories a,b."""
        opts = GeminiOptions(include_directories=["a", "b"])
        assert opts.to_cli_flags() == ["--include-directories", "a,b"]

    def test_extensions_comma_separated(self):
        """extensions=["ext1"] maps to --extensions ext1."""
        opts = GeminiOptions(extensions=["ext1"])
        assert opts.to_cli_flags() == ["--extensions", "ext1"]

    def test_policy_repeated_flags(self):
        """policy=["p1.txt", "p2.txt"] maps to repeated --policy flags."""
        opts = GeminiOptions(policy=["p1.txt", "p2.txt"])
        assert opts.to_cli_flags() == ["--policy", "p1.txt", "--policy", "p2.txt"]

    def test_defaults_empty(self):
        """All defaults produce an empty flags list."""
        opts = GeminiOptions()
        assert opts.to_cli_flags() == []

    def test_extra_fields_rejected(self):
        """Extra fields must be rejected."""
        with pytest.raises(ValidationError):
            GeminiOptions(unknown_field="x")  # type: ignore[call-arg]

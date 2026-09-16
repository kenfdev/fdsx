"""Unit tests for the pi provider options and factory registration."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from pydantic import ValidationError

from fdsx.providers.base import get_provider


def _pi_symbol(name: str) -> Any:
    try:
        module = importlib.import_module("fdsx.providers.pi")
    except ModuleNotFoundError as exc:
        if exc.name == "fdsx.providers.pi":
            pytest.fail("fdsx.providers.pi module is not implemented")
        raise
    try:
        return getattr(module, name)
    except AttributeError:
        pytest.fail(f"fdsx.providers.pi.{name} is not implemented")


class TestPiOptions:
    """Tests for PiOptions model."""

    def test_defaults_emit_no_cli_flags(self) -> None:
        """PiOptions.to_cli_flags() returns an empty list."""
        pi_options = _pi_symbol("PiOptions")

        assert pi_options().to_cli_flags() == []

    def test_unknown_option_key_is_rejected(self) -> None:
        """PiOptions rejects unknown option keys."""
        pi_options = _pi_symbol("PiOptions")

        with pytest.raises(ValidationError):
            pi_options(unknown_option=True)

    def test_inactivity_timeout_is_accepted_but_not_emitted_as_cli_flags(self) -> None:
        """PiOptions accepts inactivity_timeout without emitting CLI flags."""
        pi_options = _pi_symbol("PiOptions")

        flags = pi_options(inactivity_timeout=10).to_cli_flags()

        assert flags == []

    def test_allowed_tools_emit_tools_cli_flag(self) -> None:
        """allowed_tools emits pi's --tools flag with comma-separated tools."""
        pi_options = _pi_symbol("PiOptions")

        flags = pi_options(allowed_tools=["read", "bash"]).to_cli_flags()

        assert flags == ["--tools", "read,bash"]

    def test_disallowed_tools_emit_exclude_tools_cli_flag(self) -> None:
        """disallowed_tools emits pi's --exclude-tools flag."""
        pi_options = _pi_symbol("PiOptions")

        flags = pi_options(disallowed_tools=["write", "edit"]).to_cli_flags()

        assert flags == ["--exclude-tools", "write,edit"]

    def test_disable_tools_emits_no_tools_cli_flag(self) -> None:
        """disable_tools emits pi's --no-tools flag."""
        pi_options = _pi_symbol("PiOptions")

        flags = pi_options(disable_tools=True).to_cli_flags()

        assert flags == ["--no-tools"]

    def test_empty_tool_lists_emit_no_cli_flags(self) -> None:
        """Empty allowed/disallowed tool lists do not emit pi tool flags."""
        pi_options = _pi_symbol("PiOptions")

        flags = pi_options(allowed_tools=[], disallowed_tools=[]).to_cli_flags()

        assert flags == []

    def test_allowed_and_disallowed_tools_emit_flags_in_stable_order(self) -> None:
        """Allow and exclude flags can be combined in stable CLI order."""
        pi_options = _pi_symbol("PiOptions")

        flags = pi_options(
            allowed_tools=["read", "bash"],
            disallowed_tools=["write", "edit"],
        ).to_cli_flags()

        assert flags == [
            "--tools",
            "read,bash",
            "--exclude-tools",
            "write,edit",
        ]

    def test_disable_tools_rejects_allowed_tools(self) -> None:
        """disable_tools cannot be combined with allowed_tools."""
        pi_options = _pi_symbol("PiOptions")

        with pytest.raises(ValidationError) as exc_info:
            pi_options(disable_tools=True, allowed_tools=["read"])

        message = str(exc_info.value)
        assert "disable_tools" in message
        assert "allowed_tools" in message
        assert "cannot be combined" in message

    def test_disable_tools_rejects_disallowed_tools(self) -> None:
        """disable_tools cannot be combined with disallowed_tools."""
        pi_options = _pi_symbol("PiOptions")

        with pytest.raises(ValidationError) as exc_info:
            pi_options(disable_tools=True, disallowed_tools=["write"])

        message = str(exc_info.value)
        assert "disable_tools" in message
        assert "disallowed_tools" in message
        assert "cannot be combined" in message


class TestPiProviderFactory:
    """Tests for get_provider('pi') factory behavior."""

    def test_get_provider_pi_returns_pi_provider(self) -> None:
        """get_provider('pi') returns a PiProvider."""
        pi_provider = _pi_symbol("PiProvider")

        provider = get_provider("pi")

        assert isinstance(provider, pi_provider)

    def test_get_provider_pi_validates_and_stores_typed_options(self) -> None:
        """get_provider('pi', options) validates through PiOptions and stores it."""
        pi_options = _pi_symbol("PiOptions")

        provider = get_provider("pi", {"inactivity_timeout": 10})

        assert provider.options == pi_options(inactivity_timeout=10)

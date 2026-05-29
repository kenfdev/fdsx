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

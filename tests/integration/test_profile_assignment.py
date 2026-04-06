"""Integration tests for assign_profiles in src/fdsx/cli/init_interactive.py.

Mocks _input (the module-level input wrapper) and _console to test behavioral
logic of assign_profiles.
"""

from unittest.mock import MagicMock, patch

from fdsx.cli.init_interactive import (
    _console,
    assign_profiles,
)
from fdsx.models.init import ProviderSelection


def _mock_console() -> MagicMock:
    return MagicMock(spec=type(_console))


def _patch_console(mocker: MagicMock):
    return patch("fdsx.cli.init_interactive._console", mocker)


class TestAssignProfiles:
    def test_single_provider_auto_fills_all_profiles(self):
        """Single provider selection auto-fills all 5 profiles without prompting."""
        selection = ProviderSelection(provider="claude", model="claude-sonnet-4-6")
        with (
            patch(
                "fdsx.cli.init_interactive._input", return_value="should-not-be-called"
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = assign_profiles([selection])

        assert result == {
            "smarty": selection,
            "doer": selection,
            "specialist": selection,
            "generalist": selection,
            "behemoth": selection,
        }
        assert mock_input.call_count == 0

    def test_multiple_providers_prompts_per_profile(self):
        """Multiple providers prompt for each of the 5 profiles."""
        selections = [
            ProviderSelection(provider="claude", model="claude-sonnet-4-6"),
            ProviderSelection(provider="codex", model="o3"),
        ]
        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["1", "2", "1", "2", "1"],
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = assign_profiles(selections)

        assert result["smarty"] == selections[0]
        assert result["doer"] == selections[1]
        assert result["specialist"] == selections[0]
        assert result["generalist"] == selections[1]
        assert result["behemoth"] == selections[0]
        assert mock_input.call_count == 5

    def test_invalid_input_retries_then_succeeds(self):
        """Invalid input triggers retry; valid input on second attempt succeeds."""
        selections = [
            ProviderSelection(provider="claude", model="claude-sonnet-4-6"),
            ProviderSelection(provider="codex", model="o3"),
        ]
        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["abc", "1", "1", "1", "1", "1"],
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = assign_profiles(selections)

        assert result["smarty"] == selections[0]
        assert mock_input.call_count == 6

    def test_out_of_range_retries(self):
        """Out-of-range number triggers retry; valid input on second attempt succeeds."""
        selections = [
            ProviderSelection(provider="claude", model="claude-sonnet-4-6"),
            ProviderSelection(provider="codex", model="o3"),
        ]
        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["99", "1", "1", "1", "1", "1"],
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = assign_profiles(selections)

        assert result["smarty"] == selections[0]
        assert mock_input.call_count == 6

    def test_empty_input_retries(self):
        """Empty input triggers retry; valid input on second attempt succeeds."""
        selections = [
            ProviderSelection(provider="claude", model="claude-sonnet-4-6"),
            ProviderSelection(provider="codex", model="o3"),
        ]
        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["", "1", "1", "1", "1", "1"],
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = assign_profiles(selections)

        assert result["smarty"] == selections[0]
        assert mock_input.call_count == 6

    def test_three_providers_five_profiles(self):
        """Three providers with five profiles - last two profiles reuse available options."""
        selections = [
            ProviderSelection(provider="claude", model="claude-sonnet-4-6"),
            ProviderSelection(provider="codex", model="o3"),
            ProviderSelection(provider="gemini", model="gemini-2.5-pro"),
        ]
        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["1", "2", "3", "1", "2"],
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = assign_profiles(selections)

        assert result["smarty"] == selections[0]
        assert result["doer"] == selections[1]
        assert result["specialist"] == selections[2]
        assert result["generalist"] == selections[0]
        assert result["behemoth"] == selections[1]
        assert mock_input.call_count == 5

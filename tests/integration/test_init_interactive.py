"""Integration tests for interactive UI functions in src/fdsx/cli/init_interactive.py.

Mocks _input (the module-level input wrapper) and _console to test behavioral
logic of select_providers, select_models, select_templates,
confirm_existing_project, and confirm_overwrite.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fdsx.cli.init_interactive import (
    _console,
    confirm_existing_project,
    confirm_overwrite,
    select_models,
    select_providers,
    select_templates,
)
from fdsx.models.init import ProviderSelection, TemplateInfo


def _mock_console() -> MagicMock:
    return MagicMock(spec=type(_console))


def _patch_console(mocker: MagicMock):
    return patch("fdsx.cli.init_interactive._console", mocker)


class TestSelectProviders:
    def test_valid_single_selection(self):
        """Input '1' returns the first provider from sorted VALID_PROVIDERS."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="1"),
            _patch_console(_mock_console()),
        ):
            result = select_providers()
        assert result == ["claude"]

    def test_valid_multi_selection(self):
        """Input '1,3' returns 1st and 3rd providers."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="1,3"),
            _patch_console(_mock_console()),
        ):
            result = select_providers()
        assert result == ["claude", "gemini"]

    def test_empty_input_retries_then_succeeds(self):
        """Empty input triggers retry; '1' on second attempt returns first provider."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["", "1"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_providers()
        assert result == ["claude"]
        assert mock_input.call_count == 2

    def test_out_of_range_retries(self):
        """Out-of-range number triggers retry; '1' on third attempt succeeds."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["0", "5", "1"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_providers()
        assert result == ["claude"]
        assert mock_input.call_count == 3

    def test_non_numeric_input_retries(self):
        """Non-numeric input triggers retry; '1' on second attempt succeeds."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["abc", "1"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_providers()
        assert result == ["claude"]
        assert mock_input.call_count == 2

    def test_duplicate_selection_retries(self):
        """Duplicate indices trigger retry; '1,2' on second attempt succeeds."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["1,1", "1,2"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_providers()
        assert result == ["claude", "codex"]
        assert mock_input.call_count == 2


class TestSelectModels:
    def test_preset_selection_by_number(self):
        """Provider 'claude' with input '1' returns claude-sonnet-4-6."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="1"),
            _patch_console(_mock_console()),
        ):
            result = select_models(["claude"])
        assert result == [
            ProviderSelection(provider="claude", model="claude-sonnet-4-6")
        ]

    def test_custom_model_by_name(self):
        """Non-numeric input is returned as the custom model name."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="my-custom-model"),
            _patch_console(_mock_console()),
        ):
            result = select_models(["claude"])
        assert result == [ProviderSelection(provider="claude", model="my-custom-model")]

    def test_empty_input_retries(self):
        """Empty input triggers retry; '1' on second attempt succeeds."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["", "1"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_models(["claude"])
        assert result == [
            ProviderSelection(provider="claude", model="claude-sonnet-4-6")
        ]
        assert mock_input.call_count == 2

    def test_out_of_range_number_retries(self):
        """Out-of-range number triggers retry; '1' on second attempt succeeds."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["99", "1"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_models(["claude"])
        assert result == [
            ProviderSelection(provider="claude", model="claude-sonnet-4-6")
        ]
        assert mock_input.call_count == 2

    def test_provider_without_presets_prompts_free_text(self):
        """Provider without presets (opencode) prompts for free-text model name."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="my-model"),
            _patch_console(_mock_console()),
        ):
            result = select_models(["opencode"])
        assert result == [ProviderSelection(provider="opencode", model="my-model")]

    def test_multiple_providers_sequential_inputs(self):
        """Sequential inputs for each provider are correctly mapped."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["1", "2"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_models(["claude", "codex"])
        assert result == [
            ProviderSelection(provider="claude", model="claude-sonnet-4-6"),
            ProviderSelection(provider="codex", model="o3"),
        ]
        assert mock_input.call_count == 2


class TestSelectTemplates:
    @pytest.fixture
    def template_fixtures(self, tmp_path: Path) -> list[TemplateInfo]:
        return [
            TemplateInfo(
                name="linear-basic", path=tmp_path / "linear", source="builtin"
            ),
            TemplateInfo(
                name="parallel-basic", path=tmp_path / "parallel", source="builtin"
            ),
            TemplateInfo(
                name="plan-implement-review", path=tmp_path / "pir", source="builtin"
            ),
        ]

    def test_valid_selection(self, template_fixtures: list[TemplateInfo]):
        """Input '1' returns the first template."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="1"),
            _patch_console(_mock_console()),
        ):
            result = select_templates(template_fixtures)
        assert result == [template_fixtures[0]]

    def test_multi_selection(self, template_fixtures: list[TemplateInfo]):
        """Input '1,2' returns first two templates."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="1,2"),
            _patch_console(_mock_console()),
        ):
            result = select_templates(template_fixtures)
        assert result == [template_fixtures[0], template_fixtures[1]]

    def test_empty_input_returns_empty_list(
        self, template_fixtures: list[TemplateInfo]
    ):
        """Empty input (Enter for none) returns an empty list."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value=""),
            _patch_console(_mock_console()),
        ):
            result = select_templates(template_fixtures)
        assert result == []

    def test_empty_available_list_returns_immediately(self):
        """Empty available list returns [] without prompting."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["should-not-be-called"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_templates([])
        assert result == []
        assert mock_input.call_count == 0

    def test_out_of_range_retries(self, template_fixtures: list[TemplateInfo]):
        """Out-of-range input triggers retry; '1' on second attempt succeeds."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["99", "1"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_templates(template_fixtures)
        assert result == [template_fixtures[0]]
        assert mock_input.call_count == 2

    def test_duplicate_retries(self, template_fixtures: list[TemplateInfo]):
        """Duplicate selections trigger retry; '1' on second attempt succeeds."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["1,1", "1"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_templates(template_fixtures)
        assert result == [template_fixtures[0]]
        assert mock_input.call_count == 2

    def test_non_numeric_retries(self, template_fixtures: list[TemplateInfo]):
        """Non-numeric input triggers retry; '1' on second attempt succeeds."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["abc", "1"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = select_templates(template_fixtures)
        assert result == [template_fixtures[0]]
        assert mock_input.call_count == 2


class TestConfirmExistingProject:
    def test_yes_returns_true(self):
        """'y' input returns True."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="y"),
            _patch_console(_mock_console()),
        ):
            assert confirm_existing_project() is True

    def test_yes_variant_returns_true(self):
        """'yes' input returns True."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="yes"),
            _patch_console(_mock_console()),
        ):
            assert confirm_existing_project() is True

    def test_no_returns_false(self):
        """'n' input returns False."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="n"),
            _patch_console(_mock_console()),
        ):
            assert confirm_existing_project() is False

    def test_no_variant_returns_false(self):
        """'no' input returns False."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="no"),
            _patch_console(_mock_console()),
        ):
            assert confirm_existing_project() is False

    def test_invalid_then_valid_retries(self):
        """Invalid input 'maybe' triggers retry; 'y' on second attempt returns True."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["maybe", "y"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = confirm_existing_project()
        assert result is True
        assert mock_input.call_count == 2


class TestConfirmOverwrite:
    def test_yes_returns_true(self):
        """'y' input returns True."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="y"),
            _patch_console(_mock_console()),
        ):
            assert confirm_overwrite("my-workflow") is True

    def test_no_returns_false(self):
        """'n' input returns False."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="n"),
            _patch_console(_mock_console()),
        ):
            assert confirm_overwrite("my-workflow") is False

    def test_invalid_then_valid_retries(self):
        """Invalid input triggers retry; 'n' on second attempt returns False."""
        with (
            patch(
                "fdsx.cli.init_interactive._input", side_effect=["maybe", "n"]
            ) as mock_input,
            _patch_console(_mock_console()),
        ):
            result = confirm_overwrite("my-workflow")
        assert result is False
        assert mock_input.call_count == 2

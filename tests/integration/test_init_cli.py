"""Integration tests for fdsx init CLI command (T009)."""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from fdsx.cli import main
from fdsx.models.init import (
    ProviderSelection,
    ScaffoldResult,
    TemplateInfo,
)

runner = CliRunner()

FAKE_TEMPLATES = [
    TemplateInfo(
        name="linear-basic", path=Path("/fake/linear-basic"), source="builtin"
    ),
]
FAKE_PROVIDERS = ["claude"]
FAKE_SELECTIONS = [ProviderSelection(provider="claude", model="sonnet")]
FAKE_RESULT = ScaffoldResult(
    created=[".fdsx/config.yaml", ".fdsx/workflows/linear-basic/workflow.yaml"],
    skipped_config=False,
    skipped_workflows=[],
)


class TestInitNonTTY:
    def test_non_tty_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("fdsx.cli.main.sys") as mock_sys:
            mock_sys.stdin.isatty.return_value = False
            result = runner.invoke(main.app, ["init"])
        assert result.exit_code == 2
        assert "interactive terminal" in result.output


class TestInitHappyPath:
    def test_successful_init(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.select_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT) as mock_scaffold,
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        mock_scaffold.assert_called_once()
        assert "Initialized .fdsx/ directory" in result.output
        assert "config.yaml" in result.output
        assert "Next steps" in result.output

    def test_no_templates_selected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.select_templates", return_value=[]),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT) as mock_scaffold,
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        mock_scaffold.assert_called_once()


class TestInitKeyboardInterrupt:
    def test_keyboard_interrupt_exits_130(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", side_effect=KeyboardInterrupt),
            patch("fdsx.cli.main.needs_init", return_value=True),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"])
        assert result.exit_code == 130


class TestInitExistingProject:
    def test_existing_project_declined(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.needs_init", return_value=False),
            patch("fdsx.cli.main.confirm_existing_project", return_value=False),
            patch("fdsx.cli.main.ensure_gitignore"),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0

    def test_existing_project_confirmed_proceeds(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.needs_init", return_value=False),
            patch("fdsx.cli.main.confirm_existing_project", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.select_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT) as mock_scaffold,
            patch("fdsx.cli.main.ensure_gitignore"),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        mock_scaffold.assert_called_once()


class TestInitConflicts:
    def test_conflicts_with_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.select_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.check_conflicts", return_value=["linear-basic"]),
            patch("fdsx.cli.main.confirm_overwrite", return_value=True),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT) as mock_scaffold,
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        call_args = mock_scaffold.call_args
        assert "linear-basic" in call_args[0][2]  # allow_overwrite set

    def test_conflicts_declined_skips_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.select_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.check_conflicts", return_value=["linear-basic"]),
            patch("fdsx.cli.main.confirm_overwrite", return_value=False),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT) as mock_scaffold,
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        call_args = mock_scaffold.call_args
        assert call_args[0][2] == set()  # allow_overwrite empty

    def test_multiple_conflicts_mixed_decisions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        extra_template = TemplateInfo(
            name="parallel-basic", path=Path("/fake/parallel-basic"), source="builtin"
        )
        all_templates = [*FAKE_TEMPLATES, extra_template]
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=all_templates),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.select_templates", return_value=all_templates),
            patch(
                "fdsx.cli.main.check_conflicts",
                return_value=["linear-basic", "parallel-basic"],
            ),
            patch("fdsx.cli.main.confirm_overwrite", side_effect=[True, False]),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT) as mock_scaffold,
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        call_args = mock_scaffold.call_args
        assert call_args[0][2] == {"linear-basic"}  # only first approved


class TestInitOutputFormat:
    def test_skipped_config_shown(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result_with_skip = ScaffoldResult(
            created=[".fdsx/workflows/linear-basic/workflow.yaml"],
            skipped_config=True,
            skipped_workflows=[],
        )
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.select_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main.scaffold", return_value=result_with_skip),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "preserved" in result.output

    def test_skipped_workflows_shown(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result_with_skipped = ScaffoldResult(
            created=[".fdsx/config.yaml"],
            skipped_config=False,
            skipped_workflows=["linear-basic", "parallel-basic"],
        )
        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.select_templates", return_value=FAKE_TEMPLATES),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main.scaffold", return_value=result_with_skipped),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Skipped (already exist)" in result.output
        assert "linear-basic" in result.output
        assert "parallel-basic" in result.output

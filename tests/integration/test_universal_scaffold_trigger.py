"""Integration tests for universal scaffold trigger (FR-2).

Tests verify that guide message is shown for operational commands in interactive mode
when .fdsx/ does not exist. Exempt commands (--version, bare invocation, validate,
and any subcommand with --help) skip the guide message.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fdsx.cli import main


class TestUniversalScaffoldTrigger:
    """Tests for universal scaffold trigger behavior."""

    def test_version_flag_skips_guide_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--version is exempt: does not show guide message in uninitialized dir."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive", "--version"])
        assert result.exit_code == 0
        assert "No .fdsx/ directory found" not in result.output

    def test_bare_fdsx_skips_guide_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bare fdsx (no args) is exempt: does not show guide message in uninitialized dir."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive"])
        assert result.exit_code == 0
        assert "No .fdsx/ directory found" not in result.output

    def test_operational_subcommand_shows_guide_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-exempt operational subcommand in uninitialized dir shows guide message."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive", "list"])
        assert result.exit_code == 0
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init'" in result.output

    def test_ci_mode_shows_guide_for_operational_command_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--ci mode shows guide message for operational commands when .fdsx/ is missing."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--ci", "list"])
        assert result.exit_code == 0
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init'" in result.output

    def test_existing_fdsx_no_guide_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pre-existing .fdsx/ dir does not trigger guide message."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        (tmp_path / ".fdsx" / ".gitignore").write_text("# existing\n")
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive", "--version"])
        assert result.exit_code == 0
        assert "No .fdsx/ directory found" not in result.output

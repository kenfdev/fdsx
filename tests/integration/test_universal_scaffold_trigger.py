"""Integration tests for universal scaffold trigger (FR-2).

Tests verify that guide message is shown for ANY command in interactive mode
when .fdsx/ does not exist, and skipped in CI mode.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fdsx.cli import main


class TestUniversalScaffoldTrigger:
    """Tests for universal scaffold trigger behavior."""

    def test_version_flag_shows_guide_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--version in uninitialized dir shows guide message."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive", "--version"])
        assert result.exit_code == 0
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init'" in result.output

    def test_bare_fdsx_shows_guide_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bare fdsx (no args) in uninitialized dir shows guide message."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive"])
        assert result.exit_code == 0
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init'" in result.output

    def test_subcommand_shows_guide_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Subcommand in uninitialized dir shows guide message."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive", "validate", "dummy.yaml"])
        assert result.exit_code == 0
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init'" in result.output

    def test_ci_mode_shows_guide_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--ci mode shows guide message even when .fdsx/ is missing."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--ci", "--version"])
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

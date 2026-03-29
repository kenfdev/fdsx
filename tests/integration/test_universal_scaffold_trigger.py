"""Integration tests for universal scaffold trigger (FR-2).

Tests verify that scaffold is triggered for ANY command in interactive mode
when .fdsx/ does not exist, and skipped in CI mode.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fdsx.cli import main


class TestUniversalScaffoldTrigger:
    """Tests for universal scaffold trigger behavior."""

    def test_version_flag_triggers_scaffold_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--version in uninitialized dir triggers scaffold."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive", "--version"])
        assert result.exit_code == 0
        assert (tmp_path / ".fdsx").exists(), ".fdsx/ should be created"

    def test_bare_fdsx_triggers_scaffold_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bare fdsx (no args) in uninitialized dir triggers scaffold."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive"])
        assert result.exit_code == 0
        assert (tmp_path / ".fdsx").exists(), ".fdsx/ should be created"

    def test_subcommand_triggers_scaffold_when_uninitialized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Subcommand in uninitialized dir triggers scaffold."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        # validate will fail after scaffold, but scaffold should still happen
        runner.invoke(main.app, ["--interactive", "validate", "dummy.yaml"])
        assert (tmp_path / ".fdsx").exists(), ".fdsx/ should be created"

    def test_ci_mode_skips_scaffold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--ci mode does not trigger scaffold."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main.app, ["--ci", "--version"])
        assert result.exit_code == 0
        assert not (tmp_path / ".fdsx").exists(), (
            ".fdsx/ should NOT be created in CI mode"
        )

    def test_existing_fdsx_no_scaffold_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pre-existing .fdsx/ dir does not trigger scaffold message."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        (tmp_path / ".fdsx" / ".gitignore").write_text("# existing\n")
        runner = CliRunner()
        result = runner.invoke(main.app, ["--interactive", "--version"])
        assert result.exit_code == 0
        assert "Initialized" not in result.output

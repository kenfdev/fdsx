"""Integration tests for .gitignore creation during scaffold and retroactive CLI init."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from fdsx.cli import main
from fdsx.core.init import scaffold


class TestScaffoldCreatesGitignore:
    """T001: scaffold() creates .fdsx/.gitignore."""

    def test_scaffold_creates_gitignore(self, tmp_path: Path) -> None:
        """scaffold() creates .fdsx/.gitignore file."""
        scaffold(tmp_path)
        assert (tmp_path / ".fdsx" / ".gitignore").exists()

    def test_gitignore_has_header_comment(self, tmp_path: Path) -> None:
        """The first line of .fdsx/.gitignore starts with #."""
        scaffold(tmp_path)
        content = (tmp_path / ".fdsx" / ".gitignore").read_text()
        first_line = content.splitlines()[0]
        assert first_line.startswith("#")

    def test_gitignore_does_not_ignore_config_or_workflows(
        self, tmp_path: Path
    ) -> None:
        """config.yaml and workflows/ are NOT in the gitignore content."""
        scaffold(tmp_path)
        content = (tmp_path / ".fdsx" / ".gitignore").read_text()
        assert "config.yaml" not in content
        assert "workflows/" not in content


class TestRetroactiveGitignore:
    """T001: Retroactive .gitignore creation via CLI init guard."""

    def test_retroactive_gitignore_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When .fdsx/ exists but no .gitignore, --interactive run silently creates it."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        with patch("fdsx.cli.main.ensure_gitignore") as mock_ensure:
            result = runner.invoke(main.app, ["--interactive", "--version"])

            assert result.exit_code == 0
            mock_ensure.assert_called_once()

    def test_retroactive_no_overwrite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When .fdsx/.gitignore exists, retroactive init does not overwrite it."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        custom_content = "custom content\n"
        (tmp_path / ".fdsx" / ".gitignore").write_text(custom_content)
        runner = CliRunner()

        with patch("fdsx.cli.main.ensure_gitignore"):
            result = runner.invoke(main.app, ["--interactive", "--version"])

            assert result.exit_code == 0
            assert (tmp_path / ".fdsx" / ".gitignore").read_text() == custom_content

    def test_retroactive_ci_mode_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In CI mode, retroactive init does NOT call ensure_gitignore."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        with patch("fdsx.cli.main.ensure_gitignore") as mock_ensure:
            result = runner.invoke(main.app, ["--ci", "--version"])

            assert result.exit_code == 0
            mock_ensure.assert_not_called()

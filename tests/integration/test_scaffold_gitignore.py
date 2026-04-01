"""Integration tests for .gitignore creation during scaffold and retroactive CLI init.

Tests verify:
- scaffold() creates .fdsx/.gitignore with correct content
- CLI retroactive creation when .fdsx/ exists but .gitignore missing
- CLI preserves existing .gitignore
- CI mode skips .gitignore creation
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from fdsx.cli import main
from fdsx.core.init import (
    GITIGNORE_TEMPLATE,
    discover_templates,
    ensure_gitignore,
    scaffold,
)
from fdsx.models.init import InitConfig, ProviderSelection


def _make_default_config() -> InitConfig:
    return InitConfig(
        providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
        templates=discover_templates(),
    )


class TestScaffoldCreatesGitignore:
    """Tests for .fdsx/.gitignore creation during scaffold()."""

    def test_scaffold_creates_gitignore(self, tmp_path: Path) -> None:
        """scaffold(tmp_path, config) creates .fdsx/.gitignore file."""
        config = _make_default_config()
        scaffold(tmp_path, config)
        gitignore_path = tmp_path / ".fdsx" / ".gitignore"
        assert gitignore_path.exists(), ".fdsx/.gitignore was not created by scaffold()"

    def test_gitignore_has_header_comment(self, tmp_path: Path) -> None:
        """The first line of .fdsx/.gitignore starts with #."""
        config = _make_default_config()
        scaffold(tmp_path, config)
        gitignore_path = tmp_path / ".fdsx" / ".gitignore"
        content = gitignore_path.read_text()
        first_line = content.splitlines()[0]
        assert first_line.startswith("#"), (
            f"Expected first line to be a comment, got: {first_line!r}"
        )

    def test_gitignore_does_not_ignore_config_or_workflows(
        self, tmp_path: Path
    ) -> None:
        """config.yaml and workflows/ are NOT in .gitignore patterns."""
        config = _make_default_config()
        scaffold(tmp_path, config)
        gitignore_path = tmp_path / ".fdsx" / ".gitignore"
        content = gitignore_path.read_text()
        assert "config.yaml" not in content, "config.yaml should not be in .gitignore"
        assert "workflows/" not in content, "workflows/ should not be in .gitignore"


class TestRetroactiveGitignore:
    """Tests for retroactive .gitignore creation via CLI when .fdsx/ exists."""

    def test_retroactive_gitignore_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI creates .fdsx/.gitignore when .fdsx/ exists but .gitignore missing."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(main.app, ["--interactive", "--version"])

        assert result.exit_code == 0
        gitignore_path = tmp_path / ".fdsx" / ".gitignore"
        assert gitignore_path.exists(), (
            ".fdsx/.gitignore should be created retroactively"
        )
        assert "gitignore" not in (result.stderr_bytes or b"").decode().lower()

    def test_retroactive_no_overwrite(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI does not overwrite an existing custom .fdsx/.gitignore."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        custom_content = "custom content\n"
        (tmp_path / ".fdsx" / ".gitignore").write_text(custom_content)
        runner = CliRunner()

        result = runner.invoke(main.app, ["--interactive", "--version"])

        assert result.exit_code == 0
        assert (tmp_path / ".fdsx" / ".gitignore").read_text() == custom_content

    def test_retroactive_ci_mode_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CLI with --ci flag does not create .fdsx/.gitignore."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(main.app, ["--ci", "--version"])

        assert result.exit_code == 0
        gitignore_path = tmp_path / ".fdsx" / ".gitignore"
        assert not gitignore_path.exists(), (
            ".fdsx/.gitignore should not be created in CI mode"
        )


class TestEnsureGitignore:
    """Unit-level tests for ensure_gitignore function."""

    def test_ensure_gitignore_creates_file_when_missing(self, tmp_path: Path) -> None:
        """ensure_gitignore creates .fdsx/.gitignore when .fdsx/ exists but .gitignore does not."""
        (tmp_path / ".fdsx").mkdir()
        ensure_gitignore(tmp_path)
        gitignore_path = tmp_path / ".fdsx" / ".gitignore"
        assert gitignore_path.exists()
        assert gitignore_path.read_text() == GITIGNORE_TEMPLATE

    def test_ensure_gitignore_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        """ensure_gitignore does not overwrite an existing .fdsx/.gitignore."""
        (tmp_path / ".fdsx").mkdir()
        custom_content = "custom content\n"
        (tmp_path / ".fdsx" / ".gitignore").write_text(custom_content)
        ensure_gitignore(tmp_path)
        assert (tmp_path / ".fdsx" / ".gitignore").read_text() == custom_content

    def test_gitignore_template_contains_runtime_dirs(self) -> None:
        """GITIGNORE_TEMPLATE contains runs/, tasks/, checkpoints/, locks/."""
        assert "runs/" in GITIGNORE_TEMPLATE
        assert "tasks/" in GITIGNORE_TEMPLATE
        assert "checkpoints/" in GITIGNORE_TEMPLATE
        assert "locks/" in GITIGNORE_TEMPLATE

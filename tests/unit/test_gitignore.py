"""Unit tests for ensure_gitignore() and GITIGNORE_TEMPLATE in fdsx.core.init."""

from pathlib import Path

from fdsx.core.init import GITIGNORE_TEMPLATE, ensure_gitignore


class TestGitignoreTemplate:
    def test_contains_all_runtime_dirs(self) -> None:
        """GITIGNORE_TEMPLATE contains runs/, tasks/, checkpoints/, locks/."""
        assert "runs/" in GITIGNORE_TEMPLATE
        assert "tasks/" in GITIGNORE_TEMPLATE
        assert "checkpoints/" in GITIGNORE_TEMPLATE
        assert "locks/" in GITIGNORE_TEMPLATE


class TestEnsureGitignore:
    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        """ensure_gitignore creates .fdsx/.gitignore when .fdsx/ exists but .gitignore does not."""
        (tmp_path / ".fdsx").mkdir()
        ensure_gitignore(tmp_path)
        gitignore_path = tmp_path / ".fdsx" / ".gitignore"
        assert gitignore_path.exists()
        assert gitignore_path.read_text() == GITIGNORE_TEMPLATE

    def test_does_not_overwrite_existing(self, tmp_path: Path) -> None:
        """ensure_gitignore does not overwrite an existing .fdsx/.gitignore."""
        (tmp_path / ".fdsx").mkdir()
        custom_content = "custom content\n"
        (tmp_path / ".fdsx" / ".gitignore").write_text(custom_content)
        ensure_gitignore(tmp_path)
        assert (tmp_path / ".fdsx" / ".gitignore").read_text() == custom_content

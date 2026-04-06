"""Integration tests for skill install functionality.

Tests get_bundled_skill_path(), install_skill() in core/init.py,
and prompt_skill_install(), confirm_skill_overwrite() in cli/init_interactive.py,
and --skill flag in cli/main.py.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from fdsx.cli import main as cli_main
from fdsx.cli.init_interactive import (
    _console,
    confirm_skill_overwrite,
    prompt_skill_install,
)
from fdsx.core.init import get_bundled_skill_path, install_skill
from fdsx.models.init import ProviderSelection

runner = CliRunner()


class TestGetBundledSkillPath:
    def test_returns_path_to_fdsx_skill_package(self):
        """get_bundled_skill_path() yields a path with name 'fdsx'."""
        with get_bundled_skill_path() as result:
            assert result.name == "fdsx"

    def test_path_exists(self):
        """The yielded path exists on the filesystem."""
        with get_bundled_skill_path() as result:
            assert result.exists()


class TestInstallSkill:
    def test_copies_skill_files_to_target_dir(self, tmp_path):
        """install_skill() copies SKILL.md and references/ to target_dir/fdsx/."""
        target_dir = tmp_path / "skills"
        result = install_skill(target_dir)

        skill_dir = target_dir / "fdsx"
        assert skill_dir.exists()
        assert (skill_dir / "SKILL.md").exists()
        assert (skill_dir / "references").is_dir()
        assert (skill_dir / "references" / "yaml-schema.md").exists()
        assert len(result) > 0

    def test_returns_list_of_created_relative_paths(self, tmp_path):
        """install_skill() returns list of relative paths for created files."""
        target_dir = tmp_path / "skills"
        result = install_skill(target_dir)

        assert all(isinstance(p, str) for p in result)
        assert all(not p.startswith("/") for p in result)
        assert all(p.startswith("fdsx/") for p in result)
        assert any("SKILL.md" in p for p in result)
        assert any("yaml-schema.md" in p for p in result)

    def test_raises_when_target_exists_and_overwrite_false(self, tmp_path):
        """install_skill() raises FileExistsError when target exists and overwrite=False."""
        target_dir = tmp_path / "skills"
        install_skill(target_dir)

        with pytest.raises(FileExistsError):
            install_skill(target_dir, overwrite=False)

    def test_overwrites_when_overwrite_true(self, tmp_path):
        """install_skill() removes existing and copies fresh when overwrite=True."""
        target_dir = tmp_path / "skills"
        install_skill(target_dir)

        result = install_skill(target_dir, overwrite=True)
        assert len(result) > 0

    def test_overwrite_false_raises_when_exists(self, tmp_path):
        """install_skill() raises FileExistsError when skill exists and overwrite=False."""
        target_dir = tmp_path / "skills"
        install_skill(target_dir)

        with pytest.raises(FileExistsError):
            install_skill(target_dir, overwrite=False)

    def test_skill_files_are_copied_not_linked(self, tmp_path):
        """Files are actual copies, not symlinks."""
        target_dir = tmp_path / "skills"
        install_skill(target_dir)

        skill_file = target_dir / "fdsx" / "SKILL.md"
        assert not skill_file.is_symlink()
        assert skill_file.read_text()


class TestPromptSkillInstall:
    def _mock_console(self) -> MagicMock:
        return MagicMock(spec=type(_console))

    def _patch_console(self, mocker: MagicMock):
        return patch("fdsx.cli.init_interactive._console", mocker)

    def test_decline_returns_none(self):
        """Declining skill install (n) returns None."""
        with (
            patch("fdsx.cli.init_interactive._input", return_value="n"),
            self._patch_console(self._mock_console()),
        ):
            result = prompt_skill_install()
        assert result is None

    def test_accept_default_returns_home_skills_path(self):
        """Accepting with default (Enter) returns ~/.agents/skills."""
        home = Path("~").expanduser()
        expected = home / ".agents" / "skills"

        with (
            patch("fdsx.cli.init_interactive._input", return_value=""),
            self._patch_console(self._mock_console()),
        ):
            result = prompt_skill_install()
        assert result == expected

    def test_accept_option_1_returns_home_skills_path(self):
        """Selecting option 1 returns ~/.agents/skills."""
        home = Path("~").expanduser()
        expected = home / ".agents" / "skills"

        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["y", "1"],
            ),
            self._patch_console(self._mock_console()),
        ):
            result = prompt_skill_install()
        assert result == expected

    def test_accept_option_2_returns_project_local_path(self):
        """Selecting option 2 returns .agents/skills (project-local)."""
        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["y", "2"],
            ),
            self._patch_console(self._mock_console()),
        ):
            result = prompt_skill_install()
        assert result == Path(".agents/skills")

    def test_accept_option_3_prompts_for_custom_path(self):
        """Selecting option 3 then entering a path returns that custom path."""
        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["y", "3", "/custom/path"],
            ),
            self._patch_console(self._mock_console()),
        ):
            result = prompt_skill_install()
        assert result == Path("/custom/path")

    def test_invalid_option_retries_then_succeeds(self):
        """Invalid numeric input triggers retry; accepting on second attempt succeeds."""
        home = Path("~").expanduser()
        expected = home / ".agents" / "skills"

        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["y", "99", "1"],
            ) as mock_input,
            self._patch_console(self._mock_console()),
        ):
            result = prompt_skill_install()
        assert result == expected
        assert mock_input.call_count == 3


class TestConfirmSkillOverwrite:
    def _mock_console(self) -> MagicMock:
        return MagicMock(spec=type(_console))

    def _patch_console(self, mocker: MagicMock):
        return patch("fdsx.cli.init_interactive._console", mocker)

    def test_yes_returns_true(self, tmp_path):
        """'y' input returns True."""
        path = tmp_path / "fdsx"
        with (
            patch("fdsx.cli.init_interactive._input", return_value="y"),
            self._patch_console(self._mock_console()),
        ):
            assert confirm_skill_overwrite(path) is True

    def test_no_returns_false(self, tmp_path):
        """'n' input returns False."""
        path = tmp_path / "fdsx"
        with (
            patch("fdsx.cli.init_interactive._input", return_value="n"),
            self._patch_console(self._mock_console()),
        ):
            assert confirm_skill_overwrite(path) is False

    def test_invalid_then_yes_retries(self, tmp_path):
        """Invalid input triggers retry; 'y' on second attempt returns True."""
        path = tmp_path / "fdsx"
        with (
            patch(
                "fdsx.cli.init_interactive._input",
                side_effect=["maybe", "y"],
            ) as mock_input,
            self._patch_console(self._mock_console()),
        ):
            result = confirm_skill_overwrite(path)
        assert result is True
        assert mock_input.call_count == 2


class TestInitSkillFlag:
    def test_skill_flag_only_installs_skill(self, tmp_path, monkeypatch):
        """fdsx init --skill only installs skill, no .fdsx/ scaffold created."""
        monkeypatch.chdir(tmp_path)
        fake_skill_path = tmp_path / "fake-skill-target"

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch(
                "fdsx.cli.main.prompt_skill_install",
                return_value=fake_skill_path,
            ),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(cli_main.app, ["init", "--skill"])

        assert result.exit_code == 0
        skill_dir = fake_skill_path / "fdsx"
        assert skill_dir.exists()
        assert (skill_dir / "SKILL.md").exists()
        assert not (tmp_path / ".fdsx").exists()

    def test_skill_flag_with_overwrite_prompt(self, tmp_path, monkeypatch):
        """fdsx init --skill when skill exists prompts for overwrite."""
        monkeypatch.chdir(tmp_path)
        fake_skill_path = tmp_path / "fake-skill-target"
        install_skill(fake_skill_path)

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch(
                "fdsx.cli.main.prompt_skill_install",
                return_value=fake_skill_path,
            ),
            patch(
                "fdsx.cli.main.confirm_skill_overwrite",
                return_value=True,
            ),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(cli_main.app, ["init", "--skill"])

        assert result.exit_code == 0

    def test_skill_flag_overwrite_declined_aborts(self, tmp_path, monkeypatch):
        """fdsx init --skill when overwrite declined exits without changes."""
        monkeypatch.chdir(tmp_path)
        fake_skill_path = tmp_path / "fake-skill-target"
        install_skill(fake_skill_path)
        original_mtime = (fake_skill_path / "fdsx" / "SKILL.md").stat().st_mtime

        import time

        time.sleep(0.01)

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch(
                "fdsx.cli.main.prompt_skill_install",
                return_value=fake_skill_path,
            ),
            patch(
                "fdsx.cli.main.confirm_skill_overwrite",
                return_value=False,
            ),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(cli_main.app, ["init", "--skill"])

        assert result.exit_code == 0
        current_mtime = (fake_skill_path / "fdsx" / "SKILL.md").stat().st_mtime
        assert current_mtime == original_mtime

    def test_skill_flag_with_decline_returns_none(self, tmp_path, monkeypatch):
        """fdsx init --skill when user declines skill install exits gracefully."""
        monkeypatch.chdir(tmp_path)

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch(
                "fdsx.cli.main.prompt_skill_install",
                return_value=None,
            ),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(cli_main.app, ["init", "--skill"])

        assert result.exit_code == 0

    def test_normal_init_with_skill_install(self, tmp_path, monkeypatch):
        """Normal fdsx init (no --skill) still completes scaffold and offers skill install."""
        monkeypatch.chdir(tmp_path)
        fake_skill_path = tmp_path / "fake-skill-target"

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=[]),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=["claude"]),
            patch(
                "fdsx.cli.main.select_models",
                return_value=[ProviderSelection(provider="claude", model="sonnet")],
            ),
            patch("fdsx.cli.main.select_templates", return_value=[]),
            patch("fdsx.cli.main.scaffold") as mock_scaffold,
            patch(
                "fdsx.cli.main.prompt_skill_install",
                return_value=fake_skill_path,
            ),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(cli_main.app, ["init"], catch_exceptions=False)

        assert result.exit_code == 0
        mock_scaffold.assert_called_once()
        assert "Initialized .fdsx/ directory" in result.output

    def test_normal_init_decline_skill_still_completes(self, tmp_path, monkeypatch):
        """Declining skill install during normal fdsx init still completes scaffold."""
        monkeypatch.chdir(tmp_path)

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=[]),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=["claude"]),
            patch(
                "fdsx.cli.main.select_models",
                return_value=[ProviderSelection(provider="claude", model="sonnet")],
            ),
            patch("fdsx.cli.main.select_templates", return_value=[]),
            patch("fdsx.cli.main.scaffold") as mock_scaffold,
            patch(
                "fdsx.cli.main.prompt_skill_install",
                return_value=None,
            ),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(cli_main.app, ["init"], catch_exceptions=False)

        assert result.exit_code == 0
        mock_scaffold.assert_called_once()
        assert "Initialized .fdsx/ directory" in result.output

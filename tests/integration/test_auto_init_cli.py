"""Integration tests for --ci and --interactive global flags (T017-T018) and init guard (T019-T022).

Tests verify:
- --ci and --interactive are mutually exclusive
- --ci sets _interactive_mode to False
- --interactive sets _interactive_mode to True
- Init guard triggers scaffold() for operational commands when .fdsx/ missing
- Init guard skipped for --help, --version, CI mode, non-TTY, or when .fdsx/ exists
"""

from unittest.mock import patch

from typer.testing import CliRunner

from fdsx.cli import main
from fdsx.core.mode import get_interactive_mode, set_interactive_mode


class TestCiInteractiveFlags:
    def test_ci_and_interactive_mutual_exclusion(self, tmp_path, monkeypatch):
        """--ci and --interactive cannot be used together."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(
            main.app, ["--ci", "--interactive", "validate", "dummy.yaml"]
        )

        assert result.exit_code == 2, (
            f"Expected exit 2 for mutual exclusion, got {result.exit_code}. "
            f"output: {result.output}"
        )
        assert "mutually exclusive" in result.output.lower()

    def test_ci_flag_parsed(self, tmp_path, monkeypatch):
        """--ci flag is accepted and sets _interactive_mode to False."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        set_interactive_mode(None)
        try:
            result = runner.invoke(main.app, ["--ci", "--version"])

            assert result.exit_code == 0, (
                f"Expected exit 0, got {result.exit_code}. output: {result.output}"
            )
            assert get_interactive_mode() is False, (
                f"Expected _interactive_mode to be False, got {get_interactive_mode()}"
            )
        finally:
            set_interactive_mode(None)

    def test_interactive_flag_parsed(self, tmp_path, monkeypatch):
        """--interactive flag is accepted and sets _interactive_mode to True."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        set_interactive_mode(None)
        try:
            result = runner.invoke(main.app, ["--interactive", "--version"])

            assert result.exit_code == 0, (
                f"Expected exit 0, got {result.exit_code}. output: {result.output}"
            )
            assert get_interactive_mode() is True, (
                f"Expected _interactive_mode to be True, got {get_interactive_mode()}"
            )
        finally:
            set_interactive_mode(None)


class TestInitGuard:
    """T019-T022: Init guard tests for operational subcommands."""

    def test_init_triggers_on_run_without_fdsx(self, tmp_path, monkeypatch):
        """Guide message shown when running 'run' without .fdsx/ directory."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with (
            patch("fdsx.cli.main.needs_init", return_value=True),
        ):
            result = runner.invoke(
                main.app, ["--interactive", "run", "dummy.yaml"], catch_exceptions=False
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init'" in result.output

    def test_init_triggers_on_validate_without_fdsx(self, tmp_path, monkeypatch):
        """Guide message shown when running 'validate' without .fdsx/ directory."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with (
            patch("fdsx.cli.main.needs_init", return_value=True),
        ):
            result = runner.invoke(
                main.app,
                ["--interactive", "validate", "dummy.yaml"],
                catch_exceptions=False,
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init'" in result.output

    def test_init_triggers_on_list_without_fdsx(self, tmp_path, monkeypatch):
        """Guide message shown when running 'list' without .fdsx/ directory."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with (
            patch("fdsx.cli.main.needs_init", return_value=True),
        ):
            result = runner.invoke(
                main.app, ["--interactive", "list"], catch_exceptions=False
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init'" in result.output

    def test_init_skipped_when_fdsx_exists(self, tmp_path, monkeypatch):
        """Init is skipped when .fdsx/ directory already exists."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        with patch("fdsx.cli.main.scaffold") as mock_scaffold:
            runner.invoke(main.app, ["run", "dummy.yaml"])

        mock_scaffold.assert_not_called()

    def test_help_does_not_trigger_init(self, tmp_path, monkeypatch):
        """--help does not trigger init guard."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with patch("fdsx.cli.main.scaffold") as mock_scaffold:
            result = runner.invoke(main.app, ["--help"])

        mock_scaffold.assert_not_called()
        assert result.exit_code == 0

    def test_version_does_not_trigger_init(self, tmp_path, monkeypatch):
        """--version does not trigger init guard."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with patch("fdsx.cli.main.scaffold") as mock_scaffold:
            result = runner.invoke(main.app, ["--version"])

        mock_scaffold.assert_not_called()
        assert result.exit_code == 0

    def test_init_skipped_in_ci_mode(self, tmp_path, monkeypatch):
        """Init is skipped in CI mode even without .fdsx/."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with patch("fdsx.cli.main.scaffold") as mock_scaffold:
            runner.invoke(main.app, ["--ci", "run", "dummy.yaml"])

        mock_scaffold.assert_not_called()

    def test_init_skipped_in_non_tty(self, tmp_path, monkeypatch):
        """Init is skipped in non-TTY environment without --interactive."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        runner = CliRunner()

        with patch("fdsx.cli.main.scaffold") as mock_scaffold:
            runner.invoke(main.app, ["run", "dummy.yaml"])

        mock_scaffold.assert_not_called()

    def test_interactive_flag_shows_guide_in_non_tty(self, tmp_path, monkeypatch):
        """--interactive shows guide message even in non-TTY environment."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        runner = CliRunner()

        with (
            patch("fdsx.cli.main.needs_init", return_value=True),
        ):
            result = runner.invoke(
                main.app, ["--interactive", "run", "dummy.yaml"], catch_exceptions=False
            )

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init'" in result.output

    def test_guide_message_format(self, tmp_path, monkeypatch):
        """Guide message is displayed correctly when .fdsx/ is missing."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        with (
            patch("fdsx.cli.main.needs_init", return_value=True),
        ):
            result = runner.invoke(
                main.app, ["--interactive", "run", "dummy.yaml"], catch_exceptions=False
            )

        assert result.exit_code == 0
        assert "No .fdsx/ directory found" in result.output
        assert "Run 'fdsx init' to set up your project" in result.output

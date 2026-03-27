"""Integration tests for --ci and --interactive global flags (T017-T018).

Tests verify:
- --ci and --interactive are mutually exclusive
- --ci sets _interactive_mode to False
- --interactive sets _interactive_mode to True
"""

from typer.testing import CliRunner

from fdsx.cli import main


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

        main._interactive_mode = None
        result = runner.invoke(main.app, ["--ci", "--version"])

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        assert main._interactive_mode is False, (
            f"Expected _interactive_mode to be False, got {main._interactive_mode}"
        )

    def test_interactive_flag_parsed(self, tmp_path, monkeypatch):
        """--interactive flag is accepted and sets _interactive_mode to True."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        main._interactive_mode = None
        result = runner.invoke(main.app, ["--interactive", "--version"])

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        assert main._interactive_mode is True, (
            f"Expected _interactive_mode to be True, got {main._interactive_mode}"
        )

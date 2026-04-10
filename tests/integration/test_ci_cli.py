"""Integration tests for CI environment variable auto-detection (T008-T009).

Tests verify that the CLI correctly auto-detects CI environments by checking
CI and GITHUB_ACTIONS environment variables before falling back to TTY detection.
"""

from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core.mode import get_interactive_mode, set_interactive_mode


class TestCIEnvVarAutoDetection:
    """Tests for CI/GITHUB_ACTIONS env var detection in CLI callback."""

    def test_ci_env_var_true_enables_ci_mode(self, tmp_path, monkeypatch):
        """CI=true enables non-interactive (CI) mode."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(app, ["--version"], env={"CI": "true"})

        assert result.exit_code == 0
        try:
            assert get_interactive_mode() is False
        finally:
            set_interactive_mode(None)

    def test_ci_env_var_1_enables_ci_mode(self, tmp_path, monkeypatch):
        """CI=1 enables non-interactive (CI) mode."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(app, ["--version"], env={"CI": "1"})

        assert result.exit_code == 0
        try:
            assert get_interactive_mode() is False
        finally:
            set_interactive_mode(None)

    def test_ci_env_var_yes_enables_ci_mode(self, tmp_path, monkeypatch):
        """CI=yes enables non-interactive (CI) mode."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(app, ["--version"], env={"CI": "yes"})

        assert result.exit_code == 0
        try:
            assert get_interactive_mode() is False
        finally:
            set_interactive_mode(None)

    def test_github_actions_enables_ci_mode(self, tmp_path, monkeypatch):
        """GITHUB_ACTIONS=true enables non-interactive (CI) mode."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(app, ["--version"], env={"GITHUB_ACTIONS": "true"})

        assert result.exit_code == 0
        try:
            assert get_interactive_mode() is False
        finally:
            set_interactive_mode(None)

    def test_interactive_flag_overrides_ci_env(self, tmp_path, monkeypatch):
        """--interactive flag overrides CI env var, enabling interactive mode."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(
            app,
            ["--interactive", "--version"],
            env={"CI": "true"},
        )

        assert result.exit_code == 0
        try:
            assert get_interactive_mode() is True
        finally:
            set_interactive_mode(None)

    def test_no_flags_no_env_falls_back_to_tty(self, tmp_path, monkeypatch):
        """Without flags or env vars, falls back to sys.stdin.isatty()."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(app, ["--version"], env={})

        assert result.exit_code == 0
        try:
            mode = get_interactive_mode()
            assert mode is not None
        finally:
            set_interactive_mode(None)

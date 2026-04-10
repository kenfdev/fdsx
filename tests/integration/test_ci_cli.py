"""Integration tests for CI environment variable auto-detection (T008-T009).

Tests verify that the CLI correctly auto-detects CI environments by checking
CI and GITHUB_ACTIONS environment variables before falling back to TTY detection.
"""

from unittest.mock import MagicMock, patch

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


class TestConfirmWorkflowCIConflict:
    """Tests for --confirm-workflow + CI mode mutual exclusion (T016-T017)."""

    def test_confirm_workflow_with_ci_flag_exits_2(self, tmp_path, monkeypatch):
        """--confirm-workflow with --ci flag exits with code 2."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(
            app,
            ["--ci", "run", "--confirm-workflow", "dummy.yaml"],
        )

        assert result.exit_code == 2
        assert (
            "confirm-workflow" in result.stderr.lower()
            or "interactive mode" in result.stderr.lower()
        )
        try:
            set_interactive_mode(None)
        finally:
            set_interactive_mode(None)

    def test_confirm_workflow_with_ci_env_exits_2(self, tmp_path, monkeypatch):
        """--confirm-workflow with CI=true env var exits with code 2."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(
            app,
            ["run", "--confirm-workflow", "dummy.yaml"],
            env={"CI": "true"},
        )

        assert result.exit_code == 2
        assert (
            "confirm-workflow" in result.stderr.lower()
            or "interactive mode" in result.stderr.lower()
        )
        try:
            set_interactive_mode(None)
        finally:
            set_interactive_mode(None)

    def test_confirm_workflow_with_interactive_flag_passes_validation(
        self, tmp_path, monkeypatch
    ):
        """--confirm-workflow with --interactive flag does not exit with code 2."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        (tmp_path / "workflow.yaml").write_text(
            "name: test\ndescription: 'Test workflow'\nstates:\n  - type: task\n    name: test\n"
        )
        runner = CliRunner()

        fake_result = MagicMock()
        with patch("fdsx.core.engine.run_flow", return_value=fake_result):
            result = runner.invoke(
                app,
                ["--interactive", "run", "--confirm-workflow", "workflow.yaml"],
            )

        assert result.exit_code != 2, (
            f"Expected exit code != 2, got {result.exit_code}. stderr: {result.stderr}"
        )
        try:
            set_interactive_mode(None)
        finally:
            set_interactive_mode(None)

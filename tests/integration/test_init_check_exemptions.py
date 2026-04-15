"""TDD tests for init-check exemptions (T002-T008, T012).

These tests document the expected behavior of init-check exemptions:
- --version, --help, subcommand --help, validate, and bare invocation should
  NOT show the init warning even in uninitialized directories.
- Operational commands (run, resume, list, add) should still show the warning.

Tests T002, T005, T006, T007 will FAIL against the current implementation
until the exemptions are added (T009-T010). This is intentional TDD behavior.

Note: Typer's CliRunner mixes stdout and stderr into result.output. All
assertions use result.output to check for the presence of the init warning.
"""

import pytest
import yaml
from typer.testing import CliRunner

from fdsx.cli.main import app

_INIT_WARNING = "No .fdsx/"


class TestExemptCommands:
    """Tests for commands that should NOT show the init warning (T002-T008)."""

    def test_version_flag_skips_init_warning_in_uninit_dir(self, tmp_path, monkeypatch):
        """T002: --version should print the version without triggering the init warning."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, ["--version"])

        assert _INIT_WARNING not in result.output
        assert "fdsx " in result.output
        assert result.exit_code == 0

    def test_help_flag_skips_init_warning_in_uninit_dir(self, tmp_path, monkeypatch):
        """T003: --help should show help without triggering the init warning."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, ["--help"])

        assert _INIT_WARNING not in result.output
        assert result.exit_code == 0

    @pytest.mark.parametrize(
        "cmd", ["run", "resume", "list", "add", "init", "validate"]
    )
    def test_subcommand_help_skips_init_warning(self, cmd, tmp_path, monkeypatch):
        """T004: <subcommand> --help should show help without triggering the init warning."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, [cmd, "--help"])

        assert _INIT_WARNING not in result.output
        assert result.exit_code == 0

    def test_validate_valid_workflow_skips_init_warning(self, tmp_path, monkeypatch):
        """T005: validate with a valid workflow file should not show the init warning."""
        monkeypatch.chdir(tmp_path)
        workflow = tmp_path / "valid.yaml"
        workflow.write_text(
            yaml.dump(
                {
                    "name": "Test",
                    "description": "A minimal test workflow",
                    "start_at": "step1",
                    "states": {
                        "step1": {
                            "type": "task",
                            "provider": "system",
                            "command": "echo done",
                            "result_path": "$.result",
                            "end": True,
                        }
                    },
                }
            )
        )
        runner = CliRunner()

        result = runner.invoke(app, ["validate", str(workflow)])

        assert _INIT_WARNING not in result.output
        assert result.exit_code == 0  # validate must succeed on valid YAML

    def test_validate_invalid_workflow_skips_init_warning(self, tmp_path, monkeypatch):
        """T006: validate with an invalid workflow file should report validation errors, not init warning."""
        monkeypatch.chdir(tmp_path)
        workflow = tmp_path / "invalid.yaml"
        workflow.write_text(": invalid yaml: [unclosed")
        runner = CliRunner()

        result = runner.invoke(app, ["validate", str(workflow)])

        assert _INIT_WARNING not in result.output
        assert result.exit_code != 0       # validate must reject malformed YAML
        assert "Error:" in result.output   # validation error must surface in output

    def test_bare_invocation_skips_init_warning(self, tmp_path, monkeypatch):
        """T007: bare 'fdsx' with no subcommand should not show the init warning."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, [])

        assert _INIT_WARNING not in result.output
        assert result.exit_code == 0

    def test_version_flag_in_initialized_dir(self, tmp_path, monkeypatch):
        """T008: --version in an initialized dir should print the version (regression guard)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        runner = CliRunner()

        result = runner.invoke(app, ["--version"])

        assert "fdsx " in result.output
        assert result.exit_code == 0


class TestNonExemptCommands:
    """Tests for operational commands that SHOULD show the init warning (T012)."""

    @pytest.mark.parametrize(
        "cmd_args",
        [
            ["run", "dummy.yaml"],
            ["resume", "--thread-id", "fake-id"],
            ["list"],
            ["add", "dummy-task.txt"],
        ],
    )
    def test_non_exempt_command_shows_init_warning(
        self, cmd_args, tmp_path, monkeypatch
    ):
        """T012: Operational commands should show the init warning when .fdsx/ is missing."""
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, cmd_args)

        assert _INIT_WARNING in result.output
        assert result.exit_code == 0

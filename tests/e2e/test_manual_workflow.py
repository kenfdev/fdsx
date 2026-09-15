"""CLI option parsing and error exits without any provider execution."""

import pytest
from click import unstyle

from tests.e2e.cli_test_utils import run_fdsx


@pytest.mark.parametrize(
    "options",
    [
        ["--manual-workflow", "--auto-workflow"],
        ["--auto-workflow", "--confirm-workflow"],
    ],
)
def test_conflicting_options_exit_two(tmp_path, options):
    tasks = tmp_path / ".fdsx/tasks"
    tasks.mkdir(parents=True)
    result = run_fdsx(["run", "--tasks-dir", str(tasks), *options], cwd=tmp_path)
    assert result.returncode == 2
    assert "mutually exclusive" in result.stderr


@pytest.mark.parametrize("force_color", [False, True])
def test_manual_option_help(tmp_path, monkeypatch, force_color):
    if force_color:
        monkeypatch.setenv("FORCE_COLOR", "1")
    else:
        monkeypatch.delenv("FORCE_COLOR", raising=False)
    result = run_fdsx(["run", "--help"], cwd=tmp_path)
    assert result.returncode == 0
    plain_output = unstyle(result.stdout)
    assert "--manual-workflow" in plain_output
    assert "Disable AI" in plain_output

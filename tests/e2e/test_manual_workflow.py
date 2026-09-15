"""CLI option parsing and error exits without any provider execution."""

import pytest

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


def test_manual_option_help(tmp_path):
    result = run_fdsx(["run", "--help"], cwd=tmp_path)
    assert result.returncode == 0
    assert "--manual-workflow" in result.stdout
    assert "Disable AI" in result.stdout

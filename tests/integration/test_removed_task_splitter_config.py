from pathlib import Path

import pytest
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.core.config import load_config


def test_task_splitter_config_reports_targeted_removal_error(
    tmp_path: Path,
) -> None:
    fdsx_dir = tmp_path / ".fdsx"
    fdsx_dir.mkdir()
    (fdsx_dir / "config.yaml").write_text(
        "task_splitter:\n  provider: claude\n  model: claude-sonnet-4-6\n"
    )

    with pytest.raises(
        ValueError,
        match=(
            r"task_splitter has been removed. Delete the task_splitter section; "
            "fdsx add now queues each input file directly"
        ),
    ):
        load_config(project_dir=tmp_path, load_global=False)


def test_add_reports_task_splitter_migration_error(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "task.md"
    source.write_text("Task")
    fdsx_dir = tmp_path / ".fdsx"
    fdsx_dir.mkdir()
    (fdsx_dir / "config.yaml").write_text("task_splitter: null\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["add", str(source)])

    assert result.exit_code == 2
    assert "task_splitter has been removed" in result.stderr

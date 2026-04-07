"""Integration tests for default_tasks_dir feature (US4)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.models.task import TaskEntry, TaskFile, save_task_file


def _make_workflow_yaml(name: str, description: str) -> str:
    return yaml.dump(
        {
            "name": name,
            "description": description,
            "start_at": "s",
            "states": {
                "s": {
                    "type": "task",
                    "provider": "system",
                    "command": "echo done",
                    "result_path": "$.result",
                    "end": True,
                }
            },
        }
    )


def _setup_workflows(project_root: Path) -> Path:
    workflows_dir = project_root / ".fdsx" / "workflows"
    workflows_dir.mkdir(parents=True)
    wf_path = workflows_dir / "test-workflow.yaml"
    wf_path.write_text(_make_workflow_yaml("TestWorkflow", "Test workflow"))
    return wf_path


class TestDefaultTasksDirResolution:
    """Tests for US4: default_tasks_dir config field and no-arg fdsx run."""

    def test_no_arg_no_tasks_dir_exits_with_error(self, tmp_path, monkeypatch):
        """(a) No config, no .fdsx/tasks/ -> exit 2."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        runner = CliRunner()
        result = runner.invoke(app, ["run"])

        assert result.exit_code == 2
        assert "Tasks directory not found" in result.stderr

    def test_no_arg_with_fallback_tasks_dir_works(self, tmp_path, monkeypatch):
        """(c) No config but .fdsx/tasks/ exists -> uses fallback."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / ".fdsx" / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test task")])
        save_task_file(tasks_dir / "001-test.yaml", tf)

        _setup_workflows(tmp_path)

        with (
            patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
            patch("fdsx.core.engine.tasks_dir.run_flow", return_value={"result": "ok"}),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["run", "--auto-workflow"])

        assert result.exit_code == 0, f"stderr={result.stderr}"

    def test_no_arg_with_config_default_tasks_dir(self, tmp_path, monkeypatch):
        """(b) config.default_tasks_dir is set -> uses it."""
        monkeypatch.chdir(tmp_path)
        fdsx_dir = tmp_path / ".fdsx"
        fdsx_dir.mkdir()
        config_file = fdsx_dir / "config.yaml"
        config_file.write_text(yaml.dump({"default_tasks_dir": ".fdsx/tasks"}))

        tasks_dir = tmp_path / ".fdsx" / "tasks"
        tasks_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test task")])
        save_task_file(tasks_dir / "001-test.yaml", tf)

        _setup_workflows(tmp_path)

        with (
            patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
            patch("fdsx.core.engine.tasks_dir.run_flow", return_value={"result": "ok"}),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["run", "--auto-workflow"])

        assert result.exit_code == 0, f"stderr={result.stderr}"

    def test_project_config_default_tasks_dir(self, tmp_path, monkeypatch):
        """(d) Project config default_tasks_dir is used."""
        monkeypatch.chdir(tmp_path)
        fdsx_dir = tmp_path / ".fdsx"
        fdsx_dir.mkdir()
        config_file = fdsx_dir / "config.yaml"
        config_file.write_text(yaml.dump({"default_tasks_dir": "my-tasks"}))

        my_tasks = tmp_path / "my-tasks"
        my_tasks.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test task")])
        save_task_file(my_tasks / "001-test.yaml", tf)

        _setup_workflows(tmp_path)

        with (
            patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
            patch("fdsx.core.engine.tasks_dir.run_flow", return_value={"result": "ok"}),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["run", "--auto-workflow"])

        assert result.exit_code == 0, f"stderr={result.stderr}"

    def test_project_overrides_global_default_tasks_dir(self, tmp_path, monkeypatch):
        """(e) Project config default_tasks_dir overrides global config."""
        monkeypatch.chdir(tmp_path)

        global_dir = tmp_path / "global_config" / "fdsx"
        global_dir.mkdir(parents=True)
        global_config = global_dir / "config.yaml"
        global_config.write_text(yaml.dump({"default_tasks_dir": "global-tasks"}))

        global_home = tmp_path / "global_config"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(global_home))

        fdsx_dir = tmp_path / ".fdsx"
        fdsx_dir.mkdir()
        project_config = fdsx_dir / "config.yaml"
        project_config.write_text(yaml.dump({"default_tasks_dir": "project-tasks"}))

        project_tasks = tmp_path / "project-tasks"
        project_tasks.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test task")])
        save_task_file(project_tasks / "001-test.yaml", tf)

        _setup_workflows(tmp_path)

        with (
            patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
            patch("fdsx.core.engine.tasks_dir.run_flow", return_value={"result": "ok"}),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["run", "--auto-workflow"])

        assert result.exit_code == 0, f"stderr={result.stderr}"

    def test_tilde_expansion_in_default_tasks_dir(self, tmp_path, monkeypatch):
        """(f) ~ in default_tasks_dir is expanded to home directory."""
        monkeypatch.chdir(tmp_path)

        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        fdsx_dir = tmp_path / ".fdsx"
        fdsx_dir.mkdir()
        config_file = fdsx_dir / "config.yaml"
        config_file.write_text(yaml.dump({"default_tasks_dir": "~/tasks"}))

        home_tasks = fake_home / "tasks"
        home_tasks.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test task")])
        save_task_file(home_tasks / "001-test.yaml", tf)

        _setup_workflows(tmp_path)

        with (
            patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
            patch("fdsx.core.engine.tasks_dir.run_flow", return_value={"result": "ok"}),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            runner = CliRunner()
            result = runner.invoke(app, ["run", "--auto-workflow"])

        assert result.exit_code == 0, f"stderr={result.stderr}"

    def test_explicit_tasks_dir_ignores_config(self, tmp_path, monkeypatch):
        """(h) Explicit --tasks-dir takes precedence over config."""
        monkeypatch.chdir(tmp_path)
        fdsx_dir = tmp_path / ".fdsx"
        fdsx_dir.mkdir()
        config_file = fdsx_dir / "config.yaml"
        config_file.write_text(yaml.dump({"default_tasks_dir": "nonexistent-tasks"}))

        explicit_tasks = tmp_path / "explicit-tasks"
        explicit_tasks.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test task")])
        save_task_file(explicit_tasks / "001-test.yaml", tf)

        _setup_workflows(tmp_path)

        with (
            patch("fdsx.core.selector.get_provider", return_value=MagicMock()),
            patch("fdsx.core.engine.tasks_dir.run_flow", return_value={"result": "ok"}),
            patch("fdsx.core.engine.tasks_dir.display_tasks_dir_summary"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                app,
                ["run", "--tasks-dir", str(explicit_tasks), "--auto-workflow"],
            )

        assert result.exit_code == 0, f"stderr={result.stderr}"

    def test_no_arg_with_nonexistent_config_default_tasks_dir_exits(
        self, tmp_path, monkeypatch
    ):
        """Config default_tasks_dir points to nonexistent dir -> exit 2."""
        monkeypatch.chdir(tmp_path)
        fdsx_dir = tmp_path / ".fdsx"
        fdsx_dir.mkdir()
        config_file = fdsx_dir / "config.yaml"
        config_file.write_text(yaml.dump({"default_tasks_dir": "nonexistent-tasks"}))

        runner = CliRunner()
        result = runner.invoke(app, ["run"])

        assert result.exit_code == 2
        assert "Tasks directory not found" in result.stderr

    def test_no_arg_with_symlinked_tasks_dir_exits(self, tmp_path, monkeypatch):
        """Symlinked tasks dir in config -> exit 2."""
        monkeypatch.chdir(tmp_path)
        real_dir = tmp_path / "real-tasks"
        real_dir.mkdir()
        tf = TaskFile(entries=[TaskEntry(description="test task")])
        save_task_file(real_dir / "001-test.yaml", tf)

        symlink_dir = tmp_path / "tasks-link"
        symlink_dir.symlink_to(real_dir)

        fdsx_dir = tmp_path / ".fdsx"
        fdsx_dir.mkdir()
        config_file = fdsx_dir / "config.yaml"
        config_file.write_text(yaml.dump({"default_tasks_dir": "tasks-link"}))

        runner = CliRunner()
        result = runner.invoke(app, ["run"])

        assert result.exit_code == 2
        assert "symlink" in result.stderr.lower()

    def test_no_arg_with_file_instead_of_dir_exits(self, tmp_path, monkeypatch):
        """default_tasks_dir points to a file instead of dir -> exit 2."""
        monkeypatch.chdir(tmp_path)
        fdsx_dir = tmp_path / ".fdsx"
        fdsx_dir.mkdir()
        config_file = fdsx_dir / "config.yaml"
        config_file.write_text(yaml.dump({"default_tasks_dir": "some-file"}))

        (tmp_path / "some-file").write_text("not a directory")

        runner = CliRunner()
        result = runner.invoke(app, ["run"])

        assert result.exit_code == 2
        assert "must be a directory" in result.stderr.lower()

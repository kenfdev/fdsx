"""Manual selection through the CLI, real editor, engine and persistence."""

from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from fdsx.cli.main import app
from fdsx.models.task import TaskEntry, TaskFile, load_task_file, save_task_file
from fdsx.providers.base import ProviderResult


def workflow(root, name):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": name,
                "start_at": "echo",
                "states": {
                    "echo": {
                        "type": "task",
                        "provider": "system",
                        "command": f"echo executed-{name}",
                        "end": True,
                    }
                },
            }
        )
    )
    return path


@pytest.fixture
def setup(tmp_path):
    workflows = tmp_path / ".fdsx/workflows"
    workflow(workflows, "alpha")
    tasks = tmp_path / ".fdsx/tasks"
    tasks.mkdir()
    save_task_file(
        tasks / "task.yaml", TaskFile(entries=[TaskEntry(description="work")])
    )
    return workflows, tasks


def invoke(args=(), text="", interactive=True):
    with (
        patch("fdsx.core.mode.is_interactive", return_value=interactive),
        patch("fdsx.cli.main.is_interactive", return_value=interactive),
    ):
        return CliRunner().invoke(app, ["run", *args], input=text)


def completed(tasks):
    return load_task_file(tasks / "completed/task.yaml").entries


@pytest.fixture(autouse=True)
def selector():
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(exit_code=0, stdout="beta", stderr=""),
    ) as mock:
        yield mock


@pytest.mark.parametrize(
    "args,config,global_config,manual",
    [
        (["--manual-workflow"], {}, {}, True),
        ([], {"manual_workflow": True}, {}, True),
        ([], {}, {"manual_workflow": True}, True),
        ([], {"manual_workflow": False}, {"manual_workflow": True}, False),
        (["--manual-workflow"], {"auto_workflow": True}, {}, True),
        ([], {"manual_workflow": True, "auto_workflow": True}, {}, True),
        (["--auto-workflow"], {"manual_workflow": True}, {}, False),
        (["--confirm-workflow", "--manual-workflow"], {}, {}, True),
        (["--confirm-workflow"], {"manual_workflow": True}, {}, True),
        ([], {}, {}, False),
        (["--auto-workflow"], {}, {}, False),
    ],
)
@pytest.mark.parametrize("explicit_tasks", [False, True])
def test_mode_precedence_executes_and_saves(
    setup, tmp_path, selector, args, config, global_config, manual, explicit_tasks
):
    workflows, tasks = setup
    workflow(workflows, "beta")
    (tmp_path / ".fdsx/config.yaml").write_text(yaml.safe_dump(config))
    global_dir = tmp_path / "xdg/fdsx"
    global_dir.mkdir(parents=True)
    (global_dir / "config.yaml").write_text(yaml.safe_dump(global_config))
    options = args + (["--tasks-dir", str(tasks)] if explicit_tasks else [])
    result = invoke(options, "1\n1\nc\n" if manual else "c\n")
    assert result.exit_code == 0, result.output
    assert completed(tasks)[0].workflow == ("alpha.yaml" if manual else "beta.yaml")
    assert completed(tasks)[0].status == "completed"
    assert f"executed-{'alpha' if manual else 'beta'}" in result.output
    assert selector.call_count == (0 if manual else 1)
    assert ("WORKFLOW ASSIGNMENTS" in result.output) == ("--auto-workflow" not in args)
    if manual:
        assert "(unassigned)" in result.output
        assert "Auto-selecting" not in result.output
    if "--auto-workflow" in args:
        assert "WORKFLOW ASSIGNMENTS" not in result.output


@pytest.mark.parametrize("interactive", [True, False])
def test_single_candidate_and_saved_assignment_reuse(setup, selector, interactive):
    _, tasks = setup
    result = invoke(["--manual-workflow"], "c\n", interactive)
    assert result.exit_code == 0, result.output
    assert ("WORKFLOW ASSIGNMENTS" in result.output) == interactive
    entry = completed(tasks)[0]
    assert entry.workflow == "alpha.yaml"
    entry.status = "pending"
    save_task_file(tasks / "task.yaml", TaskFile(entries=[entry]))
    result = invoke(["--manual-workflow"], "c\n", interactive)
    assert result.exit_code == 0, result.output
    assert completed(tasks)[0].status == "completed"
    selector.assert_not_called()


def test_unassigned_confirm_rejected_then_cancelled(setup, selector):
    workflows, tasks = setup
    workflow(workflows, "beta")
    with patch("fdsx.providers.system._run_subprocess") as system:
        result = invoke(["--manual-workflow"], "c\nq\n")
    assert result.exit_code == 0, result.output
    assert "Cannot confirm" in result.output
    assert "cancelled" in result.output
    assert load_task_file(tasks / "task.yaml").entries[0].workflow is None
    selector.assert_not_called()
    system.assert_not_called()


@pytest.mark.parametrize(
    "args,interactive,message",
    [
        (["--manual-workflow", "--auto-workflow"], True, "mutually exclusive"),
        (["--auto-workflow", "--confirm-workflow"], True, "mutually exclusive"),
        (["--manual-workflow", "--confirm-workflow"], False, "requires interactive"),
        (["--manual-workflow"], False, "Specify a workflow"),
    ],
)
def test_invalid_modes_do_not_execute(setup, selector, args, interactive, message):
    workflows, tasks = setup
    workflow(workflows, "beta")
    with patch("fdsx.providers.system._run_subprocess") as system:
        result = invoke(args, interactive=interactive)
    assert result.exit_code == 2, result.output
    assert message in result.stderr
    assert load_task_file(tasks / "task.yaml").entries[0].status == "pending"
    selector.assert_not_called()
    system.assert_not_called()


def test_saved_precedes_cli_and_editor_can_change_both(setup, selector):
    workflows, tasks = setup
    beta = workflow(workflows, "beta")
    save_task_file(
        tasks / "task.yaml",
        TaskFile(
            entries=[
                TaskEntry(description="saved", workflow="alpha.yaml"),
                TaskEntry(description="cli"),
            ]
        ),
    )
    result = invoke(
        [str(beta), "--tasks-dir", str(tasks), "--manual-workflow"], "1\n2\n2\n1\nc\n"
    )
    assert result.exit_code == 0, result.output
    assert [e.workflow for e in completed(tasks)] == ["beta.yaml", "alpha.yaml"]
    assert all(e.status == "completed" for e in completed(tasks))
    selector.assert_not_called()


@pytest.mark.parametrize("pick,expected", [(1, "alpha.yaml"), (2, "beta.yaml")])
def test_project_and_global_candidates_round_trip(
    setup, tmp_path, selector, pick, expected
):
    _, tasks = setup
    global_workflows = tmp_path / "xdg/fdsx/workflows"
    duplicate = workflow(global_workflows, "alpha")
    duplicate.write_text(
        duplicate.read_text().replace("executed-alpha", "wrong-global")
    )
    workflow(global_workflows, "beta")
    result = invoke(["--manual-workflow"], f"1\n{pick}\nc\n")
    assert result.exit_code == 0, result.output
    assert completed(tasks)[0].workflow == expected
    assert "wrong-global" not in result.output
    entry = completed(tasks)[0]
    entry.status = "pending"
    save_task_file(tasks / "task.yaml", TaskFile(entries=[entry]))
    result = invoke(["--manual-workflow"], interactive=False)
    assert result.exit_code == 0, result.output
    assert completed(tasks)[0].workflow == expected
    assert "wrong-global" not in result.output
    selector.assert_not_called()


def test_explicit_single_execution_has_no_editor(setup, selector):
    workflows, _ = setup
    result = invoke([str(workflows / "alpha.yaml"), "--manual-workflow"])
    assert result.exit_code == 0, result.output
    assert "WORKFLOW ASSIGNMENTS" not in result.output
    selector.assert_not_called()


@pytest.mark.parametrize("manual", [False, True])
def test_no_candidates_errors_without_execution(setup, selector, manual):
    workflows, _ = setup
    (workflows / "alpha.yaml").unlink()
    with patch("fdsx.providers.system._run_subprocess") as system:
        result = invoke(["--manual-workflow"] if manual else [], interactive=False)
    assert result.exit_code != 0
    assert "workflow" in result.stderr.lower()
    selector.assert_not_called()
    system.assert_not_called()


@pytest.mark.parametrize("configured", [False, True])
def test_newly_queued_tasks_inherit_manual_mode(setup, tmp_path, selector, configured):
    from fdsx.core.engine.tasks_dir import run_flow

    workflows, tasks = setup
    workflow(workflows, "beta")
    calls = 0

    def execute_and_queue(*args, **kwargs):
        nonlocal calls
        result = run_flow(*args, **kwargs)
        calls += 1
        if calls == 1:
            save_task_file(
                tasks / "new.yaml", TaskFile(entries=[TaskEntry(description="new")])
            )
        return result

    if configured:
        (tmp_path / ".fdsx/config.yaml").write_text("manual_workflow: true\n")
    with patch("fdsx.core.engine.tasks_dir.run_flow", side_effect=execute_and_queue):
        result = invoke(
            [] if configured else ["--manual-workflow"], "1\n1\nc\n1\n2\nc\n"
        )
    assert result.exit_code == 0, result.output
    assert completed(tasks)[0].workflow == "alpha.yaml"
    new_entry = load_task_file(tasks / "completed/new.yaml").entries[0]
    assert new_entry.workflow == "beta.yaml"
    assert new_entry.status == "completed"
    selector.assert_not_called()


def test_saved_assignment_precedes_cli_default(setup, selector):
    workflows, tasks = setup
    beta = workflow(workflows, "beta")
    save_task_file(
        tasks / "task.yaml",
        TaskFile(
            entries=[
                TaskEntry(description="saved", workflow="alpha.yaml"),
                TaskEntry(description="cli default"),
            ]
        ),
    )
    result = invoke([str(beta), "--tasks-dir", str(tasks), "--manual-workflow"], "c\n")
    assert result.exit_code == 0, result.output
    assert [e.workflow for e in completed(tasks)] == ["alpha.yaml", "beta.yaml"]
    assert "executed-alpha" in result.output
    assert "executed-beta" in result.output
    selector.assert_not_called()


def test_saved_and_unassigned_rows_keep_saved_choice(setup, selector):
    workflows, tasks = setup
    workflow(workflows, "beta")
    save_task_file(
        tasks / "task.yaml",
        TaskFile(
            entries=[
                TaskEntry(description="saved", workflow="alpha.yaml"),
                TaskEntry(description="unassigned"),
            ]
        ),
    )
    result = invoke(["--manual-workflow"], "2\n2\nc\n")
    assert result.exit_code == 0, result.output
    assert "(unassigned)" in result.output
    assert [e.workflow for e in completed(tasks)] == ["alpha.yaml", "beta.yaml"]
    selector.assert_not_called()


@pytest.mark.parametrize("confirm", [False, True])
def test_configured_manual_noninteractive_rejects_unresolved_tasks(
    setup, tmp_path, selector, confirm
):
    workflows, tasks = setup
    workflow(workflows, "beta")
    (tmp_path / ".fdsx/config.yaml").write_text("manual_workflow: true\n")
    with patch("fdsx.providers.system._run_subprocess") as system:
        result = invoke(["--confirm-workflow"] if confirm else [], interactive=False)
    assert result.exit_code == 2, result.output
    assert (
        "requires interactive" if confirm else "Specify a workflow"
    ) in result.stderr
    assert load_task_file(tasks / "task.yaml").entries[0].status == "pending"
    selector.assert_not_called()
    system.assert_not_called()


def test_engine_manual_overrides_auto_for_direct_callers(setup, selector):
    from fdsx.core.engine import run_tasks_dir

    workflows, tasks = setup
    workflow(workflows, "beta")
    save_task_file(
        tasks / "task.yaml",
        TaskFile(entries=[TaskEntry(description="saved", workflow="alpha.yaml")]),
    )
    with patch("fdsx.core.mode.is_interactive", return_value=False):
        results = run_tasks_dir(None, tasks, auto_workflow=True, manual_workflow=True)
    assert [result["status"] for result in results] == ["completed"]
    assert completed(tasks)[0].workflow == "alpha.yaml"
    selector.assert_not_called()

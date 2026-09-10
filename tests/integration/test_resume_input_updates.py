"""Input-aware recovery observed through real checkpoints and the CLI."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.cli.main import app
from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.engine.recovery import RecoveryValidationError
from fdsx.providers.base import ProviderResult


@pytest.fixture
def stopped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    flow = tmp_path / "flow.yaml"
    flow.write_text(
        yaml.safe_dump(
            {
                "name": "input recovery",
                "description": "input recovery",
                "start_at": "review",
                "states": {
                    "review": {
                        "type": "task",
                        "provider": "claude",
                        "model": "test",
                        "prompt_template": "{task}|{source}",
                        "retry": 0,
                        "result_path": "$.review",
                        "result_file": "$.review_file",
                        "next": "branches",
                    },
                    "branches": {
                        "type": "parallel",
                        "branches": [
                            {
                                "provider": "claude",
                                "model": "test",
                                "prompt_template": "{task}|{source}|{review}",
                                "retry": 0,
                            }
                        ],
                        "result_path": "$.branches",
                        "next": "route",
                    },
                    "route": {
                        "type": "choice",
                        "choices": [
                            {
                                "variable": "$.task",
                                "operator": "equals",
                                "value": "accepted",
                                "next": "done",
                            }
                        ],
                        "default": "stop",
                    },
                    "stop": {
                        "type": "fail",
                        "error": "needs revision",
                        "cause": "review",
                    },
                    "done": {
                        "type": "task",
                        "provider": "system",
                        "command": "echo verified",
                        "result_path": "$.result",
                        "end": True,
                    },
                },
            }
        )
    )
    base = tmp_path / ".fdsx"
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(
            exit_code=0, stdout="FULL REVIEW " * 150, stderr=""
        ),
    ):
        result = run_flow(
            flow,
            inputs={"task": "original", "source": "unchanged.md"},
            thread_id="updates",
            base_dir=base,
        )
    assert result.status == "aborted"
    return base


def saved(base):
    manager = CheckpointManager(base_dir=base)
    item = manager.get_checkpointer().get_tuple(
        {"configurable": {"thread_id": "updates"}}
    )
    return item.checkpoint["channel_values"]


def files(base):
    return {
        str(p.relative_to(base)): p.read_bytes()
        for p in (base / "runs").rglob("*")
        if p.is_file()
    }


def test_approved_update_propagates_and_retains_full_history(stopped):
    base = stopped
    before = saved(base)
    review_file = Path(before["review_file"])
    original_file = review_file.read_bytes()
    calls = []

    def provider(args, **kwargs):
        calls.append(args)
        return ProviderResult(exit_code=0, stdout="new review", stderr="")

    with patch("fdsx.providers.claude._run_subprocess", side_effect=provider):
        result = resume_flow(
            "updates",
            base_dir=base,
            from_state="review",
            input_updates={"task": "accepted"},
            confirm_inputs=lambda *_: True,
        )
    assert result.status == "completed"
    assert result.results["result"] == "verified"
    assert "accepted|unchanged.md" in calls[0]
    assert "accepted|unchanged.md|new review" in calls[1]
    after = saved(base)
    assert after["task"] == "accepted"
    assert after["source"] == "unchanged.md"
    assert after["_meta"]["thread_id"] == "updates"
    assert after["_meta"]["initial_inputs"]["task"] == "original"
    revision = base / "runs" / "updates" / after["_meta"]["input_revisions"][0]
    history = json.loads((revision / "snapshot.json").read_text())
    assert history["saved_values"]["review"] == ("FULL REVIEW " * 150).strip()
    assert history["saved_values"]["task"] == "original"
    assert history["effective_inputs"] == {"task": "accepted", "source": "unchanged.md"}
    assert (
        revision / "files" / "data" / review_file.name
    ).read_bytes() == original_file
    assert review_file.read_text() == "new review"


@pytest.mark.parametrize(
    "updates,target",
    [
        ({"unknown": "value"}, "review"),
        ({"review": "value"}, "review"),
        ({"task": "accepted"}, None),
        ({"task": "original"}, None),
        ({"task": "accepted"}, "absent"),
        ({"task": "accepted"}, "done"),
        ({"task": "accepted"}, "stop"),
    ],
)
def test_invalid_update_leaves_saved_execution_unchanged(stopped, updates, target):
    before, artifacts = saved(stopped), files(stopped)
    with (
        pytest.raises(RecoveryValidationError),
        patch("fdsx.providers.claude._run_subprocess") as provider,
    ):
        resume_flow(
            "updates", base_dir=stopped, from_state=target, input_updates=updates
        )
    provider.assert_not_called()
    assert saved(stopped) == before
    assert files(stopped) == artifacts
    assert not CheckpointManager(base_dir=stopped).is_locked("updates")[0]


@pytest.mark.parametrize(
    "answer,interactive", [("n\n", True), ("y\n", False), ("", True)]
)
def test_cli_refusal_changes_nothing_and_runs_no_hooks(stopped, answer, interactive):
    before, artifacts = saved(stopped), files(stopped)
    with (
        patch("click.testing._NamedTextIOWrapper.isatty", return_value=interactive),
        patch("fdsx.cli.main.execute_run_hooks") as hooks,
        patch("fdsx.providers.claude._run_subprocess") as provider,
    ):
        result = CliRunner().invoke(
            app,
            [
                "resume",
                "--thread-id",
                "updates",
                "--base-dir",
                str(stopped),
                "--from",
                "review",
                "--input",
                "task=accepted",
            ],
            input=answer,
        )
    assert result.exit_code == 1
    assert "-original" in result.stderr
    assert "+accepted" in result.stderr
    assert "Restart state: review" in result.stderr
    assert "retained results may reflect old inputs" in result.stderr
    assert "unchanged.md" not in result.stderr
    hooks.assert_not_called()
    provider.assert_not_called()
    assert saved(stopped) == before
    assert files(stopped) == artifacts
    assert not CheckpointManager(base_dir=stopped).is_locked("updates")[0]


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["--input", "task=discard", "--input", "task=accepted"], "accepted"),
        (["--input", "task="], ""),
    ],
)
def test_cli_approval_uses_run_parsing_conventions(stopped, arguments, expected):
    with (
        patch("click.testing._NamedTextIOWrapper.isatty", return_value=True),
        patch(
            "fdsx.providers.claude._run_subprocess",
            return_value=ProviderResult(exit_code=0, stdout="updated", stderr=""),
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "resume",
                "--thread-id",
                "updates",
                "--base-dir",
                str(stopped),
                "--from",
                "review",
                *arguments,
            ],
            input="y\n",
        )
    assert result.exit_code == (0 if expected == "accepted" else 1), result.output
    assert saved(stopped)["task"] == expected


def test_identical_update_recovers_without_revision(stopped):
    before = saved(stopped)
    review_file = Path(before["review_file"])
    original_file = review_file.read_bytes()
    with (
        patch("click.testing._NamedTextIOWrapper.isatty", return_value=True),
        patch(
            "fdsx.providers.claude._run_subprocess",
            return_value=ProviderResult(exit_code=0, stdout="rerun", stderr=""),
        ),
    ):
        result = CliRunner().invoke(
            app,
            [
                "resume",
                "--thread-id",
                "updates",
                "--base-dir",
                str(stopped),
                "--from",
                "review",
                "--input",
                "task=original",
            ],
            input="y\n",
        )
    assert result.exit_code == 1  # workflow-defined fail state still executes
    assert "Inputs are unchanged" in result.stderr
    assert "Restart state: review" in result.stderr
    assert not saved(stopped)["_meta"].get("input_revisions")
    log = json.loads((stopped / "runs/updates/run.json").read_text())
    assert len(log["recoveries"]) == 1
    assert saved(stopped)["review"] == "rerun"
    snapshot = (
        stopped / "runs/updates" / saved(stopped)["_meta"]["recovery_snapshots"][0]
    )
    history = json.loads((snapshot / "snapshot.json").read_text())
    assert history["saved_values"]["review"] == before["review"]
    assert history["saved_values"]["task"] == "original"
    assert (snapshot / "files/data" / review_file.name).read_bytes() == original_file
    assert review_file.read_text() == "rerun"


def test_malformed_cli_input_is_rejected_without_echoing_value(stopped):
    before = files(stopped)
    result = CliRunner().invoke(
        app, ["resume", "--thread-id", "updates", "--input", "sensitive-invalid"]
    )
    assert result.exit_code == 2
    assert "sensitive-invalid" not in result.output
    assert files(stopped) == before


def test_retained_review_is_available_to_selected_branch(stopped):
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(exit_code=0, stdout="adjudicated", stderr=""),
    ) as provider:
        result = resume_flow(
            "updates",
            base_dir=stopped,
            from_state="branches",
            input_updates={"task": "accepted"},
            confirm_inputs=lambda *_: True,
        )
    assert result.status == "completed"
    assert (
        "accepted|unchanged.md|" + ("FULL REVIEW " * 150).strip()
        in provider.call_args.kwargs["args"]
    )
    assert saved(stopped)["review"] == ("FULL REVIEW " * 150).strip()


def replace_saved_metadata(base, change):
    manager = CheckpointManager(base_dir=base)
    saver = manager.get_checkpointer()
    config = {"configurable": {"thread_id": "updates"}}
    item = saver.get_tuple(config)
    checkpoint = item.checkpoint
    change(checkpoint["channel_values"])
    saver.put(item.config, checkpoint, item.metadata, {})


def test_missing_input_metadata_refuses_without_inference(stopped):
    replace_saved_metadata(stopped, lambda values: values["_meta"].pop("input_keys"))
    before, artifacts = saved(stopped), files(stopped)
    with (
        pytest.raises(RecoveryValidationError, match="metadata"),
        patch("fdsx.providers.claude._run_subprocess") as provider,
    ):
        resume_flow(
            "updates",
            base_dir=stopped,
            from_state="review",
            input_updates={"task": "accepted"},
            confirm_inputs=lambda *_: True,
        )
    provider.assert_not_called()
    assert saved(stopped) == before
    assert files(stopped) == artifacts


def test_locked_update_refuses_without_changes(stopped):
    from fdsx.core.engine.errors import RunLockedError

    manager = CheckpointManager(base_dir=stopped)
    assert manager.acquire_lock("updates")
    before, artifacts = saved(stopped), files(stopped)
    try:
        with pytest.raises(RunLockedError):
            resume_flow(
                "updates",
                base_dir=stopped,
                from_state="review",
                input_updates={"task": "accepted"},
                confirm_inputs=lambda *_: True,
            )
        assert saved(stopped) == before
        assert files(stopped) == artifacts
    finally:
        manager.release_lock("updates")


def test_completed_update_refuses_without_changes(stopped):
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(exit_code=0, stdout="updated", stderr=""),
    ):
        resume_flow(
            "updates",
            base_dir=stopped,
            from_state="review",
            input_updates={"task": "accepted"},
            confirm_inputs=lambda *_: True,
        )
    before, artifacts = saved(stopped), files(stopped)
    with pytest.raises(RecoveryValidationError, match="completed successfully"):
        resume_flow(
            "updates",
            base_dir=stopped,
            from_state="review",
            input_updates={"task": "next"},
            confirm_inputs=lambda *_: True,
        )
    assert saved(stopped) == before
    assert files(stopped) == artifacts


def test_required_inputs_use_proposed_values(stopped):
    # Saved key metadata is authoritative even if its current value is absent.
    replace_saved_metadata(stopped, lambda values: values.pop("source"))
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(exit_code=0, stdout="updated", stderr=""),
    ) as provider:
        result = resume_flow(
            "updates",
            base_dir=stopped,
            from_state="review",
            input_updates={"task": "accepted", "source": "restored"},
            confirm_inputs=lambda *_: True,
        )
    assert result.status == "completed"
    assert "accepted|restored" in provider.call_args_list[0].kwargs["args"]


def test_diff_has_context_and_sanitizes_terminal_controls(stopped):
    old = "\n".join(f"line {i}" for i in range(30))
    replace_saved_metadata(stopped, lambda values: values.update(task=old))
    proposed = old.replace("line 15", "changed\x1b[31m\x07")
    with patch("click.testing._NamedTextIOWrapper.isatty", return_value=True):
        result = CliRunner().invoke(
            app,
            [
                "resume",
                "--thread-id",
                "updates",
                "--base-dir",
                str(stopped),
                "--from",
                "review",
                "--input",
                "task=" + proposed,
            ],
            input="n\n",
        )
    assert result.exit_code == 1
    assert "-line 15" in result.stderr
    assert "+changed" in result.stderr
    assert " line 12" in result.stderr
    assert "line 0" not in result.stderr
    assert "line 29" not in result.stderr
    assert "\x1b" not in result.stderr and "\x07" not in result.stderr


def test_accepted_inputs_survive_a_later_resume(stopped):
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(exit_code=0, stdout="updated", stderr=""),
    ):
        first = resume_flow(
            "updates",
            base_dir=stopped,
            from_state="review",
            input_updates={"task": "revised"},
            confirm_inputs=lambda *_: True,
        )
    assert first.status == "aborted"
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(exit_code=0, stdout="again", stderr=""),
    ) as provider:
        second = resume_flow("updates", base_dir=stopped, from_state="review")
    assert second.status == "aborted"
    assert "revised|unchanged.md" in provider.call_args_list[0].kwargs["args"]
    assert saved(stopped)["_meta"]["input_values"]["task"] == "revised"


def test_cancel_does_not_backfill_metadata_or_reset_progress(stopped):
    replace_saved_metadata(stopped, lambda values: values["_meta"].pop("run_dir"))
    progress = stopped / "runs/updates/map/progress.json"
    progress.parent.mkdir()
    progress.write_text('{"completed_iterations": 3}')
    before, artifacts = saved(stopped), files(stopped)
    with pytest.raises(RecoveryValidationError):
        resume_flow(
            "updates",
            base_dir=stopped,
            from_state="review",
            input_updates={"task": "accepted"},
            confirm_inputs=lambda *_: False,
        )
    assert saved(stopped) == before
    assert files(stopped) == artifacts


def test_allowed_input_result_collision_replaces_current_value(stopped):
    # An author may use a registered input name as a result key as well.
    flow_path = stopped.parent / "flow.yaml"
    flow = yaml.safe_load(flow_path.read_text())
    replace_saved_metadata(
        stopped, lambda values: values.update(task="previous output")
    )
    flow["states"]["branches"]["branches"][0]["prompt_template"] = "{task}|{source}"
    flow["states"]["review"]["result_path"] = "$.task"
    flow["states"]["review"]["prompt_template"] = "{task}|{source}"
    flow_path.write_text(yaml.safe_dump(flow))
    with patch(
        "fdsx.providers.claude._run_subprocess",
        return_value=ProviderResult(exit_code=0, stdout="accepted", stderr=""),
    ) as provider:
        result = resume_flow(
            "updates",
            base_dir=stopped,
            from_state="review",
            input_updates={"task": "first revision"},
            confirm_inputs=lambda *_: True,
        )
    assert result.status == "completed"
    assert "first revision|unchanged.md" in provider.call_args_list[0].kwargs["args"]
    assert saved(stopped)["task"] == "accepted"
    assert saved(stopped)["_meta"]["initial_inputs"]["task"] == "original"


@pytest.mark.parametrize("failure", [OSError, RuntimeError])
@pytest.mark.parametrize("operation", ["click.confirm", "fdsx.display.terminal.print"])
def test_confirmation_error_preserves_execution_and_releases_lock(
    stopped, failure, operation
):
    progress = stopped / "runs/updates/map/progress.json"
    progress.parent.mkdir()
    progress.write_text('{"completed_iterations": 3}')
    before, artifacts = saved(stopped), files(stopped)
    with (
        patch("click.testing._NamedTextIOWrapper.isatty", return_value=True),
        patch(operation, side_effect=failure("private terminal content"), create=True),
        patch("fdsx.cli.main.execute_run_hooks") as run_hooks,
        patch("fdsx.core.engine.resume.execute_workflow_hooks") as workflow_hooks,
        patch("fdsx.providers.claude._run_subprocess") as provider,
    ):
        result = CliRunner().invoke(
            app,
            [
                "resume",
                "--thread-id",
                "updates",
                "--base-dir",
                str(stopped),
                "--from",
                "review",
                "--input",
                "task=accepted",
            ],
            input="y\n",
        )
    assert result.exit_code == 1
    assert "Interactive approval could not be obtained" in result.stderr
    assert "private terminal content" not in result.output
    run_hooks.assert_not_called()
    workflow_hooks.assert_not_called()
    provider.assert_not_called()
    assert saved(stopped) == before
    assert files(stopped) == artifacts
    assert not CheckpointManager(base_dir=stopped).is_locked("updates")[0]


@pytest.mark.parametrize("old,proposed", [(1, "1"), (True, "true"), (None, "null")])
def test_cli_type_changing_replacement_displays_difference(stopped, old, proposed):
    replace_saved_metadata(stopped, lambda values: values.update(task=old))
    with (
        patch("click.testing._NamedTextIOWrapper.isatty", return_value=True),
        patch(
            "fdsx.providers.claude._run_subprocess",
            return_value=ProviderResult(exit_code=0, stdout="rerun", stderr=""),
        ) as provider,
    ):
        result = CliRunner().invoke(
            app,
            [
                "resume",
                "--thread-id",
                "updates",
                "--base-dir",
                str(stopped),
                "--from",
                "review",
                "--input",
                "task=" + proposed,
            ],
            input="y\n",
        )
    assert result.exit_code == 1  # workflow-defined fail state
    assert "--- task (saved)" in result.stderr
    assert "+++ task (proposed)" in result.stderr
    assert "-" + json.dumps(old) in result.stderr
    assert "+" + json.dumps(proposed) in result.stderr
    assert "Inputs are unchanged" not in result.stderr
    assert proposed + "|unchanged.md" in provider.call_args_list[0].kwargs["args"]
    assert saved(stopped)["task"] == proposed

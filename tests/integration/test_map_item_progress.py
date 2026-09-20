"""Public resume behavior for sparse, legacy and failed map items."""

import json
import shlex
from contextlib import suppress
from pathlib import Path

import pytest
import yaml

from fdsx.checkpoint.map_progress import MapProgress
from fdsx.core.engine import resume_flow, run_flow
from fdsx.providers.base import ProviderResult


def workflow(local, fail_fast=True):
    tasks = [
        {
            "type": "task",
            "name": name,
            "provider": "system",
            "command": f"echo {name} {{item}}",
            "retry": 0,
            "result_path": "$.value",
        }
        for name in ("first", "last")
    ]
    iterator = {"states": tasks}
    if local:
        iterator = {"start_at": "first", "output_path": "$.value", "states": {}}
        for task in tasks:
            name = task.pop("name")
            task.update({"next": "last"} if name == "first" else {"end": True})
            iterator["states"][name] = task
    return {
        "name": "item-progress",
        "description": "Resume items independently",
        "start_at": "work",
        "states": {
            "work": {
                "type": "map",
                "items_path": "$.items",
                "iterator": iterator,
                "result_path": "$.results",
                "fail_fast": fail_fast,
                "end": True,
            }
        },
    }


def write(tmp_path, data):
    path = tmp_path / "flow.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.fixture
def provider(monkeypatch):
    calls = []
    control = {"stop": None, "fail": None}

    def execute(**kwargs):
        _, step, item = shlex.split(kwargs["args"][0])
        calls.append((step, item))
        if (step, item) == control["stop"]:
            raise KeyboardInterrupt()
        if (step, item) == control["fail"]:
            return ProviderResult(1, "", "item failed")
        value = f"{step}-{item}"
        kwargs["output_callback"](value)
        return ProviderResult(0, value, "")

    monkeypatch.setattr("fdsx.providers.system._run_subprocess", execute)
    return calls, control


def progress_file(tmp_path):
    return tmp_path / ".fdsx/runs/items/work/progress.json"


def start(tmp_path, local, provider, *, fail_fast=True):
    path = write(tmp_path, workflow(local, fail_fast))
    provider[1]["stop"] = ("last", "B")
    # Engine interruption handling may return an interrupted result.
    with suppress(KeyboardInterrupt):
        run_flow(
            path,
            {"items": ["A", "B", "C"]},
            thread_id="items",
            base_dir=tmp_path / ".fdsx",
        )
    provider[0].clear()
    provider[1]["stop"] = None
    return path


def resume(tmp_path, path):
    return resume_flow("items", tmp_path / ".fdsx", path)


def envelope(index, value):
    return {"index": index, "exit_code": 0, "error": None, "output": value}


@pytest.mark.parametrize("local", [False, True])
def test_sparse_resume_runs_only_missing_item_in_input_order(tmp_path, provider, local):
    path = start(tmp_path, local, provider)
    progress = MapProgress(str(tmp_path / ".fdsx/runs/items"), "work", 1, 3)
    # Exercise noncontiguous completion publication without parallel execution.
    for i, item in [(2, "C"), (1, "B")]:
        progress.collect(
            i, envelope(i, f"last-{item}") if local else f"last-{item}", "success"
        )
    result = resume(tmp_path, path)
    assert provider[0] == [("first", "A"), ("last", "A")]
    assert result.results["results"] == [
        envelope(i, f"last-{v}") if local else f"last-{v}" for i, v in enumerate("ABC")
    ]
    record = json.loads((tmp_path / ".fdsx/runs/items/run.json").read_text())
    current = [s for s in record["states"] if s["name"] == "work"][-1]
    assert (
        current["completed_count"],
        current["reused_count"],
        current["executed_count"],
        current["attempt_count"],
    ) == (3, 2, 1, 2)
    assert [i["item_index"] for i in current["iterations"]] == [0]


@pytest.mark.parametrize("local", [False, True])
def test_interrupted_item_restarts_first_step_and_keeps_old_logs(
    tmp_path, provider, local, capsys
):
    path = start(tmp_path, local, provider)
    logs = tmp_path / ".fdsx/runs/items/logs"
    before = {p: p.read_bytes() for p in logs.rglob("*.log")}
    resume(tmp_path, path)
    assert provider[0] == [("first", "B"), ("last", "B"), ("first", "C"), ("last", "C")]
    assert all(p.read_bytes() == value for p, value in before.items())
    assert len(list(logs.rglob("*.log"))) > len(before)
    assert "2/3" in capsys.readouterr().err


@pytest.mark.parametrize("local", [False, True])
def test_legacy_null_is_reused_without_guessing_and_migrates_on_save(
    tmp_path, provider, local
):
    path = start(tmp_path, local, provider)
    legacy = {"completed_iterations": 1, "results": [None]}
    saved = progress_file(tmp_path)
    saved.write_text(json.dumps(legacy))
    before = saved.read_bytes()
    # Stop before any item can be collected: read must not migrate on disk.
    provider[1]["stop"] = ("first", "B")
    with suppress(KeyboardInterrupt):
        resume(tmp_path, path)
    assert saved.read_bytes() == before
    provider[0].clear()
    provider[1]["stop"] = None
    result = resume(tmp_path, path)
    assert provider[0] == [("first", "B"), ("last", "B"), ("first", "C"), ("last", "C")]
    assert result.results["results"] == [
        None,
        *[
            envelope(i, f"last-{item}") if local else f"last-{item}"
            for i, item in [(1, "B"), (2, "C")]
        ],
    ]
    converted = json.loads(saved.read_text())
    assert converted["version"] == 1
    assert converted["items"]["0"] == {"result": None, "status": "unknown"}


@pytest.mark.parametrize("local", [False, True])
def test_fail_fast_failure_is_retried_and_saved_success_skipped(
    tmp_path, provider, local
):
    path = write(tmp_path, workflow(local))
    provider[1]["fail"] = ("last", "B")
    with pytest.raises(RuntimeError):
        run_flow(
            path, {"items": list("ABC")}, thread_id="items", base_dir=tmp_path / ".fdsx"
        )
    assert set(json.loads(progress_file(tmp_path).read_text())["items"]) == {"0"}
    provider[1]["fail"] = None
    provider[0].clear()
    resume(tmp_path, path)
    assert provider[0] == [("first", "B"), ("last", "B"), ("first", "C"), ("last", "C")]


@pytest.mark.parametrize("local", [False, True])
def test_collected_failure_retains_iterator_failure_policy(tmp_path, provider, local):
    path = start(tmp_path, local, provider, fail_fast=False)
    failure = (
        {"index": 1, "exit_code": 1, "error": "failed", "output": None}
        if local
        else None
    )
    progress_file(tmp_path).write_text(
        json.dumps(
            {
                "version": 1,
                "state_iteration": 1,
                "items": {
                    "0": {"result": None, "status": "success"},
                    "1": {"result": failure, "status": "failure"},
                },
            }
        )
    )
    if local:
        result = resume(tmp_path, path)
        assert result.status == "completed"
        assert result.results["results"][:2] == [None, failure]
    else:
        with pytest.raises(RuntimeError, match="1 of 3"):
            resume(tmp_path, path)
    assert provider[0] == [("first", "C"), ("last", "C")]


@pytest.mark.parametrize("local", [False, True])
def test_failed_save_keeps_previous_progress_and_resume_reexecutes_item(
    tmp_path, provider, local, monkeypatch, capsys
):
    path = start(tmp_path, local, provider)
    original = progress_file(tmp_path).read_bytes()
    replace = Path.replace

    def fail_publish(self, target):
        if Path(target) == progress_file(tmp_path):
            raise OSError("injected disk failure")
        return replace(self, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_publish)
        with pytest.raises(RuntimeError, match="Could not save map item progress"):
            resume(tmp_path, path)
    assert progress_file(tmp_path).read_bytes() == original
    record = json.loads((tmp_path / ".fdsx/runs/items/run.json").read_text())
    current = [s for s in record["states"] if s["name"] == "work"][-1]
    assert current["completed_count"] == 1
    assert not current["iterations"]
    captured = capsys.readouterr()
    assert "map_progress_save_failed" in captured.out + captured.err
    provider[0].clear()
    resume(tmp_path, path)
    assert provider[0][0] == ("first", "B")
    assert set(json.loads(progress_file(tmp_path).read_text())["items"]) == {
        "0",
        "1",
        "2",
    }


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_new_visit_never_reuses_previous_visit_even_after_legacy_resume(
    tmp_path, provider, local, legacy
):
    data = workflow(local)
    data["states"]["work"].pop("end")
    data["states"]["work"]["next"] = "route"
    data["states"].update(
        {
            "route": {
                "type": "choice",
                "choices": [
                    {
                        "variable": "$.again",
                        "operator": "equals",
                        "value": True,
                        "next": "once",
                    }
                ],
                "default": "done",
            },
            "once": {"type": "pass", "parameters": {"$.again": False}, "next": "work"},
            "done": {"type": "pass", "end": True},
        }
    )
    path = write(tmp_path, data)
    provider[1]["stop"] = ("last", "B")
    with suppress(KeyboardInterrupt):
        run_flow(
            path,
            {"items": list("ABC"), "again": True},
            thread_id="items",
            base_dir=tmp_path / ".fdsx",
        )
    if legacy:
        result = envelope(0, "last-A") if local else "last-A"
        progress_file(tmp_path).write_text(
            json.dumps({"completed_iterations": 1, "results": [result]})
        )
    provider[1]["stop"] = None
    provider[0].clear()
    result = resume(tmp_path, path)
    assert result.status == "completed"
    assert provider[0] == [
        (step, item) for item in "BCABC" for step in ("first", "last")
    ]
    assert json.loads(progress_file(tmp_path).read_text())["state_iteration"] == 2


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_explicit_recovery_invalidates_all_item_progress(
    tmp_path, provider, local, legacy
):
    path = write(tmp_path, workflow(local))
    provider[1]["fail"] = ("last", "B")
    with pytest.raises(RuntimeError):
        run_flow(
            path, {"items": list("ABC")}, thread_id="items", base_dir=tmp_path / ".fdsx"
        )
    provider[1]["fail"] = None
    provider[0].clear()
    if legacy:
        progress_file(tmp_path).write_text(
            json.dumps({"completed_iterations": 1, "results": [None]})
        )
    result = resume_flow("items", tmp_path / ".fdsx", path, from_state="work")
    assert result.status == "completed"
    assert provider[0] == [(step, item) for item in "ABC" for step in ("first", "last")]


@pytest.mark.parametrize("local", [False, True])
def test_saved_failure_from_execution_is_not_retried(tmp_path, provider, local):
    data = workflow(local, fail_fast=False)
    path = write(tmp_path, data)
    provider[1].update(fail=("last", "B"), stop=("first", "C"))
    with suppress(KeyboardInterrupt):
        run_flow(
            path, {"items": list("ABC")}, thread_id="items", base_dir=tmp_path / ".fdsx"
        )
    assert (
        json.loads(progress_file(tmp_path).read_text())["items"]["1"]["status"]
        == "failure"
    )
    provider[1].update(fail=None, stop=None)
    provider[0].clear()
    if local:
        assert resume(tmp_path, path).results["results"][1]["exit_code"] == 1
    else:
        with pytest.raises(RuntimeError, match="1 of 3"):
            resume(tmp_path, path)
    assert provider[0] == [("first", "C"), ("last", "C")]


@pytest.mark.parametrize("local", [False, True])
def test_all_legacy_results_do_not_leak_into_failed_new_visit(
    tmp_path, provider, local
):
    path = start(tmp_path, local, provider)
    data = workflow(local)
    data["states"]["work"].pop("end")
    data["states"]["work"]["max_iterations"] = 3
    # Keep a valid termination route in the graph.
    data["states"]["work"]["next"] = "route"
    data["states"]["route"] = {
        "type": "choice",
        "choices": [
            {
                "variable": "$.finished",
                "operator": "equals",
                "value": True,
                "next": "done",
            }
        ],
        "default": "work",
    }
    data["states"]["done"] = {"type": "pass", "end": True}
    write(tmp_path, data)
    progress_file(tmp_path).write_text(
        json.dumps(
            {
                "completed_iterations": 3,
                "results": [
                    envelope(i, f"last-{item}") if local else f"last-{item}"
                    for i, item in enumerate("ABC")
                ],
            }
        )
    )
    provider[1]["fail"] = ("first", "A")
    with pytest.raises(RuntimeError):
        resume(tmp_path, path)
    assert provider[0] == [("first", "A")]
    assert not progress_file(tmp_path).exists()
    provider[1]["fail"] = None
    provider[0].clear()
    data["states"]["work"].pop("next")
    data["states"]["work"]["end"] = True
    write(tmp_path, data)
    resume(tmp_path, path)
    assert provider[0] == [(step, item) for item in "ABC" for step in ("first", "last")]


@pytest.mark.parametrize("local", [False, True])
def test_retry_attempts_are_separate_from_item_counts(
    tmp_path, provider, local, monkeypatch
):
    data = workflow(local, fail_fast=False)
    tasks = data["states"]["work"]["iterator"]["states"]
    for task in tasks.values() if local else tasks:
        task["retry"] = 1
    path = write(tmp_path, data)
    provider[1]["fail"] = ("last", "B")
    waits = []
    monkeypatch.setattr("fdsx.core.compiler.execution.time.sleep", waits.append)
    if local:
        run_flow(
            path, {"items": list("ABC")}, thread_id="items", base_dir=tmp_path / ".fdsx"
        )
    else:
        with pytest.raises(RuntimeError):
            run_flow(
                path,
                {"items": list("ABC")},
                thread_id="items",
                base_dir=tmp_path / ".fdsx",
            )
    record = json.loads((tmp_path / ".fdsx/runs/items/run.json").read_text())
    current = [s for s in record["states"] if s["name"] == "work"][-1]
    assert current["executed_count"] == current["completed_count"] == 3
    assert current["attempt_count"] == 7
    assert current["task_attempts"] == {"0": 2, "1": 3, "2": 2}
    assert current["failure_count"] == 1
    assert waits == [1]
    if local:
        assert [entry["item_index"] for entry in record["local_workflows"]] == [0, 1, 2]
        assert {entry["execution_id"] for entry in record["local_workflows"]} == {
            current["execution_id"]
        }

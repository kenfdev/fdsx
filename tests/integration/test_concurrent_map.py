"""Bounded map behavior through YAML, run/resume and persisted observations."""

import json
import shlex
from threading import Barrier, Event, Lock

import pytest

from fdsx.checkpoint.map_progress import MapProgress
from fdsx.core.cancellation import current_cancellation
from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.engine.validate import FlowValidationError
from fdsx.logging.recorder import RunRecorder
from fdsx.providers.base import ProviderResult
from tests.integration.test_map_item_progress import envelope, workflow, write


@pytest.fixture(params=[False, True], ids=["legacy", "local"])
def local(request):
    return request.param


def configured(tmp_path, local, limit=2, fail_fast=True):
    data = workflow(local, fail_fast)
    data["states"]["work"]["max_concurrency"] = limit
    return write(tmp_path, data)


def run(tmp_path, path, items="ABCD"):
    return run_flow(
        path, {"items": list(items)}, thread_id="items", base_dir=tmp_path / ".fdsx"
    )


def saved(tmp_path):
    return json.loads((tmp_path / ".fdsx/runs/items/work/progress.json").read_text())[
        "items"
    ]


def record(tmp_path):
    return json.loads((tmp_path / ".fdsx/runs/items/run.json").read_text())


def install(monkeypatch, action):
    def execute(**kwargs):
        _, step, item = shlex.split(kwargs["args"][0])
        result = action(step, item)
        kwargs["output_callback"](result.stdout)
        return result

    monkeypatch.setattr("fdsx.providers.system._run_subprocess", execute)


def ok(step, item):
    return ProviderResult(0, f"{step}-{item}", "")


@pytest.mark.parametrize(
    "invalid", [0, -1, 2.0, 1.5, True, False, None, "2", "unlimited"]
)
def test_invalid_limit_rejects_before_provider(tmp_path, monkeypatch, local, invalid):
    path = configured(tmp_path, local, invalid)
    install(monkeypatch, lambda *_: pytest.fail("provider started"))
    with pytest.raises(FlowValidationError):
        run(tmp_path, path)


@pytest.mark.parametrize("limit", [None, 1, 8])
@pytest.mark.parametrize("items", ["", "AB"])
def test_sequential_empty_and_excess_capacity(
    tmp_path, monkeypatch, local, limit, items
):
    data = workflow(local)
    if limit is not None:
        data["states"]["work"]["max_concurrency"] = limit
    active = set()
    lock = Lock()
    calls = []

    def execute(step, item):
        with lock:
            if step == "first":
                active.add(item)
            assert len(active) <= (limit or 1)
            calls.append((step, item))
            if step == "last":
                active.remove(item)
        return ok(step, item)

    install(monkeypatch, execute)
    result = run(tmp_path, write(tmp_path, data), items)
    assert len(result.results["results"]) == len(items)
    assert sorted(calls) == sorted(
        (step, item) for item in items for step in ("first", "last")
    )


def test_refills_before_first_finishes_and_publishes_sparse_results(
    tmp_path, monkeypatch, local
):
    first_started = Event()
    third_started = Event()
    lock = Lock()
    active = set()
    maximum = 0
    steps = {item: [] for item in "ABCD"}

    def execute(step, item):
        nonlocal maximum
        with lock:
            steps[item].append(step)
            if step == "first":
                active.add(item)
                maximum = max(maximum, len(active))
                assert len(active) <= 2
        if step == "first" and item == "A":
            first_started.set()
            assert third_started.wait(5)
        if step == "first" and item == "B":
            assert first_started.wait(5)
        if step == "first" and item == "C":
            assert set(saved(tmp_path)) == {"1"}
            third_started.set()
        if step == "last":
            with lock:
                active.remove(item)
        return ok(step, item)

    install(monkeypatch, execute)
    result = run(tmp_path, configured(tmp_path, local))
    expected = [
        envelope(i, f"last-{v}") if local else f"last-{v}" for i, v in enumerate("ABCD")
    ]
    assert result.results["results"] == expected
    assert maximum == 2
    assert all(value == ["first", "last"] for value in steps.values())
    state = record(tmp_path)["states"][0]
    assert state["completed_count"] == state["executed_count"] == 4
    assert state["attempt_count"] == 8
    assert {i["item_index"] for i in state["iterations"]} == set(range(4))
    for index, item in enumerate("ABCD"):
        directory = (
            tmp_path
            / ".fdsx/runs/items/logs/work/1"
            / state["execution_id"]
            / str(index)
        )
        text = "\n".join(path.read_text() for path in directory.rglob("*.log"))
        assert f"first-{item}" in text and f"last-{item}" in text
        assert all(f"last-{other}" not in text for other in "ABCD" if other != item)


@pytest.mark.parametrize("both_fail", [False, True])
def test_fail_fast_drains_and_resume_reuses_success(
    tmp_path, monkeypatch, local, both_fail
):
    started = Barrier(2, timeout=5)
    failed = Event()
    original = RunRecorder.record_map_iteration_complete

    def completed(self, name, index, status, output):
        original(self, name, index, status, output)
        if index == 1:
            failed.set()

    monkeypatch.setattr(RunRecorder, "record_map_iteration_complete", completed)
    calls = []

    def execute(step, item):
        calls.append((step, item))
        if step == "first":
            started.wait()
        if step == "last" and item == "B":
            return ProviderResult(1, "", "failure-B")
        if step == "last" and item == "A":
            assert failed.wait(5)
            if both_fail:
                return ProviderResult(1, "", "failure-A")
        return ok(step, item)

    install(monkeypatch, execute)
    path = configured(tmp_path, local)
    with pytest.raises(
        RuntimeError, match=r"(?:item|iteration) " + ("0" if both_fail else "1")
    ):
        run(tmp_path, path)
    assert {item for _, item in calls} == {"A", "B"}
    state = record(tmp_path)["states"][0]
    assert len(state["iterations"]) == 2
    if not both_fail:
        assert set(saved(tmp_path)) == {"0"}
    calls.clear()
    install(
        monkeypatch, lambda step, item: (calls.append((step, item)), ok(step, item))[1]
    )
    result = resume_flow("items", tmp_path / ".fdsx", path)
    assert len(result.results["results"]) == 4
    assert {item for _, item in calls} == (set("ABCD") if both_fail else set("BCD"))


@pytest.mark.parametrize("kind", ["save", "unexpected"])
@pytest.mark.parametrize("fail_fast", [False, True])
def test_infrastructure_failure_stops_and_drains_other_success(
    tmp_path, monkeypatch, local, kind, fail_fast
):
    ready = Barrier(2, timeout=5)
    fault = Event()
    original = MapProgress.collect

    def collect(self, index, result, status):
        if kind == "save" and index == 1:
            fault.set()
            raise OSError("disk-fault")
        original(self, index, result, status)

    monkeypatch.setattr(MapProgress, "collect", collect)
    calls = []

    def execute(step, item):
        calls.append((step, item))
        if step == "first":
            ready.wait()
        if item == "A" and step == "last":
            assert fault.wait(5)
        if kind == "unexpected" and item == "B" and step == "last":
            fault.set()
            raise RuntimeError("unexpected-fault")
        return ok(step, item)

    install(monkeypatch, execute)
    path = configured(tmp_path, local, fail_fast=fail_fast)
    with pytest.raises(RuntimeError, match="fault"):
        run(tmp_path, path)
    assert {item for _, item in calls} == {"A", "B"}
    assert set(saved(tmp_path)) == {"0"}
    monkeypatch.setattr(MapProgress, "collect", original)
    calls.clear()
    install(
        monkeypatch, lambda step, item: (calls.append((step, item)), ok(step, item))[1]
    )
    resume_flow("items", tmp_path / ".fdsx", path)
    assert {item for _, item in calls} == set("BCD")


def test_collect_all_failures_preserves_form_policy(tmp_path, monkeypatch, local):
    barrier = Barrier(2, timeout=5)
    calls = []

    def execute(step, item):
        calls.append((step, item))
        if step == "first":
            barrier.wait()
        return (
            ProviderResult(1, "", f"failure-{item}")
            if step == "last"
            else ok(step, item)
        )

    install(monkeypatch, execute)
    path = configured(tmp_path, local, fail_fast=False)
    if local:
        result = run(tmp_path, path)
        assert all(item["exit_code"] == 1 for item in result.results["results"])
    else:
        with pytest.raises(RuntimeError, match="item 0"):
            run(tmp_path, path)
    assert len(calls) == 8
    assert all(value["status"] == "failure" for value in saved(tmp_path).values())
    install(monkeypatch, lambda *_: pytest.fail("collected failure retried"))
    if local:
        resume_flow("items", tmp_path / ".fdsx", path)
    else:
        with pytest.raises(RuntimeError):
            resume_flow("items", tmp_path / ".fdsx", path)


def test_retry_wait_holds_slot_and_interruption_prevents_retry(
    tmp_path, monkeypatch, local
):
    data = workflow(local)
    data["states"]["work"]["max_concurrency"] = 2
    states = data["states"]["work"]["iterator"]["states"]
    for task in states.values() if local else states:
        task["retry"] = 1
    waiting = Event()
    other_started = Event()
    calls = []

    def pause(delay):
        assert delay == 1
        waiting.set()
        assert other_started.wait(5)
        assert {item for _, item in calls} == {"A", "B"}
        current_cancellation.get().stopped.set()

    monkeypatch.setattr("fdsx.core.compiler.execution.retry_wait", pause)

    def execute(step, item):
        calls.append((step, item))
        if item == "B":
            other_started.set()
            assert waiting.wait(5)
        return ProviderResult(1, "", "retry")

    install(monkeypatch, execute)
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, write(tmp_path, data))
    assert sorted(calls) == [("first", "A"), ("first", "B")]
    assert record(tmp_path)["status"] == "interrupted"


def test_sparse_interruption_resumes_only_unfinished_from_first_task(
    tmp_path, monkeypatch, local
):
    published = Event()
    original = MapProgress.collect

    def collect(self, index, result, status):
        original(self, index, result, status)
        if index == 1:
            published.set()

    monkeypatch.setattr(MapProgress, "collect", collect)
    calls = []

    def execute(step, item):
        calls.append((step, item))
        if item == "A" and step == "last":
            assert published.wait(5)
            assert "0" not in saved(tmp_path)
            current_cancellation.get().stopped.set()
        return ok(step, item)

    install(monkeypatch, execute)
    path = configured(tmp_path, local)
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, path, "AB")
    assert set(saved(tmp_path)) == {"1"}
    calls.clear()
    install(
        monkeypatch, lambda step, item: (calls.append((step, item)), ok(step, item))[1]
    )
    result = resume_flow("items", tmp_path / ".fdsx", path)
    assert calls == [("first", "A"), ("last", "A")]
    assert len(result.results["results"]) == 2


def test_retry_success_keeps_capacity_and_does_not_fail_fast(
    tmp_path, monkeypatch, local
):
    ready = Barrier(2, timeout=5)
    release = Event()
    calls = []
    waits = []

    def pause(delay):
        waits.append(delay)
        assert {item for _, item in calls} == {"A", "B"}
        release.set()

    monkeypatch.setattr("fdsx.core.compiler.execution.retry_wait", pause)
    data = workflow(local)
    data["states"]["work"]["max_concurrency"] = 2
    states = data["states"]["work"]["iterator"]["states"]
    for task in states.values() if local else states:
        task["retry"] = 1

    def execute(step, item):
        calls.append((step, item))
        if step == "first" and item in "AB" and calls.count((step, item)) == 1:
            ready.wait()
            if item == "A":
                return ProviderResult(1, "", "retry-me")
            assert release.wait(5)
        return ok(step, item)

    install(monkeypatch, execute)
    assert run(tmp_path, write(tmp_path, data)).status == "completed"
    assert waits == [1]
    assert calls.count(("first", "A")) == 2
    assert len(calls) == 9


def test_parallel_then_map_preserves_parent_and_nested_results(
    tmp_path, monkeypatch, local
):
    data = workflow(local)
    work = data["states"]["work"]
    work.update(max_concurrency=2, result_path="$.nested.results")
    data["start_at"] = "parallel"
    data["states"]["parallel"] = {
        "type": "parallel",
        "branches": [{"provider": "system", "command": "echo parent P", "retry": 0}],
        "result_path": "$.parallel",
        "next": "work",
    }
    data["states"]["work"].pop("end")
    data["states"]["work"]["next"] = "after"
    data["states"]["after"] = {
        "type": "pass",
        "parameters": {"$.parent_value": "{value}", "$.kept": "{nested.keep}"},
        "end": True,
    }
    barrier = Barrier(2, timeout=5)

    def execute(step, item):
        if step == "first":
            barrier.wait()
        return ok(step, item)

    install(monkeypatch, execute)
    path = write(tmp_path, data)
    result = run_flow(
        path,
        {"items": list("AB"), "value": "parent", "nested": {"keep": 7}},
        base_dir=tmp_path / ".fdsx",
    )
    assert result.results["parallel"][0]["output"] == "parent-P"
    assert len(result.results["nested"]["results"]) == 2
    assert result.results["kept"] == "7"
    assert result.results["parent_value"] == "parent"


def test_legacy_progress_resumes_remaining_items_concurrently(
    tmp_path, monkeypatch, local
):
    path = configured(tmp_path, local)
    install(monkeypatch, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, path)
    progress = tmp_path / ".fdsx/runs/items/work/progress.json"
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text(
        json.dumps(
            {
                "completed_iterations": 1,
                "results": [envelope(0, "last-A") if local else "last-A"],
            }
        )
    )
    barrier = Barrier(2, timeout=5)
    calls = []

    def execute(step, item):
        calls.append((step, item))
        if step == "first" and item in "BC":
            barrier.wait()
        return ok(step, item)

    install(monkeypatch, execute)
    result = resume_flow("items", tmp_path / ".fdsx", path)
    assert len(result.results["results"]) == 4
    assert {item for _, item in calls} == set("BCD")
    assert len(calls) == 6


@pytest.mark.parametrize("fail_fast", [False, True])
def test_save_error_outranks_item_failure_without_losing_diagnostics(
    tmp_path, monkeypatch, local, fail_fast
):
    ready = Barrier(3, timeout=5)
    original = MapProgress.collect

    def collect(self, index, result, status):
        if index == 1:
            raise OSError("disk-fault")
        original(self, index, result, status)

    monkeypatch.setattr(MapProgress, "collect", collect)

    def execute(step, item):
        if step == "first":
            ready.wait()
        if step == "last" and item == "A":
            return ProviderResult(1, "", "ordinary-failure")
        return ok(step, item)

    install(monkeypatch, execute)
    path = configured(tmp_path, local, limit=3, fail_fast=fail_fast)
    with pytest.raises(RuntimeError, match="disk-fault"):
        run(tmp_path, path, "ABC")
    state = record(tmp_path)["states"][0]
    assert any(
        item["item_index"] == 0 and item["status"] == "error"
        for item in state["iterations"]
    )
    assert state["execution_errors"] == [{"item_index": 1, "error": "disk-fault"}]
    assert set(saved(tmp_path)) == ({"2"} if fail_fast else {"0", "2"})


def test_new_visit_and_explicit_recovery_do_not_reuse_prior_visit(
    tmp_path, monkeypatch, local
):
    data = workflow(local)
    work = data["states"]["work"]
    work.update(max_concurrency=2, next="route")
    work.pop("end")
    data["states"].update(
        {
            "route": {
                "type": "choice",
                "choices": [
                    {
                        "variable": "$.again",
                        "operator": "equals",
                        "value": True,
                        "next": "reset",
                    }
                ],
                "default": "done",
            },
            "reset": {"type": "pass", "parameters": {"$.again": False}, "next": "work"},
            "done": {
                "type": "fail",
                "error": "test-terminal",
                "cause": "recovery fixture",
            },
        }
    )
    barrier = Barrier(2, timeout=5)
    calls = []

    def execute(step, item):
        calls.append((step, item))
        if step == "first":
            barrier.wait()
        return ok(step, item)

    install(monkeypatch, execute)
    path = write(tmp_path, data)
    run_flow(
        path,
        {"items": list("AB"), "again": True},
        thread_id="items",
        base_dir=tmp_path / ".fdsx",
    )
    assert len(calls) == 8
    assert all(
        calls.count((step, item)) == 2 for step in ("first", "last") for item in "AB"
    )
    progress = tmp_path / ".fdsx/runs/items/work/progress.json"
    assert json.loads(progress.read_text())["state_iteration"] == 2
    calls.clear()
    resume_flow("items", tmp_path / ".fdsx", path, from_state="work")
    assert sorted(calls) == [
        ("first", "A"),
        ("first", "B"),
        ("last", "A"),
        ("last", "B"),
    ]


def test_interrupt_after_successful_task_prevents_next_task_and_drains_before_finalize(
    tmp_path, monkeypatch, local
):
    from fdsx.checkpoint.manager import CheckpointManager

    ready = Barrier(2, timeout=5)
    lock = Lock()
    active = 0
    calls = []
    original_finalize = RunRecorder.finalize
    original_release = CheckpointManager.release_lock

    def finalize(self, *args, **kwargs):
        assert active == 0
        return original_finalize(self, *args, **kwargs)

    def release(self, *args, **kwargs):
        assert active == 0
        return original_release(self, *args, **kwargs)

    monkeypatch.setattr(RunRecorder, "finalize", finalize)
    monkeypatch.setattr(CheckpointManager, "release_lock", release)

    def execute(step, item):
        nonlocal active
        with lock:
            active += 1
            calls.append((step, item))
        try:
            ready.wait()
            cancellation = current_cancellation.get()
            if item == "B":
                cancellation.stopped.set()
            assert cancellation.stopped.wait(5)
            return ok(step, item)
        finally:
            with lock:
                active -= 1

    install(monkeypatch, execute)
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, configured(tmp_path, local))
    assert sorted(calls) == [("first", "A"), ("first", "B")]
    assert record(tmp_path)["status"] == "interrupted"

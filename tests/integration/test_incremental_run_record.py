"""Run metadata survives without terminal cleanup."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.core.engine import resume_flow, run_flow
from fdsx.logging import RunRecorder
from fdsx.providers.base import ProviderResult


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "flow.yaml"
    path.write_text("""name: incremental
description: Test incremental records
start_at: first
states:
  first:
    type: task
    provider: system
    command: echo first
    next: second
  second:
    type: task
    provider: system
    command: echo second
    end: true
""")
    return path


def test_record_exists_at_start_and_after_each_state(flow, tmp_path):
    base = tmp_path / ".fdsx"
    record = base / "runs" / "incremental" / "run.json"
    calls = 0

    def execute(**kwargs):
        nonlocal calls
        saved = json.loads(record.read_text())
        assert saved["status"] == "running"
        assert [state["name"] for state in saved["states"]] == (
            [] if calls == 0 else ["first"]
        )
        calls += 1
        return ProviderResult(exit_code=0, stdout="ok", stderr="")

    with patch("fdsx.providers.system._run_subprocess", side_effect=execute):
        run_flow(flow, thread_id="incremental", base_dir=base)
    saved = json.loads(record.read_text())
    assert [state["name"] for state in saved["states"]] == ["first", "second"]
    assert saved["status"] == "completed"


def test_existing_checkpoint_resumes_without_run_record(flow, tmp_path):
    base = tmp_path / ".fdsx"
    fake = ProviderResult(exit_code=0, stdout="ok", stderr="")
    with (
        patch(
            "fdsx.providers.system._run_subprocess", side_effect=[fake, SystemExit(130)]
        ),
        pytest.raises(SystemExit),
    ):
        run_flow(flow, thread_id="legacy", base_dir=base)
    (base / "runs" / "legacy" / "run.json").unlink()
    with patch("fdsx.providers.system._run_subprocess", return_value=fake) as execute:
        result = resume_flow("legacy", base_dir=base)
    assert result.status == "completed"
    assert execute.call_count == 1


@pytest.mark.parametrize("stop_at", [1, 2])
def test_sigkill_run_resumes_from_available_checkpoint(flow, tmp_path, stop_at):
    base = tmp_path / ".fdsx"
    ready = tmp_path / "ready"
    script = """
import os
import signal
import sys
from pathlib import Path
from unittest.mock import patch
from fdsx.core.engine import run_flow
from fdsx.providers.base import ProviderResult
calls = 0
def execute(**kwargs):
    global calls
    calls += 1
    if calls == int(sys.argv[3]):
        Path(sys.argv[2]).write_text(str(os.getpid()))
        signal.pause()
    return ProviderResult(exit_code=0, stdout="ok", stderr="")
with patch("fdsx.providers.system._run_subprocess", side_effect=execute):
    run_flow(Path(sys.argv[1]), thread_id="killed", base_dir=Path(sys.argv[4]))
"""
    # Run from the project so uv resolves this checkout, while all artifacts use tmp_path.
    project = Path(__file__).resolve().parents[2]
    with (tmp_path / "child.log").open("w") as output:
        process = subprocess.Popen(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-c",
                script,
                str(flow),
                str(ready),
                str(stop_at),
                str(base),
            ],
            cwd=project,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 15
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert ready.exists(), (tmp_path / "child.log").read_text()
            os.kill(int(ready.read_text()), signal.SIGKILL)
            process.wait(timeout=10)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
    saved = json.loads((base / "runs" / "killed" / "run.json").read_text())
    assert saved["status"] == "running"
    fake = ProviderResult(exit_code=0, stdout="resumed", stderr="")
    with patch("fdsx.providers.system._run_subprocess", return_value=fake) as execute:
        result = resume_flow("killed", base_dir=base)
    assert result.status == "completed"
    # Checkpoint writes are asynchronous: an immediate kill can leave the
    # previous checkpoint, even though the latest run record was published.
    assert 3 - stop_at <= execute.call_count <= 2
    saved = json.loads((base / "runs" / "killed" / "run.json").read_text())
    assert [s["name"] for s in saved["states"]] == (
        (["first"] if stop_at == 2 else [])
        + (["first"] if execute.call_count == 2 else [])
        + ["second"]
    )


def test_failed_replacement_preserves_previous_record(tmp_path):
    recorder = RunRecorder(thread_id="atomic", flow_name="test")
    path = recorder.save(tmp_path)
    previous = path.read_bytes()
    recorder.record_state_start("first", "task")
    with (
        patch(
            "fdsx.logging.recorder.Path.replace", side_effect=OSError("disk failure")
        ),
        pytest.raises(OSError, match="disk failure"),
    ):
        recorder.save(tmp_path)
    assert path.read_bytes() == previous
    recorder.save(tmp_path)
    assert len(json.loads(path.read_text())["states"]) == 1


def test_repeated_saves_merge_previous_attempt_only_once(tmp_path):
    first = RunRecorder(thread_id="history", flow_name="test")
    first.record_state_start("first", "task")
    first.save(tmp_path)
    resumed = RunRecorder(thread_id="history", flow_name="test")
    resumed.record_state_start("second", "task")
    resumed.save(tmp_path)
    path = resumed.save(tmp_path)
    assert [s["name"] for s in json.loads(path.read_text())["states"]] == [
        "first",
        "second",
    ]

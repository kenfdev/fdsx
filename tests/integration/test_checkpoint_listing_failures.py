"""Checkpoint listing keeps run metadata and reports recoverable failures."""

import json
import sqlite3
from unittest.mock import patch

import pytest

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core.engine import run_flow
from fdsx.core.engine.validate import FlowValidationError


@pytest.fixture
def completed_run(tmp_path):
    flow = tmp_path / "flow.yaml"
    flow.write_text(
        "name: Listing test\ndescription: Offline listing fixture\nstart_at: done\nstates:\n"
        "  done:\n    type: pass\n    end: true\n"
    )
    base = tmp_path / ".fdsx"
    run_flow(flow, thread_id="listing-test", base_dir=base)
    return CheckpointManager(base_dir=base)


def test_corrupt_database_preserves_run_metadata_and_warns(tmp_path, caplog):
    manager = CheckpointManager(base_dir=tmp_path / ".fdsx")
    database = manager.checkpoints_dir / "checkpoints.db"
    database.write_bytes(b"not a sqlite database")
    run_dir = manager.base_dir / "runs/listing-test"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({"flow_name": "Listing test", "status": "completed"})
    )

    threads = manager.list_threads()

    assert threads[0]["thread_id"] == "listing-test"
    assert threads[0]["flow_name"] == "Listing test"
    assert threads[0]["status"] == "completed"
    assert "checkpoint_thread_listing_failed" in caplog.text
    assert "checkpoint_read_failed" in caplog.text


def test_invalid_config_preserves_run_metadata_and_warns(completed_run, caplog):
    (completed_run.base_dir / "config.yaml").write_text("profiles: [invalid\n")

    threads = completed_run.list_threads()

    assert threads[0]["status"] == "completed"
    assert "checkpoint_profiles_unavailable" in caplog.text


@pytest.mark.parametrize(
    ("target", "error", "event"),
    [
        (
            "fdsx.core.compiler.compile_flow",
            FlowValidationError("invalid workflow"),
            "checkpoint_snapshot_unavailable",
        ),
        (
            "langgraph.checkpoint.sqlite.SqliteSaver.get_tuple",
            sqlite3.DatabaseError("invalid checkpoint"),
            "checkpoint_read_failed",
        ),
    ],
)
def test_snapshot_failure_uses_run_log_and_warns(
    completed_run, caplog, target, error, event
):
    with patch(target, side_effect=error):
        threads = completed_run.list_threads()

    run_log = json.loads(
        (completed_run.base_dir / "runs/listing-test/run.json").read_text()
    )
    assert threads[0]["flow_name"] == run_log["flow_name"]
    assert threads[0]["status"] == "completed"
    assert event in caplog.text

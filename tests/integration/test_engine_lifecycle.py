from typing import Any
from unittest.mock import patch

import pytest
import structlog.contextvars
import structlog.testing

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core.engine import (
    CheckpointNotFoundError,
    FlowExecutionError,
    RunLockedError,
    resume_flow,
    run_flow,
)
from fdsx.logging import RunRecorder
from fdsx.providers.system import SystemProvider
from tests import FIXTURES_DIR


def test_fresh_run_emits_one_structured_completion_event(tmp_path) -> None:
    thread_id = "lifecycle-fresh-completion"

    with structlog.testing.capture_logs() as logs:
        result = run_flow(
            FIXTURES_DIR / "simple_flow.yaml",
            thread_id=thread_id,
            base_dir=tmp_path / ".fdsx",
            quiet=True,
        )

    completion_events = [
        entry for entry in logs if entry.get("event") == "flow_execution_completed"
    ]

    assert result.status == "completed"
    assert len(completion_events) == 1
    event = completion_events[0]
    assert event["thread_id"] == thread_id
    assert event["flow_name"] == "Simple Plan-Implement-Review Flow"
    assert event["status"] == "completed"
    assert event["states_run"] == 3
    assert event["duration_seconds"] >= 0


def test_resumed_run_emits_one_structured_completion_event(tmp_path) -> None:
    thread_id = "lifecycle-resume-completion"
    base_dir = tmp_path / ".fdsx"
    flow_path = FIXTURES_DIR / "checkpoint_flow.yaml"
    original_execute = SystemProvider().execute
    call_count = 0

    def crash_on_second_state(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("simulated provider crash")
        return original_execute(*args, **kwargs)

    with (
        pytest.raises(RuntimeError, match="Flow execution failed"),
        patch.object(SystemProvider, "execute", side_effect=crash_on_second_state),
    ):
        run_flow(flow_path, thread_id=thread_id, base_dir=base_dir, quiet=True)

    with structlog.testing.capture_logs() as logs:
        result = resume_flow(thread_id, base_dir=base_dir, flow_path=flow_path)

    completion_events = [
        entry for entry in logs if entry.get("event") == "flow_execution_completed"
    ]

    assert result.status == "completed"
    assert len(completion_events) == 1
    event = completion_events[0]
    assert event["thread_id"] == thread_id
    assert event["flow_name"] == "Checkpoint Test Flow"
    assert event["status"] == "completed"
    assert event["states_run"] == 4
    assert event["duration_seconds"] >= 0


def test_resume_missing_checkpoint_raises_typed_engine_error(tmp_path) -> None:
    with pytest.raises(
        CheckpointNotFoundError,
        match="No checkpoint found for thread ID missing-thread",
    ):
        resume_flow("missing-thread", base_dir=tmp_path / ".fdsx")


def test_fresh_run_locked_thread_raises_typed_engine_error(tmp_path) -> None:
    base_dir = tmp_path / ".fdsx"
    thread_id = "lifecycle-locked-thread"
    manager = CheckpointManager(base_dir)
    manager.acquire_lock(thread_id)

    with pytest.raises(RunLockedError, match="locked by PID"):
        run_flow(
            FIXTURES_DIR / "simple_flow.yaml",
            thread_id=thread_id,
            base_dir=base_dir,
            quiet=True,
        )


def test_fresh_run_wraps_unexpected_failure_in_engine_error(tmp_path) -> None:
    thread_id = "lifecycle-execution-error"

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(
            FlowExecutionError,
            match="Flow execution failed",
        ) as raised,
        patch.object(
            SystemProvider,
            "execute",
            side_effect=OSError("provider transport failed"),
        ),
    ):
        run_flow(
            FIXTURES_DIR / "simple_flow.yaml",
            thread_id=thread_id,
            base_dir=tmp_path / ".fdsx",
            quiet=True,
        )

    assert isinstance(raised.value.__cause__, OSError)
    completion_events = [
        entry for entry in logs if entry.get("event") == "flow_execution_completed"
    ]
    assert len(completion_events) == 1
    event = completion_events[0]
    assert event["thread_id"] == thread_id
    assert event["status"] == "failed"
    assert event["states_run"] == 1


def test_fresh_run_binds_and_clears_structured_log_context(tmp_path) -> None:
    thread_id = "lifecycle-log-context"
    observed_contexts: list[dict[str, Any]] = []
    original_execute = SystemProvider().execute

    def capture_context(*args: Any, **kwargs: Any) -> Any:
        observed_contexts.append(structlog.contextvars.get_contextvars())
        return original_execute(*args, **kwargs)

    structlog.contextvars.clear_contextvars()
    with patch.object(SystemProvider, "execute", side_effect=capture_context):
        run_flow(
            FIXTURES_DIR / "simple_flow.yaml",
            thread_id=thread_id,
            base_dir=tmp_path / ".fdsx",
            quiet=True,
        )

    assert observed_contexts
    assert all(context["thread_id"] == thread_id for context in observed_contexts)
    assert all(
        context["flow_name"] == "Simple Plan-Implement-Review Flow"
        for context in observed_contexts
    )
    assert structlog.contextvars.get_contextvars() == {}


def test_fresh_run_releases_lock_when_checkpoint_setup_fails(tmp_path) -> None:
    base_dir = tmp_path / ".fdsx"
    thread_id = "lifecycle-checkpoint-setup-failure"
    manager = CheckpointManager(base_dir)
    (manager.checkpoints_dir / "checkpoints.db").write_bytes(b"not sqlite")

    with pytest.raises(FlowExecutionError):
        run_flow(
            FIXTURES_DIR / "simple_flow.yaml",
            thread_id=thread_id,
            base_dir=base_dir,
            quiet=True,
        )

    assert CheckpointManager(base_dir).is_locked(thread_id) == (False, None)


def test_failure_cleanup_does_not_replace_provider_error(tmp_path) -> None:
    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(FlowExecutionError) as raised,
        patch.object(
            SystemProvider,
            "execute",
            side_effect=OSError("primary provider failure"),
        ),
        patch.object(
            RunRecorder,
            "save",
            side_effect=OSError("secondary recorder failure"),
        ),
    ):
        run_flow(
            FIXTURES_DIR / "simple_flow.yaml",
            thread_id="lifecycle-secondary-failure",
            base_dir=tmp_path / ".fdsx",
            quiet=True,
        )

    assert str(raised.value.__cause__) == "primary provider failure"
    assert any(
        entry.get("event") == "flow_failure_cleanup_failed"
        and entry.get("step") == "recorder_save"
        for entry in logs
    )


def test_interrupted_run_emits_completion_event_and_releases_lock(tmp_path) -> None:
    base_dir = tmp_path / ".fdsx"
    thread_id = "lifecycle-interrupted"

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(SystemExit),
        patch.object(SystemProvider, "execute", side_effect=SystemExit(130)),
    ):
        run_flow(
            FIXTURES_DIR / "simple_flow.yaml",
            thread_id=thread_id,
            base_dir=base_dir,
            quiet=True,
        )

    completion_events = [
        entry for entry in logs if entry.get("event") == "flow_execution_completed"
    ]
    assert [event["status"] for event in completion_events] == ["interrupted"]
    assert CheckpointManager(base_dir).is_locked(thread_id) == (False, None)


def test_resumed_provider_failure_uses_same_engine_error_and_cleanup(tmp_path) -> None:
    thread_id = "lifecycle-resume-failure"
    base_dir = tmp_path / ".fdsx"
    flow_path = FIXTURES_DIR / "checkpoint_flow.yaml"
    original_execute = SystemProvider().execute
    call_count = 0

    def crash_after_checkpoint(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("initial interruption")
        return original_execute(*args, **kwargs)

    with (
        pytest.raises(FlowExecutionError),
        patch.object(SystemProvider, "execute", side_effect=crash_after_checkpoint),
    ):
        run_flow(flow_path, thread_id=thread_id, base_dir=base_dir, quiet=True)

    with (
        structlog.testing.capture_logs() as logs,
        pytest.raises(FlowExecutionError, match="Flow resume failed") as raised,
        patch.object(
            SystemProvider,
            "execute",
            side_effect=OSError("resumed provider failure"),
        ),
    ):
        resume_flow(thread_id, base_dir=base_dir, flow_path=flow_path)

    assert str(raised.value.__cause__) == "resumed provider failure"
    assert any(
        entry.get("event") == "flow_execution_completed"
        and entry.get("status") == "failed"
        for entry in logs
    )
    assert CheckpointManager(base_dir).is_locked(thread_id) == (False, None)


def test_abort_cleanup_failure_does_not_replace_aborted_result(tmp_path) -> None:
    flow_path = tmp_path / "abort.yaml"
    flow_path.write_text(
        """
name: lifecycle-abort
description: Exercises abort cleanup handling
start_at: stop
states:
  stop:
    type: fail
    error: ExpectedFailure
    cause: requested by test
""".strip()
    )

    with (
        structlog.testing.capture_logs() as logs,
        patch.object(
            RunRecorder,
            "save",
            side_effect=OSError("secondary recorder failure"),
        ),
    ):
        result = run_flow(
            flow_path,
            thread_id="lifecycle-abort-cleanup",
            base_dir=tmp_path / ".fdsx",
            quiet=True,
        )

    assert result.status == "aborted"
    assert result.abort_state == "stop"
    assert any(entry.get("event") == "flow_terminal_cleanup_failed" for entry in logs)

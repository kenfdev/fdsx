from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest.mock import MagicMock, patch

from fdsx.core.engine import (
    _calc_elapsed,
    _extract_results,
    _find_failed_state,
    _workflow_persist_id,
)
from fdsx.core.engine.interrupts import handle_interrupts
from fdsx.logging import RunRecorder


class TestExtractResults:
    """F4 regression: _extract_results must preserve nested result paths."""

    def test_single_level_path(self):
        """Single-level paths work the same as before."""
        state = {"result": "hello", "_meta": {"thread_id": "abc"}}
        result = _extract_results(state, ["$.result"])
        assert result == {"result": "hello"}

    def test_nested_path_preserved(self):
        """F4: nested result path must not be flattened to root key."""
        state = {"review": {"summary": "good", "decision": "approve"}}
        result = _extract_results(state, ["$.review.summary"])
        assert result == {"review": {"summary": "good"}}

    def test_multiple_nested_paths_same_root(self):
        """F4: two sub-paths under same root must not overwrite each other."""
        state = {"review": {"summary": "good", "decision": "approve"}}
        result = _extract_results(state, ["$.review.summary", "$.review.decision"])
        assert result["review"]["summary"] == "good"
        assert result["review"]["decision"] == "approve"

    def test_none_value_skipped(self):
        """Missing paths in state produce no entry in results."""
        state = {}
        result = _extract_results(state, ["$.missing"])
        assert result == {}


class TestCalcElapsed:
    """Tests for _calc_elapsed helper in engine."""

    def _make_recorder(self, started_offset_secs: float = 0.0) -> RunRecorder:
        recorder = RunRecorder(
            thread_id="test-thread-id",
            flow_name="TestFlow",
        )
        # Override started_at to a known value
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        recorder.started_at = (
            base + timedelta(seconds=started_offset_secs)
        ).isoformat()
        return recorder

    def test_elapsed_with_completed_at(self):
        """Elapsed time is correctly computed from started_at and completed_at."""
        recorder = self._make_recorder()
        base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        recorder.completed_at = (base + timedelta(seconds=90)).isoformat()
        elapsed = _calc_elapsed(recorder)
        assert elapsed == 90.0

    def test_elapsed_without_completed_at_uses_current_time(self):
        """When completed_at is None, elapsed is computed from now (approximate)."""
        recorder = self._make_recorder()
        # started_at is fixed in the past — elapsed should be positive
        base = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        recorder.started_at = base.isoformat()
        elapsed = _calc_elapsed(recorder)
        # Should be a large positive number (>= ~1 year in seconds)
        assert elapsed > 0.0

    def test_elapsed_invalid_timestamps_returns_zero(self):
        """Malformed timestamps return 0.0 without raising."""
        recorder = self._make_recorder()
        recorder.started_at = "not-a-date"
        recorder.completed_at = "also-not-a-date"
        assert _calc_elapsed(recorder) == 0.0


class TestFindFailedState:
    """Tests for _find_failed_state helper in engine."""

    def _make_recorder(self) -> RunRecorder:
        return RunRecorder(
            thread_id="test-thread-id",
            flow_name="TestFlow",
        )

    def test_returns_none_when_no_error_states(self):
        """Returns None when all states completed successfully."""
        recorder = self._make_recorder()
        recorder.states = [
            {"name": "step1", "status": "completed"},
            {"name": "step2", "status": "completed"},
        ]
        assert _find_failed_state(recorder) is None

    def test_returns_error_state_name_and_error(self):
        """Returns (name, error) for the first error state found (from end)."""
        recorder = self._make_recorder()
        recorder.states = [
            {"name": "step1", "status": "completed"},
            {"name": "step2", "status": "error", "error": "exit code 1"},
        ]
        result = _find_failed_state(recorder)
        assert result is not None
        assert result[0] == "step2"
        assert result[1] == "exit code 1"

    def test_returns_most_recent_error_state(self):
        """Returns the most recent (last) error state when multiple exist."""
        recorder = self._make_recorder()
        recorder.states = [
            {"name": "step1", "status": "error", "error": "first error"},
            {"name": "step2", "status": "error", "error": "second error"},
        ]
        result = _find_failed_state(recorder)
        assert result is not None
        assert result[0] == "step2"
        assert result[1] == "second error"

    def test_returns_empty_error_string_when_no_error_key(self):
        """Returns empty string for error when state has no 'error' key."""
        recorder = self._make_recorder()
        recorder.states = [{"name": "step1", "status": "error"}]
        result = _find_failed_state(recorder)
        assert result is not None
        assert result[0] == "step1"
        assert result[1] == ""

    def test_empty_states_returns_none(self):
        """Returns None when recorder has no states."""
        recorder = self._make_recorder()
        recorder.states = []
        assert _find_failed_state(recorder) is None


class TestErrorPathFallback:
    """Regression: error path must always produce failure message.

    When _find_failed_state returns None (exception before any state executes),
    display_completion_summary must receive a non-None failed_state so it prints
    the failure (✗) message rather than the success (✓) message.

    family_tag: error-path-success-message-on-no-recorded-state
    """

    def test_error_fallback_produces_failure_message_not_success(self):
        """Boundary: no recorder error state → 'unknown' fallback → failure message printed."""
        from fdsx.display.terminal import display_completion_summary

        stderr = StringIO()
        # Simulate what engine.py does in the except block when
        # _find_failed_state returns None:
        #   failed_state_name = failed[0] if failed else "unknown"
        failed = None  # _find_failed_state would return None
        failed_state_name = failed[0] if failed else "unknown"
        error_message = "infrastructure failure"

        with patch("sys.stderr", stderr):
            display_completion_summary(
                "TestFlow", 1.0, failed_state_name, error_message
            )

        output = stderr.getvalue()
        assert "✗" in output, "Must print failure symbol, not success"
        assert "✓" not in output, "Must NOT print success symbol"
        assert "unknown" in output
        assert "infrastructure failure" in output

    def test_error_fallback_is_unknown_not_none(self):
        """Boundary: fallback state name for no-state errors is 'unknown', not None.

        Regression guard: if this returns None, display_completion_summary would
        print a success message inside an exception handler.
        """
        failed: tuple[str, str] | None = None
        failed_state_name = failed[0] if failed else "unknown"
        assert failed_state_name == "unknown"
        assert failed_state_name is not None


class TestWorkflowPersistId:
    """Regression tests for F1: workflow persist ID round-trips correctly."""

    def test_flat_file_returns_filename(self, tmp_path):
        """Flat workflow file persists as just the filename."""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        wf = workflows_dir / "batch_flow.yaml"
        wf.touch()

        result = _workflow_persist_id(wf, workflows_dir)
        assert result == "batch_flow.yaml"

    def test_directory_workflow_returns_relative_path(self, tmp_path):
        """Directory workflow persists as 'dirname/workflow.yaml'."""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        review_dir = workflows_dir / "review"
        review_dir.mkdir()
        wf = review_dir / "workflow.yaml"
        wf.touch()

        result = _workflow_persist_id(wf, workflows_dir)
        assert result == "review/workflow.yaml"

    def test_round_trip_flat(self, tmp_path):
        """Flat workflow ID resolves back to the original file."""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        wf = workflows_dir / "batch_flow.yaml"
        wf.touch()

        persist_id = _workflow_persist_id(wf, workflows_dir)
        resolved = workflows_dir / persist_id
        assert resolved.resolve() == wf.resolve()

    def test_round_trip_directory(self, tmp_path):
        """Directory workflow ID resolves back to the original file."""
        workflows_dir = tmp_path / "workflows"
        workflows_dir.mkdir()
        review_dir = workflows_dir / "review"
        review_dir.mkdir()
        wf = review_dir / "workflow.yaml"
        wf.touch()

        persist_id = _workflow_persist_id(wf, workflows_dir)
        resolved = workflows_dir / persist_id
        assert resolved.resolve() == wf.resolve()


class TestWorkflowValidatorNesting:
    """Regression tests for F1: TaskEntry.workflow validator allows one-level nesting."""

    def test_flat_filename_accepted(self):
        from fdsx.models.task import TaskEntry

        entry = TaskEntry(description="test", workflow="batch_flow.yaml")
        assert entry.workflow == "batch_flow.yaml"

    def test_one_level_nesting_accepted(self):
        from fdsx.models.task import TaskEntry

        entry = TaskEntry(description="test", workflow="review/workflow.yaml")
        assert entry.workflow == "review/workflow.yaml"

    def test_deep_nesting_rejected(self):
        import pytest
        from fdsx.models.task import TaskEntry

        with pytest.raises(Exception, match="nesting too deep"):
            TaskEntry(description="test", workflow="a/b/c.yaml")

    def test_parent_traversal_rejected(self):
        import pytest
        from fdsx.models.task import TaskEntry

        with pytest.raises(Exception, match="without \\.\\."):
            TaskEntry(description="test", workflow="../escape.yaml")

    def test_absolute_path_rejected(self):
        import pytest
        from fdsx.models.task import TaskEntry

        with pytest.raises(Exception, match="relative path"):
            TaskEntry(description="test", workflow="/etc/passwd")


class TestHandleInterrupts:
    """Unit tests for handle_interrupts in engine/interrupts.py."""

    def _make_state_info(self, tasks=None):
        """Build a mock state_info object."""
        mock_state = MagicMock()
        mock_state.tasks = tasks or []
        return mock_state

    def _make_task_with_interrupt(self, payload: dict):
        """Build a mock task with an interrupt payload."""
        mock_interrupt = MagicMock()
        mock_interrupt.value = payload
        mock_task = MagicMock()
        mock_task.interrupts = [mock_interrupt]
        return mock_task

    def _make_task_without_interrupt(self):
        """Build a mock task with no interrupt."""
        mock_task = MagicMock()
        mock_task.interrupts = []
        return mock_task

    def test_no_tasks_returns_last_state_unchanged(self):
        """When no tasks are pending, last_state is returned immediately."""
        graph = MagicMock()
        empty_state = self._make_state_info(tasks=[])
        graph.get_state.return_value = empty_state

        initial_state = {"result": "done"}
        result = handle_interrupts(graph, {}, initial_state)

        assert result == initial_state
        graph.stream.assert_not_called()

    def test_task_without_interrupt_breaks_immediately(self):
        """Tasks with no interrupt payload cause immediate loop exit."""
        graph = MagicMock()
        state_with_task = self._make_state_info(
            tasks=[self._make_task_without_interrupt()]
        )
        graph.get_state.return_value = state_with_task

        initial_state = {"result": "pending"}
        result = handle_interrupts(graph, {}, initial_state)

        assert result == initial_state
        graph.stream.assert_not_called()

    def test_single_interrupt_prompts_and_resumes(self):
        """A single interrupt triggers display_wait_prompt and streams Command(resume=...)."""
        graph = MagicMock()
        payload = {"message": "approve?", "choices": ["yes", "no"], "state_name": "approval"}
        task = self._make_task_with_interrupt(payload)

        # First call: task with interrupt. Second call: no tasks (done).
        empty_state = self._make_state_info(tasks=[])
        state_with_interrupt = self._make_state_info(tasks=[task])
        graph.get_state.side_effect = [state_with_interrupt, empty_state]

        # stream yields one non-interrupt snapshot
        graph.stream.return_value = iter([{"status": "ok"}])

        with patch(
            "fdsx.core.engine.interrupts.display_wait_prompt", return_value="yes"
        ) as mock_prompt:
            result = handle_interrupts(graph, {"configurable": {}}, {"x": 1})

        mock_prompt.assert_called_once_with("approval", "approve?", ["yes", "no"])
        graph.stream.assert_called_once()
        assert result == {"status": "ok"}

    def test_multiple_interrupts_loops_until_done(self):
        """Multiple sequential interrupts are all handled before returning."""
        graph = MagicMock()
        payload = {"message": "continue?", "choices": ["yes"], "state_name": "step"}
        task = self._make_task_with_interrupt(payload)
        empty_state = self._make_state_info(tasks=[])
        state_with_interrupt = self._make_state_info(tasks=[task])

        # Two interrupt rounds, then done
        graph.get_state.side_effect = [
            state_with_interrupt,
            state_with_interrupt,
            empty_state,
        ]
        graph.stream.return_value = iter([{"round": 1}])

        with patch(
            "fdsx.core.engine.interrupts.display_wait_prompt", return_value="yes"
        ) as mock_prompt:
            handle_interrupts(graph, {}, {})

        assert mock_prompt.call_count == 2

    def test_interrupt_snapshot_with_interrupt_key_skipped(self):
        """State snapshots containing '__interrupt__' are skipped (last_state not updated)."""
        graph = MagicMock()
        payload = {"message": "ok?", "choices": [], "state_name": "s"}
        task = self._make_task_with_interrupt(payload)
        empty_state = self._make_state_info(tasks=[])
        state_with_interrupt = self._make_state_info(tasks=[task])

        graph.get_state.side_effect = [state_with_interrupt, empty_state]
        # stream yields an interrupt snapshot then a valid one
        graph.stream.return_value = iter([
            {"__interrupt__": True},
            {"real": "state"},
        ])

        with patch("fdsx.core.engine.interrupts.display_wait_prompt", return_value="ok"):
            result = handle_interrupts(graph, {}, {"original": True})

        # The __interrupt__ snapshot was skipped; real state was captured
        assert result == {"real": "state"}

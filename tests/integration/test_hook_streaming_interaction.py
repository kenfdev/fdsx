"""Integration tests for T026: Hook + Streaming Interaction.

Verifies that hooks fire correctly around states that produce streaming output.

Key interactions to test:
- on_start hook fires before the task node (before streaming begins)
- on_complete hook fires after the task node (after streaming ends)
- StreamLogger log files are created correctly alongside hook execution
- Hook data files (input.json/output.json) are created correctly
- Order of operations: on_start → streaming → on_complete
- Both streaming output AND hook files coexist correctly
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fdsx.core.compiler import compile_flow, _wrap_with_hooks
from fdsx.core.hooks import INPUT_FILENAME, OUTPUT_FILENAME, HOOKS_DIR_NAME
from fdsx.logging.recorder import RUNS_DIR_NAME, LOGS_DIR_NAME
from fdsx.logging.stream_logger import LOG_FILE_SUFFIX, StreamLogger
from fdsx.models.flow import HookEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recorder(
    thread_id: str = "test-thread", flow_name: str = "TestFlow"
) -> MagicMock:
    recorder = MagicMock()
    recorder.thread_id = thread_id
    recorder.flow_name = flow_name
    return recorder


def _make_hook(command: str, on_failure: str = "warn") -> HookEntry:
    return HookEntry(command=command, on_failure=on_failure)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# T026: Hook + Streaming interaction tests
# ---------------------------------------------------------------------------


class TestHookFiringOrderWithStreaming:
    """Verify hooks fire in correct order relative to streaming output."""

    def test_on_start_fires_before_node_output(self, tmp_path: Path) -> None:
        """on_start hook fires before the task node produces any streaming output.

        Execution order must be: on_start hook → streaming → on_complete hook.
        """
        execution_order: list[str] = []

        def streaming_node(state_dict: dict) -> dict:
            """Simulates a node that produces streaming output."""
            execution_order.append("node_started")
            # Simulate streaming output that would be generated inside node
            execution_order.append("node_streaming")
            execution_order.append("node_completed")
            return {**state_dict, "result": "streamed output"}

        on_start = [_make_hook("echo on_start")]
        on_complete = [_make_hook("echo on_complete")]
        recorder = _make_recorder(thread_id="hook-stream-tid")

        wrapped = _wrap_with_hooks(
            streaming_node,
            "StreamingState",
            on_start,
            on_complete,
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "data.json"
            with patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec:
                mock_exec.side_effect = lambda hooks, **kw: execution_order.append(
                    f"hook_{kw['status']}"
                )
                wrapped({"input": "value"})

        # on_start fires before node executes
        assert execution_order.index("hook_starting") < execution_order.index(
            "node_started"
        ), "on_start hook must fire before node execution starts"
        # node executes before on_complete fires
        assert execution_order.index("node_completed") < execution_order.index(
            "hook_completed"
        ), "on_complete hook must fire after node execution completes"

    def test_streaming_log_file_and_hook_data_files_coexist(
        self, tmp_path: Path
    ) -> None:
        """StreamLogger log files and hook data JSON files exist in the correct directories.

        - Log files: .fdsx/runs/<thread-id>/logs/<state-name>_<iteration>.log
        - Hook files: .fdsx/runs/<thread-id>/hooks/<state-name>/input.json, output.json
        """
        thread_id = "coexist-tid"
        runs_dir = tmp_path / RUNS_DIR_NAME
        log_dir = runs_dir / thread_id / LOGS_DIR_NAME
        fdsx_base_dir = tmp_path

        # Create a real StreamLogger (not mocked) to verify log file creation
        stream_logger = StreamLogger("CoexistState", log_dir)

        # Simulate what the task node does: stream output then close
        stream_logger.on_stdout("streaming line one")
        stream_logger.on_stderr("streaming error line")
        stream_logger.close()

        # Verify log file was created
        log_file = log_dir / f"CoexistState_1{LOG_FILE_SUFFIX}"
        assert log_file.exists(), f"Log file not created at {log_file}"
        content = log_file.read_text()
        assert "streaming line one" in content
        assert "streaming error line" in content

        # Now also write hook data files (simulating what _wrap_with_hooks does)
        from fdsx.core.hooks import write_hook_data

        input_path = write_hook_data(
            {"input": "data"},
            state_name="CoexistState",
            filename=INPUT_FILENAME,
            thread_id=thread_id,
            base_dir=fdsx_base_dir,
        )
        output_path = write_hook_data(
            {"result": "streamed output"},
            state_name="CoexistState",
            filename=OUTPUT_FILENAME,
            thread_id=thread_id,
            base_dir=fdsx_base_dir,
        )

        # Both log file and hook data files should coexist
        assert log_file.exists()
        assert input_path.exists()
        assert output_path.exists()

        # Verify hook data files are named correctly
        assert input_path.name == INPUT_FILENAME
        assert output_path.name == OUTPUT_FILENAME

        # Verify they are in the hooks subdirectory for the state
        assert input_path.parent.name == "CoexistState"
        assert input_path.parent.parent.name == HOOKS_DIR_NAME
        assert input_path.parent.parent.parent.name == thread_id

    def test_hook_fires_correctly_with_streaming_task_state(
        self, tmp_path: Path
    ) -> None:
        """Full integration: hooks fire around a system provider task that produces output.

        Uses compile_flow with a real system provider task so that:
        - StreamLogger is instantiated inside the task node
        - Hooks are wired by _wrap_with_hooks
        - on_start fires before the system command runs
        - on_complete fires after the system command completes
        """
        flow_yaml = """
name: Hook Streaming Integration
description: Test hooks + streaming interaction
start_at: produce_output
states:
  produce_output:
    type: task
    provider: system
    command: "echo streaming output line"
    result_path: $.result
    end: true
    hooks:
      on_start:
        - command: "echo hook_start"
      on_complete:
        - command: "echo hook_complete"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        from fdsx.core.loader import load_flow

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        thread_id = "hook-stream-full-tid"
        recorder = _make_recorder(
            thread_id=thread_id, flow_name="Hook Streaming Integration"
        )
        log_dir = tmp_path / RUNS_DIR_NAME / thread_id / LOGS_DIR_NAME

        hook_calls: list[dict] = []

        def fake_execute_hooks(
            hooks, *, state_name, status, data_path, thread_id, flow_name
        ):
            hook_calls.append({"state_name": state_name, "status": status})

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with patch("fdsx.core.compiler.compile.execute_hooks", side_effect=fake_execute_hooks):
            with patch(
                "fdsx.core.compiler.compile.write_hook_data", side_effect=fake_write_hook_data
            ):
                compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
                config_dict = {"configurable": {"thread_id": thread_id}}
                list(
                    compiled.graph.stream(
                        {"_meta": {}}, config=config_dict, stream_mode="values"
                    )
                )

        # Both hooks should have fired
        starting_calls = [c for c in hook_calls if c["status"] == "starting"]
        completed_calls = [c for c in hook_calls if c["status"] == "completed"]

        assert len(starting_calls) >= 1, "on_start hook should have fired"
        assert len(completed_calls) >= 1, "on_complete hook should have fired"
        assert starting_calls[0]["state_name"] == "produce_output"
        assert completed_calls[0]["state_name"] == "produce_output"

    def test_streaming_output_appears_on_stderr_with_hooks_configured(
        self, tmp_path: Path, capsys
    ) -> None:
        """When hooks are configured, streaming output still appears on stderr with [prefix].

        This verifies the hook wrapper does not interfere with streaming output.
        """
        flow_yaml = """
name: Hook + Streaming Terminal
description: Verify terminal output not affected by hooks
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "echo hello_from_task"
    result_path: $.result
    end: true
    hooks:
      on_start:
        - command: "echo hook_start"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        from fdsx.core.loader import load_flow

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        thread_id = "stream-terminal-tid"
        recorder = _make_recorder(
            thread_id=thread_id, flow_name="Hook + Streaming Terminal"
        )
        log_dir = tmp_path / RUNS_DIR_NAME / thread_id / LOGS_DIR_NAME

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with patch("fdsx.core.compiler.compile.execute_hooks"):
            with patch(
                "fdsx.core.compiler.compile.write_hook_data", side_effect=fake_write_hook_data
            ):
                compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
                config_dict = {"configurable": {"thread_id": thread_id}}
                list(
                    compiled.graph.stream(
                        {"_meta": {}}, config=config_dict, stream_mode="values"
                    )
                )

        captured = capsys.readouterr()
        # Streaming output should appear on stderr with [state_name] prefix
        assert "[step1] hello_from_task" in captured.err, (
            f"Expected '[step1] hello_from_task' in stderr, got: {captured.err!r}"
        )

    def test_log_file_created_when_hooks_also_configured(self, tmp_path: Path) -> None:
        """Per-state log file is created when hooks are also configured.

        This verifies hook execution does not interfere with StreamLogger log file creation.
        """
        flow_yaml = """
name: Log + Hook Coexistence
description: Log file and hooks together
start_at: logstep
states:
  logstep:
    type: task
    provider: system
    command: "echo logged_output"
    result_path: $.result
    end: true
    hooks:
      on_start:
        - command: "echo hook_start"
      on_complete:
        - command: "echo hook_done"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        from fdsx.core.loader import load_flow

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        thread_id = "log-hook-tid"
        recorder = _make_recorder(
            thread_id=thread_id, flow_name="Log + Hook Coexistence"
        )
        log_dir = tmp_path / RUNS_DIR_NAME / thread_id / LOGS_DIR_NAME

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with patch("fdsx.core.compiler.compile.execute_hooks"):
            with patch(
                "fdsx.core.compiler.compile.write_hook_data", side_effect=fake_write_hook_data
            ):
                compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
                config_dict = {"configurable": {"thread_id": thread_id}}
                list(
                    compiled.graph.stream(
                        {"_meta": {}}, config=config_dict, stream_mode="values"
                    )
                )

        # Per-state log file should exist
        log_file = log_dir / f"logstep_1{LOG_FILE_SUFFIX}"
        assert log_file.exists(), f"Log file should exist at {log_file}"

        content = log_file.read_text()
        assert "logged_output" in content


class TestHookInputOutputDataWithStreaming:
    """Verify hook data files contain correct data when state produces streaming output."""

    def test_input_json_contains_state_before_streaming(self, tmp_path: Path) -> None:
        """input.json is written with the pre-execution state (before streaming).

        This verifies write_hook_data for input.json captures state BEFORE the
        task node runs (and before streaming begins).
        """
        initial_state = {"setup_value": "initial", "_meta": {}}
        captured_input_data: list[dict] = []

        def node_fn(state_dict: dict) -> dict:
            return {**state_dict, "result": "done"}

        hook = _make_hook("echo start")
        recorder = _make_recorder(thread_id="input-data-tid")

        wrapped = _wrap_with_hooks(
            node_fn,
            "DataState",
            [hook],
            [],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        def fake_write(data, *, state_name, filename, thread_id, base_dir):
            if filename == INPUT_FILENAME:
                captured_input_data.append(dict(data))
            return tmp_path / filename

        with patch("fdsx.core.compiler.compile.write_hook_data", side_effect=fake_write):
            with patch("fdsx.core.compiler.compile.execute_hooks"):
                wrapped(initial_state)

        assert len(captured_input_data) == 1
        assert captured_input_data[0] == initial_state

    def test_output_json_contains_state_after_streaming(self, tmp_path: Path) -> None:
        """output.json is written with the post-execution state (after streaming).

        This verifies write_hook_data for output.json captures the result produced
        by the task node (the state AFTER streaming is complete).
        """
        captured_output_data: list[dict] = []

        def node_fn(state_dict: dict) -> dict:
            # Simulate a node that processes streaming output and returns result
            return {**state_dict, "result": "streamed_and_processed"}

        hook = _make_hook("echo complete")
        recorder = _make_recorder(thread_id="output-data-tid")

        wrapped = _wrap_with_hooks(
            node_fn,
            "OutputState",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        def fake_write(data, *, state_name, filename, thread_id, base_dir):
            if filename == OUTPUT_FILENAME:
                captured_output_data.append(dict(data))
            return tmp_path / filename

        with patch("fdsx.core.compiler.compile.write_hook_data", side_effect=fake_write):
            with patch("fdsx.core.compiler.compile.execute_hooks"):
                wrapped({"input": "value"})

        assert len(captured_output_data) == 1
        assert captured_output_data[0]["result"] == "streamed_and_processed"


class TestHookAbortWithStreaming:
    """Verify abort policy behaves correctly when streaming is also configured."""

    def test_abort_on_start_prevents_streaming(self, tmp_path: Path) -> None:
        """When on_start hook aborts, the task node (including streaming) does not execute."""
        from fdsx.core.hooks import HookAbortError

        node_executed = []

        def streaming_node(state_dict: dict) -> dict:
            node_executed.append(True)
            # This would be where streaming happens
            return {**state_dict, "result": "should not reach"}

        hook = _make_hook("abort-script", on_failure="abort")
        recorder = _make_recorder()

        wrapped = _wrap_with_hooks(
            streaming_node,
            "AbortStreamState",
            [hook],
            [],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "in.json"
            with patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec:
                mock_exec.side_effect = HookAbortError("abort-script", 1)
                with pytest.raises(HookAbortError):
                    wrapped({"input": "x"})

        # Node (and its streaming) should not have executed
        assert len(node_executed) == 0, (
            "Node (and streaming) should not execute when on_start aborts"
        )

    def test_on_complete_fires_with_failed_status_when_task_node_raises(
        self, tmp_path: Path
    ) -> None:
        """on_complete fires with status='failed' when the task node raises.

        This covers the case where a streaming task fails mid-execution.
        """

        def failing_node(state_dict: dict) -> dict:
            raise RuntimeError("provider failed")

        hook = _make_hook("echo on_complete")
        recorder = _make_recorder()

        wrapped = _wrap_with_hooks(
            failing_node,
            "FailingStreamState",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "out.json"
            with patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec:
                with pytest.raises(RuntimeError, match="provider failed"):
                    wrapped({"x": 1})

        assert mock_exec.call_count == 1
        exec_kwargs = mock_exec.call_args[1]
        assert exec_kwargs["status"] == "failed", (
            "on_complete should fire with 'failed' status when task node raises"
        )


class TestMultiStateHookStreamingInteraction:
    """Verify hook+streaming works correctly in a multi-state flow."""

    def test_hooks_fire_for_each_state_independently(self, tmp_path: Path) -> None:
        """Each state fires hooks independently; streaming is separate per state."""
        flow_yaml = """
name: Multi State Hook Stream
description: Multi-state hook and streaming
start_at: step1
hooks:
  on_start:
    - command: "echo flow_start"
  on_complete:
    - command: "echo flow_complete"
states:
  step1:
    type: task
    provider: system
    command: "echo step1_output"
    result_path: $.step1_result
    next: step2
  step2:
    type: task
    provider: system
    command: "echo step2_output"
    result_path: $.step2_result
    end: true
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        from fdsx.core.loader import load_flow

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        thread_id = "multi-state-hook-tid"
        recorder = _make_recorder(
            thread_id=thread_id, flow_name="Multi State Hook Stream"
        )
        log_dir = tmp_path / RUNS_DIR_NAME / thread_id / LOGS_DIR_NAME

        hook_calls: list[dict] = []

        def fake_execute_hooks(
            hooks, *, state_name, status, data_path, thread_id, flow_name
        ):
            hook_calls.append({"state_name": state_name, "status": status})

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with patch("fdsx.core.compiler.compile.execute_hooks", side_effect=fake_execute_hooks):
            with patch(
                "fdsx.core.compiler.compile.write_hook_data", side_effect=fake_write_hook_data
            ):
                compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
                config_dict = {"configurable": {"thread_id": thread_id}}
                list(
                    compiled.graph.stream(
                        {"_meta": {}}, config=config_dict, stream_mode="values"
                    )
                )

        # Both states should have hooks fired
        state_names_that_fired = {c["state_name"] for c in hook_calls}
        assert "step1" in state_names_that_fired, "step1 hooks should have fired"
        assert "step2" in state_names_that_fired, "step2 hooks should have fired"

        # Both starting and completed for each state
        step1_starting = [
            c
            for c in hook_calls
            if c["state_name"] == "step1" and c["status"] == "starting"
        ]
        step1_completed = [
            c
            for c in hook_calls
            if c["state_name"] == "step1" and c["status"] == "completed"
        ]
        step2_starting = [
            c
            for c in hook_calls
            if c["state_name"] == "step2" and c["status"] == "starting"
        ]
        step2_completed = [
            c
            for c in hook_calls
            if c["state_name"] == "step2" and c["status"] == "completed"
        ]

        assert len(step1_starting) == 1, "step1 on_start should fire once"
        assert len(step1_completed) == 1, "step1 on_complete should fire once"
        assert len(step2_starting) == 1, "step2 on_start should fire once"
        assert len(step2_completed) == 1, "step2 on_complete should fire once"

    def test_log_files_created_per_state_with_hooks(self, tmp_path: Path) -> None:
        """Each state creates its own log file even when hooks are configured at flow level."""
        flow_yaml = """
name: Per State Logs With Hooks
description: Log files per state with flow hooks
start_at: stateA
hooks:
  on_start:
    - command: "echo flow_hook"
states:
  stateA:
    type: task
    provider: system
    command: "echo output_from_A"
    result_path: $.a_result
    next: stateB
  stateB:
    type: task
    provider: system
    command: "echo output_from_B"
    result_path: $.b_result
    end: true
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        from fdsx.core.loader import load_flow

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        thread_id = "per-state-log-tid"
        recorder = _make_recorder(
            thread_id=thread_id, flow_name="Per State Logs With Hooks"
        )
        log_dir = tmp_path / RUNS_DIR_NAME / thread_id / LOGS_DIR_NAME

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with patch("fdsx.core.compiler.compile.execute_hooks"):
            with patch(
                "fdsx.core.compiler.compile.write_hook_data", side_effect=fake_write_hook_data
            ):
                compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
                config_dict = {"configurable": {"thread_id": thread_id}}
                list(
                    compiled.graph.stream(
                        {"_meta": {}}, config=config_dict, stream_mode="values"
                    )
                )

        # Both state log files should exist
        log_A = log_dir / f"stateA_1{LOG_FILE_SUFFIX}"
        log_B = log_dir / f"stateB_1{LOG_FILE_SUFFIX}"
        assert log_A.exists(), f"Log file for stateA should exist at {log_A}"
        assert log_B.exists(), f"Log file for stateB should exist at {log_B}"

        # Content should be from respective states
        assert "output_from_A" in log_A.read_text()
        assert "output_from_B" in log_B.read_text()

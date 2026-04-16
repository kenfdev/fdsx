"""Integration tests for Phase 8: Hooks System Wiring (T022-T023).

Tests:
- _wrap_with_hooks() wraps a node with on_start/on_complete hooks
- Hooks fire correctly for TaskState, ChoiceState, PassState, ParallelState, WaitState
- Hook levels merge in correct order: global → flow → state
- Abort-policy hook failure halts the node execution
- Warn-policy hook failure continues execution
- ParallelState hooks wrap dispatch/collector, not branch executor
- WaitState on_start fires in notify node, on_complete fires in interrupt node
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from fdsx.core.compiler import _wrap_with_hooks, compile_flow
from fdsx.core.config import FdsxConfig
from fdsx.core.engine import run_flow
from fdsx.core.hooks import INPUT_FILENAME, OUTPUT_FILENAME
from fdsx.core.loader import load_flow
from fdsx.models.flow import HookConfig, HookEntry

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


def _simple_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """A simple node that adds a marker to the state."""
    return {**state_dict, "executed": True}


# ---------------------------------------------------------------------------
# T022: _wrap_with_hooks() unit-level integration tests
# ---------------------------------------------------------------------------


class TestWrapWithHooksNoOp:
    """When both hook lists are empty, _wrap_with_hooks returns the original function."""

    def test_returns_original_fn_when_no_hooks(self) -> None:
        wrapped = _wrap_with_hooks(
            _simple_node,
            "MyState",
            [],
            [],
            recorder=None,
            fdsx_base_dir=None,
        )
        assert wrapped is _simple_node

    def test_no_subprocess_calls_when_no_hooks(self, tmp_path: Path) -> None:
        wrapped = _wrap_with_hooks(
            _simple_node,
            "MyState",
            [],
            [],
            recorder=_make_recorder(),
            fdsx_base_dir=tmp_path,
        )
        with patch("subprocess.run") as mock_run:
            result = wrapped({"key": "value"})
        mock_run.assert_not_called()
        assert result["executed"] is True


class TestWrapWithHooksOnStart:
    """on_start hooks fire before node execution with status='starting'."""

    def test_on_start_hook_fires_before_node(self, tmp_path: Path) -> None:
        execution_order: list[str] = []

        def tracking_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            execution_order.append("node")
            return {**state_dict, "executed": True}

        hook = _make_hook("echo start")
        on_start = [hook]

        recorder = _make_recorder(thread_id="tid-001")
        wrapped = _wrap_with_hooks(
            tracking_node,
            "State1",
            on_start,
            [],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with (
            patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec,
            patch("fdsx.core.compiler.compile.write_hook_data") as mock_write,
        ):
            mock_write.return_value = tmp_path / "input.json"
            result = wrapped({"x": 1})

        assert result["executed"] is True
        # execute_hooks called once (on_start)
        assert mock_exec.call_count == 1
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["status"] == "starting"
        assert call_kwargs["state_name"] == "State1"
        assert call_kwargs["thread_id"] == "tid-001"
        assert call_kwargs["flow_name"] == "TestFlow"

    def test_input_json_written_before_on_start(self, tmp_path: Path) -> None:
        hook = _make_hook("echo start")
        recorder = _make_recorder(thread_id="tid-input")
        wrapped = _wrap_with_hooks(
            _simple_node,
            "StateX",
            [hook],
            [],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        call_order: list[str] = []

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.side_effect = lambda *a, **kw: (
                call_order.append(f"write:{kw.get('filename', '')}"),
                tmp_path / kw.get("filename", "out.json"),
            )[-1]
            with patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec:
                mock_exec.side_effect = lambda *a, **kw: call_order.append("exec_hook")
                wrapped({"y": 2})

        assert call_order[0] == f"write:{INPUT_FILENAME}"
        assert call_order[1] == "exec_hook"


class TestWrapWithHooksOnComplete:
    """on_complete hooks fire after node execution with status='completed'."""

    def test_on_complete_hook_fires_after_node(self, tmp_path: Path) -> None:
        hook = _make_hook("echo complete")
        recorder = _make_recorder(thread_id="tid-002")
        wrapped = _wrap_with_hooks(
            _simple_node,
            "State2",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with (
            patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec,
            patch("fdsx.core.compiler.compile.write_hook_data") as mock_write,
        ):
            mock_write.return_value = tmp_path / "out.json"
            result = wrapped({"a": 1})

        assert result["executed"] is True
        assert mock_exec.call_count == 1
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["status"] == "completed"
        assert call_kwargs["state_name"] == "State2"

    def test_output_json_written_before_on_complete(self, tmp_path: Path) -> None:
        hook = _make_hook("echo done")
        recorder = _make_recorder(thread_id="tid-output")
        wrapped = _wrap_with_hooks(
            _simple_node,
            "StateY",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        call_order: list[str] = []

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.side_effect = lambda *a, **kw: (
                call_order.append(f"write:{kw.get('filename', '')}"),
                tmp_path / kw.get("filename", "out.json"),
            )[-1]
            with patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec:
                mock_exec.side_effect = lambda *a, **kw: call_order.append("exec_hook")
                wrapped({"z": 3})

        assert call_order[0] == f"write:{INPUT_FILENAME}"
        assert call_order[1] == f"write:{OUTPUT_FILENAME}"
        assert call_order[2] == "exec_hook"


class TestWrapWithHooksBothEvents:
    """When both hook lists are non-empty, both fire in correct order."""

    def test_start_fires_before_node_complete_fires_after(self, tmp_path: Path) -> None:
        execution_order: list[str] = []

        def tracking_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            execution_order.append("node")
            return {**state_dict, "done": True}

        on_start = [_make_hook("start-hook")]
        on_complete = [_make_hook("complete-hook")]
        recorder = _make_recorder()

        wrapped = _wrap_with_hooks(
            tracking_node,
            "MyState",
            on_start,
            on_complete,
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with (
            patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec,
            patch("fdsx.core.compiler.compile.write_hook_data") as mock_write,
        ):
            mock_write.return_value = tmp_path / "x.json"
            wrapped({"input": "value"})

        # execute_hooks called twice: once for on_start, once for on_complete
        assert mock_exec.call_count == 2
        first_call = mock_exec.call_args_list[0]
        second_call = mock_exec.call_args_list[1]
        assert first_call[1]["status"] == "starting"
        assert second_call[1]["status"] == "completed"


class TestWrapWithHooksDataFiles:
    """Hook data files are written with correct state_name, thread_id, and filenames."""

    def test_input_json_uses_correct_params(self, tmp_path: Path) -> None:
        hook = _make_hook("echo x")
        recorder = _make_recorder(thread_id="my-thread", flow_name="MyFlow")
        wrapped = _wrap_with_hooks(
            _simple_node,
            "DataState",
            [hook],
            [],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "in.json"
            with patch("fdsx.core.compiler.compile.execute_hooks"):
                wrapped({"val": 42})

        # First call = input.json
        first_call = mock_write.call_args_list[0]
        assert first_call[1]["state_name"] == "DataState"
        assert first_call[1]["filename"] == INPUT_FILENAME
        assert first_call[1]["thread_id"] == "my-thread"
        assert first_call[1]["base_dir"] == tmp_path

    def test_output_json_uses_node_result(self, tmp_path: Path) -> None:
        hook = _make_hook("echo y")
        recorder = _make_recorder(thread_id="my-thread")

        def my_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            return {"result": "node_output"}

        wrapped = _wrap_with_hooks(
            my_node,
            "ResultState",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "out.json"
            with patch("fdsx.core.compiler.compile.execute_hooks"):
                wrapped({"in": "data"})

        # Second call = output.json, data = full post-execution state (input merged with partial result)
        second_call = mock_write.call_args_list[1]
        assert second_call[0][0] == {
            "in": "data",
            "result": "node_output",
        }  # positional arg
        assert second_call[1]["filename"] == OUTPUT_FILENAME


class TestWrapWithHooksAbortBehavior:
    """Abort-policy hook failure raises HookAbortError which halts the node."""

    def test_abort_on_start_prevents_node_execution(self, tmp_path: Path) -> None:
        from fdsx.core.hooks import HookAbortError

        executed = []

        def tracking_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            executed.append(True)
            return state_dict

        hook = _make_hook("fail-script", on_failure="abort")
        recorder = _make_recorder()
        wrapped = _wrap_with_hooks(
            tracking_node,
            "AbortState",
            [hook],
            [],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "in.json"
            with patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec:
                mock_exec.side_effect = HookAbortError("fail-script", 1)
                with pytest.raises(HookAbortError):
                    wrapped({"x": 1})

        assert len(executed) == 0, "Node should not execute after abort"

    def test_abort_on_complete_raises_after_node(self, tmp_path: Path) -> None:
        from fdsx.core.hooks import HookAbortError

        executed = []

        def tracking_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            executed.append(True)
            return {**state_dict, "done": True}

        hook = _make_hook("fail-after", on_failure="abort")
        recorder = _make_recorder()
        wrapped = _wrap_with_hooks(
            tracking_node,
            "AbortAfterState",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "out.json"
            with patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec:
                mock_exec.side_effect = HookAbortError("fail-after", 2)
                with pytest.raises(HookAbortError):
                    wrapped({"x": 1})

        assert len(executed) == 1, "Node should have executed before abort"


class TestWrapWithHooksNodeFailure:
    """on_complete hooks fire with status='failed' when the node raises, then the error re-raises."""

    def test_on_complete_fires_with_failed_status_when_node_raises(
        self, tmp_path: Path
    ) -> None:
        """on_complete hooks fire with status='failed' when node raises RuntimeError."""

        def failing_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("node exploded")

        hook = _make_hook("echo on_complete")
        recorder = _make_recorder()
        wrapped = _wrap_with_hooks(
            failing_node,
            "FailState",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "out.json"
            with (
                patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec,
                pytest.raises(RuntimeError, match="node exploded"),
            ):
                wrapped({"x": 1})

        assert mock_exec.call_count == 1
        exec_kwargs = mock_exec.call_args[1]
        assert exec_kwargs["status"] == "failed"

    def test_node_error_is_reraised_after_on_complete_hooks(
        self, tmp_path: Path
    ) -> None:
        """The original exception is re-raised after on_complete hooks run."""
        original_error = ValueError("original error")

        def failing_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            raise original_error

        hook = _make_hook("echo on_complete")
        recorder = _make_recorder()
        wrapped = _wrap_with_hooks(
            failing_node,
            "ReraiseState",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "out.json"
            with (
                patch("fdsx.core.compiler.compile.execute_hooks"),
                pytest.raises(ValueError) as exc_info,
            ):
                wrapped({})

        assert exc_info.value is original_error

    def test_output_json_written_with_input_state_on_failure(
        self, tmp_path: Path
    ) -> None:
        """When node fails, output.json is written with the input state_dict (fallback)."""

        def failing_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("boom")

        hook = _make_hook("echo h")
        recorder = _make_recorder()
        wrapped = _wrap_with_hooks(
            failing_node,
            "FallbackState",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        write_data_calls: list[dict] = []

        def fake_write(data, *, state_name, filename, thread_id, base_dir):
            write_data_calls.append({"data": data, "filename": filename})
            return tmp_path / filename

        with (
            patch("fdsx.core.compiler.compile.write_hook_data", side_effect=fake_write),
            patch("fdsx.core.compiler.compile.execute_hooks"),
            pytest.raises(RuntimeError),
        ):
            wrapped({"original": "state"})

        output_write = next(
            c for c in write_data_calls if c["filename"] == OUTPUT_FILENAME
        )
        assert output_write["data"] == {"original": "state"}, (
            "output.json should contain the input state_dict as fallback on failure"
        )

    def test_both_on_start_and_on_complete_fire_with_correct_statuses_on_failure(
        self, tmp_path: Path
    ) -> None:
        """on_start fires with 'starting', on_complete fires with 'failed' when node raises."""

        def failing_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("fail")

        on_start = [_make_hook("echo start")]
        on_complete = [_make_hook("echo complete")]
        recorder = _make_recorder()
        wrapped = _wrap_with_hooks(
            failing_node,
            "BothHooksFailState",
            on_start,
            on_complete,
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "x.json"
            with (
                patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec,
                pytest.raises(RuntimeError),
            ):
                wrapped({"k": "v"})

        assert mock_exec.call_count == 2
        start_call = mock_exec.call_args_list[0]
        complete_call = mock_exec.call_args_list[1]
        assert start_call[1]["status"] == "starting"
        assert complete_call[1]["status"] == "failed"


class TestWrapWithHooksRecorderFallback:
    """When recorder is None, thread_id and flow_name fall back to empty strings."""

    def test_no_recorder_uses_empty_strings(self, tmp_path: Path) -> None:
        hook = _make_hook("echo hook")
        wrapped = _wrap_with_hooks(
            _simple_node,
            "NullRecorderState",
            [hook],
            [],
            recorder=None,
            fdsx_base_dir=tmp_path,
        )

        with patch("fdsx.core.compiler.compile.write_hook_data") as mock_write:
            mock_write.return_value = tmp_path / "in.json"
            with patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec:
                wrapped({"k": "v"})

        write_kwargs = mock_write.call_args_list[0][1]
        assert write_kwargs["thread_id"] == ""

        exec_kwargs = mock_exec.call_args[1]
        assert exec_kwargs["thread_id"] == ""
        assert exec_kwargs["flow_name"] == ""


# ---------------------------------------------------------------------------
# T023: compile_flow integration tests — hooks wired for all node types
# ---------------------------------------------------------------------------


class TestCompileFlowHooksWiring:
    """Verify compile_flow applies _wrap_with_hooks for all node types."""

    def test_task_state_hooks_fire_via_compile_flow(self, tmp_path: Path) -> None:
        """on_start and on_complete hooks fire for TaskState via compile_flow."""
        flow_yaml = """
name: Task Hook Flow
description: Task with hooks
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "echo done"
    result_path: $.result
    end: true
    hooks:
      on_start:
        - command: "echo on_start"
      on_complete:
        - command: "echo on_complete"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        recorder = _make_recorder(thread_id="hooks-tid", flow_name="Task Hook Flow")
        log_dir = tmp_path / ".fdsx" / "runs" / "hooks-tid" / "logs"

        hook_calls: list[dict] = []

        def fake_execute_hooks(
            hooks, *, state_name, status, data_path, thread_id, flow_name, event
        ):
            hook_calls.append(
                {
                    "state_name": state_name,
                    "status": status,
                    "count": len(hooks),
                }
            )

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch(
                "fdsx.core.compiler.compile.execute_hooks",
                side_effect=fake_execute_hooks,
            ),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(
                flow,
                recorder=recorder,
                log_dir=log_dir,
            )
            # Run the graph
            config_dict = {"configurable": {"thread_id": "hooks-tid"}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        starting_calls = [c for c in hook_calls if c["status"] == "starting"]
        completed_calls = [c for c in hook_calls if c["status"] == "completed"]
        assert len(starting_calls) >= 1, "on_start hooks should have fired"
        assert len(completed_calls) >= 1, "on_complete hooks should have fired"
        assert starting_calls[0]["state_name"] == "step1"
        assert completed_calls[0]["state_name"] == "step1"

    def test_flow_level_hooks_fire_for_all_states(self, tmp_path: Path) -> None:
        """Flow-level hooks fire for all states in the flow."""
        flow_yaml = """
name: Flow Hook Test
description: Flow-level hooks
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
    command: "echo done"
    result_path: $.result
    end: true
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        recorder = _make_recorder(thread_id="flow-hook-tid", flow_name="Flow Hook Test")
        log_dir = tmp_path / ".fdsx" / "runs" / "flow-hook-tid" / "logs"

        hook_calls: list[dict] = []

        def fake_execute_hooks(
            hooks, *, state_name, status, data_path, thread_id, flow_name, event
        ):
            hook_calls.append({"status": status, "count": len(hooks)})

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch(
                "fdsx.core.compiler.compile.execute_hooks",
                side_effect=fake_execute_hooks,
            ),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
            config_dict = {"configurable": {"thread_id": "flow-hook-tid"}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        assert any(c["status"] == "starting" for c in hook_calls), (
            "on_start should fire"
        )
        assert any(c["status"] == "completed" for c in hook_calls), (
            "on_complete should fire"
        )

    def test_config_level_hooks_merge_with_flow_and_state(self, tmp_path: Path) -> None:
        """Config-level hooks are included via collect_hooks merge."""
        flow_yaml = """
name: Config Hook Test
description: Config-level hooks
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "echo done"
    result_path: $.result
    end: true
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, _errors = load_flow(flow_path)
        assert flow is not None

        # Config with global hook
        fdsx_config = FdsxConfig(
            hooks=HookConfig(on_start=[HookEntry(command="echo global_start")])
        )

        recorder = _make_recorder(
            thread_id="config-hook-tid", flow_name="Config Hook Test"
        )
        log_dir = tmp_path / ".fdsx" / "runs" / "config-hook-tid" / "logs"

        hook_calls: list[list] = []

        def fake_execute_hooks(
            hooks, *, state_name, status, data_path, thread_id, flow_name, event
        ):
            hook_calls.append([h.command for h in hooks])

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch(
                "fdsx.core.compiler.compile.execute_hooks",
                side_effect=fake_execute_hooks,
            ),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(
                flow, recorder=recorder, config=fdsx_config, log_dir=log_dir
            )
            config_dict = {"configurable": {"thread_id": "config-hook-tid"}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        # Config-level hook should appear in the on_start call
        all_commands = [cmd for call_cmds in hook_calls for cmd in call_cmds]
        assert "echo global_start" in all_commands, (
            f"Config-level hook not found in hook calls: {hook_calls}"
        )

    def test_hook_merge_order_global_flow_state(self, tmp_path: Path) -> None:
        """Hooks merge in order: global (config) → flow → state."""
        flow_yaml = """
name: Merge Order Test
description: Hook merge order
start_at: step1
hooks:
  on_start:
    - command: "flow-hook"
states:
  step1:
    type: task
    provider: system
    command: "echo done"
    result_path: $.result
    end: true
    hooks:
      on_start:
        - command: "state-hook"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, _errors = load_flow(flow_path)
        assert flow is not None

        fdsx_config = FdsxConfig(
            hooks=HookConfig(on_start=[HookEntry(command="global-hook")])
        )

        recorder = _make_recorder(thread_id="merge-tid", flow_name="Merge Order Test")
        log_dir = tmp_path / ".fdsx" / "runs" / "merge-tid" / "logs"

        captured_hooks: list[list[str]] = []

        def fake_execute_hooks(
            hooks, *, state_name, status, data_path, thread_id, flow_name, event
        ):
            if status == "starting":
                captured_hooks.append([h.command for h in hooks])

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch(
                "fdsx.core.compiler.compile.execute_hooks",
                side_effect=fake_execute_hooks,
            ),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(
                flow, recorder=recorder, config=fdsx_config, log_dir=log_dir
            )
            config_dict = {"configurable": {"thread_id": "merge-tid"}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        assert len(captured_hooks) == 1
        cmds = captured_hooks[0]
        assert cmds == ["global-hook", "flow-hook", "state-hook"], (
            f"Expected global→flow→state order, got: {cmds}"
        )

    def test_no_hooks_configured_no_subprocess_calls(self, tmp_path: Path) -> None:
        """When no hooks are configured at any level, no execute_hooks calls are made."""
        flow_yaml = """
name: No Hook Flow
description: No hooks
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "echo done"
    result_path: $.result
    end: true
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, _errors = load_flow(flow_path)
        assert flow is not None

        recorder = _make_recorder(thread_id="no-hook-tid")
        log_dir = tmp_path / ".fdsx" / "runs" / "no-hook-tid" / "logs"

        with patch("fdsx.core.compiler.compile.execute_hooks") as mock_exec:
            compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
            config_dict = {"configurable": {"thread_id": "no-hook-tid"}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        mock_exec.assert_not_called()

    def test_pass_state_hooks_fire(self, tmp_path: Path) -> None:
        """Hooks fire for PassState nodes."""
        flow_yaml = """
name: Pass Hook Flow
description: PassState with hooks
start_at: passme
states:
  passme:
    type: pass
    end: true
    hooks:
      on_start:
        - command: "echo pass_start"
      on_complete:
        - command: "echo pass_done"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        recorder = _make_recorder(thread_id="pass-tid")
        log_dir = tmp_path / ".fdsx" / "runs" / "pass-tid" / "logs"

        hook_calls: list[dict] = []

        def fake_execute_hooks(
            hooks, *, state_name, status, data_path, thread_id, flow_name, event
        ):
            hook_calls.append({"state_name": state_name, "status": status})

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch(
                "fdsx.core.compiler.compile.execute_hooks",
                side_effect=fake_execute_hooks,
            ),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
            config_dict = {"configurable": {"thread_id": "pass-tid"}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        assert any(
            c["status"] == "starting" and c["state_name"] == "passme"
            for c in hook_calls
        )
        assert any(
            c["status"] == "completed" and c["state_name"] == "passme"
            for c in hook_calls
        )

    def test_parallel_state_hooks_wrap_dispatch_and_collector(
        self, tmp_path: Path
    ) -> None:
        """ParallelState: on_start fires in dispatch, on_complete fires in collector."""
        flow_yaml = """
name: Parallel Hook Flow
description: ParallelState with hooks
start_at: par
states:
  par:
    type: parallel
    result_path: $.results
    end: true
    hooks:
      on_start:
        - command: "echo par_start"
      on_complete:
        - command: "echo par_done"
    branches:
      - provider: system
        command: "echo branch1"
        retry: 0
      - provider: system
        command: "echo branch2"
        retry: 0
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        recorder = _make_recorder(thread_id="par-tid")
        log_dir = tmp_path / ".fdsx" / "runs" / "par-tid" / "logs"

        hook_calls: list[dict] = []

        def fake_execute_hooks(
            hooks, *, state_name, status, data_path, thread_id, flow_name, event
        ):
            hook_calls.append(
                {"state_name": state_name, "status": status, "count": len(hooks)}
            )

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch(
                "fdsx.core.compiler.compile.execute_hooks",
                side_effect=fake_execute_hooks,
            ),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
            config_dict = {"configurable": {"thread_id": "par-tid"}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        starting_calls = [c for c in hook_calls if c["status"] == "starting"]
        completed_calls = [c for c in hook_calls if c["status"] == "completed"]

        # on_start fires once (in dispatch node), on_complete fires once (in collector)
        assert len(starting_calls) == 1
        assert len(completed_calls) == 1
        assert starting_calls[0]["state_name"] == "par"
        assert completed_calls[0]["state_name"] == "par"

    def test_fdsx_base_dir_derived_from_log_dir(self, tmp_path: Path) -> None:
        """fdsx_base_dir is correctly derived as log_dir.parent.parent.parent."""
        flow_yaml = """
name: Base Dir Test
description: Test base dir derivation
start_at: s1
states:
  s1:
    type: task
    provider: system
    command: "echo x"
    result_path: $.r
    end: true
    hooks:
      on_start:
        - command: "echo h"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, _errors = load_flow(flow_path)
        assert flow is not None

        fdsx_root = tmp_path / ".fdsx"
        log_dir = fdsx_root / "runs" / "test-tid" / "logs"
        recorder = _make_recorder(thread_id="test-tid")

        write_calls: list[dict] = []

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            write_calls.append({"base_dir": base_dir})
            return tmp_path / filename

        with (
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
            patch("fdsx.core.compiler.compile.execute_hooks"),
        ):
            compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
            config_dict = {"configurable": {"thread_id": "test-tid"}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        assert len(write_calls) > 0
        assert write_calls[0]["base_dir"] == fdsx_root, (
            f"Expected base_dir={fdsx_root}, got {write_calls[0]['base_dir']}"
        )

    def test_log_dir_none_passes_none_as_base_dir(self, tmp_path: Path) -> None:
        """When log_dir is None, fdsx_base_dir is None (write_hook_data uses CWD default)."""
        flow_yaml = """
name: None Log Dir Test
description: No log dir
start_at: s1
states:
  s1:
    type: task
    provider: system
    command: "echo x"
    result_path: $.r
    end: true
    hooks:
      on_start:
        - command: "echo h"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, _errors = load_flow(flow_path)
        assert flow is not None

        recorder = _make_recorder(thread_id="none-logdir-tid")
        write_calls: list[dict] = []

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            write_calls.append({"base_dir": base_dir})
            return tmp_path / filename

        with (
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
            patch("fdsx.core.compiler.compile.execute_hooks"),
        ):
            compiled = compile_flow(flow, recorder=recorder, log_dir=None)
            config_dict = {"configurable": {"thread_id": "none-logdir-tid"}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        assert len(write_calls) > 0
        assert write_calls[0]["base_dir"] is None


# ---------------------------------------------------------------------------
# T003: FDSX_HOOKS environment variable integration tests
# ---------------------------------------------------------------------------


class TestFdsxHooksEnvVar:
    """Verify FDSX_HOOKS env var is set for hook subprocesses and absent from provider subprocesses."""

    _FLOW_WITH_ON_START_HOOK = """
name: FDSX Hooks Env Test
description: Tests that FDSX_HOOKS is set for on_start hooks
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: $.result
    end: true
    hooks:
      on_start:
        - command: "echo hook_start"
"""

    _FLOW_WITH_ON_COMPLETE_HOOK = """
name: FDSX Hooks Env Test
description: Tests that FDSX_HOOKS is set for on_complete hooks
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: $.result
    end: true
    hooks:
      on_complete:
        - command: "echo hook_complete"
"""

    _FLOW_PROVIDER_ENV_CHECK = """
name: Provider Env Check
description: Tests that FDSX_HOOKS is absent from provider subprocess env
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "sh -c 'printf \\"%s\\" \\"${FDSX_HOOKS+PRESENT}\\" > out.txt'"
    result_path: $.result
    end: true
"""

    def test_on_start_hook_observes_event_value(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """on_start hook subprocess receives FDSX_HOOKS=on_start in its environment."""
        monkeypatch.chdir(tmp_path)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(self._FLOW_WITH_ON_START_HOOK)

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        thread_id = "fdsx-hooks-env-start"
        recorder = _make_recorder(thread_id=thread_id, flow_name="FDSX Hooks Env Test")
        log_dir = tmp_path / ".fdsx" / "runs" / thread_id / "logs"

        captured_envs: list[dict] = []

        def fake_subprocess_run(cmd, **kwargs):
            env = kwargs.get("env")
            if env is not None:
                captured_envs.append(dict(env))
            result = MagicMock()
            result.returncode = 0
            return result

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch("fdsx.core.hooks.subprocess.run", side_effect=fake_subprocess_run),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
            config_dict = {"configurable": {"thread_id": thread_id}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        on_start_envs = [e for e in captured_envs if e.get("FDSX_STATUS") == "starting"]
        assert len(on_start_envs) >= 1, "on_start hook should have fired"
        assert on_start_envs[0].get("FDSX_HOOKS") == "on_start", (
            f"Expected FDSX_HOOKS='on_start', got {on_start_envs[0].get('FDSX_HOOKS')!r}"
        )

    def test_on_complete_hook_observes_event_value_success(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """on_complete hook subprocess receives FDSX_HOOKS=on_complete when node succeeds."""
        monkeypatch.chdir(tmp_path)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(self._FLOW_WITH_ON_COMPLETE_HOOK)

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        thread_id = "fdsx-hooks-env-complete"
        recorder = _make_recorder(thread_id=thread_id, flow_name="FDSX Hooks Env Test")
        log_dir = tmp_path / ".fdsx" / "runs" / thread_id / "logs"

        captured_envs: list[dict] = []

        def fake_subprocess_run(cmd, **kwargs):
            env = kwargs.get("env")
            if env is not None:
                captured_envs.append(dict(env))
            result = MagicMock()
            result.returncode = 0
            return result

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch("fdsx.core.hooks.subprocess.run", side_effect=fake_subprocess_run),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
            config_dict = {"configurable": {"thread_id": thread_id}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        on_complete_envs = [
            e for e in captured_envs if e.get("FDSX_STATUS") in ("completed", "failed")
        ]
        assert len(on_complete_envs) >= 1, "on_complete hook should have fired"
        assert on_complete_envs[0].get("FDSX_HOOKS") == "on_complete", (
            f"Expected FDSX_HOOKS='on_complete', got {on_complete_envs[0].get('FDSX_HOOKS')!r}"
        )

    def test_on_complete_hook_observes_event_value_failure(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """on_complete hook subprocess receives FDSX_HOOKS=on_complete even when node fails."""
        monkeypatch.chdir(tmp_path)

        def failing_node(state_dict: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("node exploded")

        hook = _make_hook("echo on_complete")
        recorder = _make_recorder()
        wrapped = _wrap_with_hooks(
            failing_node,
            "FailState",
            [],
            [hook],
            recorder=recorder,
            fdsx_base_dir=tmp_path,
        )

        captured_envs: list[dict] = []

        def fake_subprocess_run(cmd, **kwargs):
            env = kwargs.get("env")
            if env is not None:
                captured_envs.append(dict(env))
            result = MagicMock()
            result.returncode = 0
            return result

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch("fdsx.core.hooks.subprocess.run", side_effect=fake_subprocess_run),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
            pytest.raises(RuntimeError, match="node exploded"),
        ):
            wrapped({"x": 1})

        assert len(captured_envs) >= 1, (
            "on_complete hook should have fired after node failure"
        )
        assert captured_envs[0].get("FDSX_HOOKS") == "on_complete", (
            f"Expected FDSX_HOOKS='on_complete', got {captured_envs[0].get('FDSX_HOOKS')!r}"
        )

    def test_provider_subprocess_does_not_see_fdsx_hooks(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Provider subprocess does not receive FDSX_HOOKS when it is absent from the environment."""
        monkeypatch.chdir(tmp_path)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(self._FLOW_PROVIDER_ENV_CHECK)

        run_flow(flow_path, thread_id="provider-env-check", base_dir=tmp_path)

        out = (tmp_path / "out.txt").read_text().strip()
        assert out == "", (
            f"FDSX_HOOKS should not be present in provider subprocess env, got: {out!r}"
        )

    def test_inherited_fdsx_hooks_scrubbed_from_provider(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Stale FDSX_HOOKS inherited from the parent process is scrubbed from provider subprocess env."""
        monkeypatch.setenv("FDSX_HOOKS", "stale")
        monkeypatch.chdir(tmp_path)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(self._FLOW_PROVIDER_ENV_CHECK)

        run_flow(flow_path, thread_id="provider-env-scrub", base_dir=tmp_path)

        out = (tmp_path / "out.txt").read_text().strip()
        assert out == "", (
            f"Stale FDSX_HOOKS should be scrubbed from provider subprocess env, got: {out!r}"
        )


# ---------------------------------------------------------------------------
# T002: TestStateHookRename — on_state_start/on_state_end rename (US1)
# ---------------------------------------------------------------------------


class TestStateHookRename:
    """Verify on_state_start/on_state_end rename acceptance criteria (US1)."""

    _FLOW_WITH_ON_STATE_START_HOOK = """
name: State Hook Rename Test
description: Tests on_state_start hook fires with renamed key
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "echo done"
    result_path: $.result
    end: true
    hooks:
      on_state_start:
        - command: "echo hook_state_start"
"""

    _FLOW_WITH_ON_STATE_END_HOOK = """
name: State Hook Rename Test
description: Tests on_state_end hook fires with renamed key
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "echo done"
    result_path: $.result
    end: true
    hooks:
      on_state_end:
        - command: "echo hook_state_end"
"""

    _FLOW_WITH_LEGACY_ON_START = """
name: Legacy Key Test
description: YAML with legacy on_start key
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "echo done"
    result_path: $.result
    end: true
    hooks:
      on_start:
        - command: "echo x"
"""

    _FLOW_WITH_LEGACY_ON_COMPLETE = """
name: Legacy Key Test
description: YAML with legacy on_complete key
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "echo done"
    result_path: $.result
    end: true
    hooks:
      on_complete:
        - command: "echo x"
"""

    def test_on_state_start_fires_with_correct_fdsx_hooks_value(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """on_state_start hook subprocess receives FDSX_HOOKS='on_state_start' in its environment."""
        monkeypatch.chdir(tmp_path)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(self._FLOW_WITH_ON_STATE_START_HOOK)

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        thread_id = "fdsx-hooks-state-start"
        recorder = _make_recorder(thread_id=thread_id, flow_name="State Hook Rename Test")
        log_dir = tmp_path / ".fdsx" / "runs" / thread_id / "logs"

        captured_envs: list[dict] = []

        def fake_subprocess_run(cmd, **kwargs):
            env = kwargs.get("env")
            if env is not None:
                captured_envs.append(dict(env))
            result = MagicMock()
            result.returncode = 0
            return result

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch("fdsx.core.hooks.subprocess.run", side_effect=fake_subprocess_run),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
            config_dict = {"configurable": {"thread_id": thread_id}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        on_state_start_envs = [
            e for e in captured_envs if e.get("FDSX_STATUS") == "starting"
        ]
        assert len(on_state_start_envs) >= 1, "on_state_start hook should have fired"
        assert on_state_start_envs[0].get("FDSX_HOOKS") == "on_state_start", (
            f"Expected FDSX_HOOKS='on_state_start', got {on_state_start_envs[0].get('FDSX_HOOKS')!r}"
        )

    def test_on_state_end_fires_with_correct_fdsx_hooks_value(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """on_state_end hook subprocess receives FDSX_HOOKS='on_state_end' in its environment."""
        monkeypatch.chdir(tmp_path)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(self._FLOW_WITH_ON_STATE_END_HOOK)

        flow, errors = load_flow(flow_path)
        assert flow is not None, f"Load errors: {errors}"

        thread_id = "fdsx-hooks-state-end"
        recorder = _make_recorder(thread_id=thread_id, flow_name="State Hook Rename Test")
        log_dir = tmp_path / ".fdsx" / "runs" / thread_id / "logs"

        captured_envs: list[dict] = []

        def fake_subprocess_run(cmd, **kwargs):
            env = kwargs.get("env")
            if env is not None:
                captured_envs.append(dict(env))
            result = MagicMock()
            result.returncode = 0
            return result

        def fake_write_hook_data(data, *, state_name, filename, thread_id, base_dir):
            return tmp_path / filename

        with (
            patch("fdsx.core.hooks.subprocess.run", side_effect=fake_subprocess_run),
            patch(
                "fdsx.core.compiler.compile.write_hook_data",
                side_effect=fake_write_hook_data,
            ),
        ):
            compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
            config_dict = {"configurable": {"thread_id": thread_id}}
            list(
                compiled.graph.stream(
                    {"_meta": {}}, config=config_dict, stream_mode="values"
                )
            )

        on_state_end_envs = [
            e for e in captured_envs if e.get("FDSX_STATUS") in ("completed", "failed")
        ]
        assert len(on_state_end_envs) >= 1, "on_state_end hook should have fired"
        assert on_state_end_envs[0].get("FDSX_HOOKS") == "on_state_end", (
            f"Expected FDSX_HOOKS='on_state_end', got {on_state_end_envs[0].get('FDSX_HOOKS')!r}"
        )

    def test_legacy_on_start_rejected_by_load_flow(self, tmp_path: Path) -> None:
        """load_flow rejects YAML with legacy 'on_start' key and hints at 'on_state_start'."""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(self._FLOW_WITH_LEGACY_ON_START)

        flow, errors = load_flow(flow_path)

        assert flow is None, "Expected load_flow to fail with legacy on_start key"
        assert any("on_state_start" in e for e in errors), (
            f"Expected error hinting at 'on_state_start', got: {errors}"
        )

    def test_legacy_on_complete_rejected_by_load_flow(self, tmp_path: Path) -> None:
        """load_flow rejects YAML with legacy 'on_complete' key and hints at 'on_state_end'."""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(self._FLOW_WITH_LEGACY_ON_COMPLETE)

        flow, errors = load_flow(flow_path)

        assert flow is None, "Expected load_flow to fail with legacy on_complete key"
        assert any("on_state_end" in e for e in errors), (
            f"Expected error hinting at 'on_state_end', got: {errors}"
        )

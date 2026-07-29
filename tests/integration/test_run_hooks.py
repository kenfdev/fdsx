"""Integration tests for run-level lifecycle hooks CLI wiring (T006-T010).

Tests cover:
- T006: _compute_run_status() pure helper
- T007: run() handler wiring (single-flow and tasks-dir)
- T008: resume() handler wiring
- T009: tasks-dir aggregate status passed to on_run_end
- T010: hook failure policy and merge order
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from fdsx.cli.main import _compute_run_status, app
from fdsx.core.config import RunHookConfig
from fdsx.core.engine import run_flow
from fdsx.core.hooks import collect_run_hooks, execute_run_hooks
from fdsx.core.loader import load_flow
from fdsx.models.flow import HookEntry

runner = CliRunner()

_SIMPLE_FLOW_YAML = """
name: SimpleFlow
description: Minimal workflow for run hook testing
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.result"
    end: true
"""


# ---------------------------------------------------------------------------
# T006: TestComputeRunStatus
# ---------------------------------------------------------------------------


class TestComputeRunStatus:
    """T006: Pure helper _compute_run_status()."""

    def test_all_completed_returns_completed(self) -> None:
        """All results with status=completed yields 'completed'."""
        results = [{"status": "completed"}, {"status": "completed"}]
        assert _compute_run_status(results) == "completed"

    def test_all_failed_returns_failed(self) -> None:
        """All results with status=failed yields 'failed'."""
        results = [{"status": "failed"}, {"status": "failed"}]
        assert _compute_run_status(results) == "failed"

    def test_mixed_returns_partial(self) -> None:
        """Mixed completed and failed yields 'partial'."""
        results = [{"status": "completed"}, {"status": "failed"}]
        assert _compute_run_status(results) == "partial"

    def test_empty_returns_completed(self) -> None:
        """An empty task queue is a successful no-op."""
        assert _compute_run_status([]) == "completed"

    def test_single_completed_returns_completed(self) -> None:
        """Single completed result yields 'completed'."""
        assert _compute_run_status([{"status": "completed"}]) == "completed"


# ---------------------------------------------------------------------------
# T007: TestRunHooksWiring
# ---------------------------------------------------------------------------


class TestRunHooksWiring:
    """T007: run() handler fires on_run_start and on_run_end correctly."""

    def test_single_flow_fires_start_and_end_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """on_run_start fires before run_flow; on_run_end fires with 'completed' on success."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_SIMPLE_FLOW_YAML)

        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch(
                "fdsx.cli.main.engine.run_flow",
                return_value=MagicMock(status="completed"),
            ),
        ):
            runner.invoke(app, ["run", str(flow_path)])

        assert mock_exec.call_count == 2
        start_call = next(
            c
            for c in mock_exec.call_args_list
            if c.kwargs.get("event") == "on_run_start"
        )
        end_call = next(
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        )
        assert start_call.kwargs["status"] == "starting"
        assert end_call.kwargs["status"] == "completed"

    def test_single_flow_fires_start_before_run_flow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """on_run_start is the first execute_run_hooks call (before engine.run_flow)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_SIMPLE_FLOW_YAML)

        call_order: list[str] = []

        def record_hook(*args, **kwargs) -> None:
            call_order.append(f"hook:{kwargs.get('event')}")

        def record_run_flow(*args, **kwargs) -> None:
            call_order.append("run_flow")

        with (
            patch("fdsx.cli.main.execute_run_hooks", side_effect=record_hook),
            patch("fdsx.cli.main.engine.run_flow", side_effect=record_run_flow),
        ):
            runner.invoke(app, ["run", str(flow_path)])

        assert call_order[0] == "hook:on_run_start"
        assert "run_flow" in call_order
        assert call_order[-1] == "hook:on_run_end"

    def test_tasks_dir_fires_on_run_end_with_computed_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """on_run_end fires with status from _compute_run_status in tasks-dir mode."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        fake_results = [{"status": "completed"}]
        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch("fdsx.cli.main.engine.run_tasks_dir", return_value=fake_results),
        ):
            runner.invoke(app, ["run", "--tasks-dir", str(tasks_dir)])

        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "completed"

    def test_validation_error_does_not_fire_on_run_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FlowValidationError (exit code 2) does not trigger on_run_end."""
        from fdsx.core.engine import FlowValidationError

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_SIMPLE_FLOW_YAML)

        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch(
                "fdsx.cli.main.engine.run_flow",
                side_effect=FlowValidationError("bad flow"),
            ),
        ):
            result = runner.invoke(app, ["run", str(flow_path)])

        assert result.exit_code == 2
        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 0

    def test_runtime_error_fires_on_run_end_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RuntimeError from run_flow fires on_run_end with status='failed'."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_SIMPLE_FLOW_YAML)

        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch(
                "fdsx.cli.main.engine.run_flow",
                side_effect=RuntimeError("provider crashed"),
            ),
        ):
            result = runner.invoke(app, ["run", str(flow_path)])

        assert result.exit_code == 1
        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "failed"


# ---------------------------------------------------------------------------
# T008: TestResumeHooksWiring
# ---------------------------------------------------------------------------


class TestResumeHooksWiring:
    """T008: resume() handler fires on_run_start and on_run_end correctly."""

    def test_resume_fires_start_and_end_on_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """on_run_start fires with 'starting'; on_run_end fires with 'completed' on success."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch(
                "fdsx.cli.main.engine.resume_flow",
                return_value=MagicMock(status="completed"),
            ),
        ):
            runner.invoke(app, ["resume", "--thread-id", "test-thread"])

        start_calls = [
            c
            for c in mock_exec.call_args_list
            if c.kwargs.get("event") == "on_run_start"
        ]
        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(start_calls) == 1
        assert start_calls[0].kwargs["status"] == "starting"
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "completed"

    def test_resume_fires_on_run_end_failed_on_runtime_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RuntimeError from resume_flow fires on_run_end with status='failed'."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch(
                "fdsx.cli.main.engine.resume_flow",
                side_effect=RuntimeError("something went wrong"),
            ),
        ):
            result = runner.invoke(app, ["resume", "--thread-id", "test-thread"])

        assert result.exit_code == 1
        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "failed"

    def test_resume_non_success_fires_on_run_end_once_with_result_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch(
                "fdsx.cli.main.engine.resume_flow",
                return_value=MagicMock(status="max_loop_reached"),
            ),
        ):
            result = runner.invoke(app, ["resume", "--thread-id", "test-thread"])

        assert result.exit_code == 1
        end_calls = [
            call
            for call in mock_exec.call_args_list
            if call.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "max_loop_reached"

    def test_resume_fires_on_run_end_failed_on_no_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'No checkpoint found' RuntimeError fires on_run_end with status='failed'."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch(
                "fdsx.cli.main.engine.resume_flow",
                side_effect=RuntimeError("No checkpoint found for thread"),
            ),
        ):
            result = runner.invoke(app, ["resume", "--thread-id", "missing-thread"])

        assert result.exit_code == 2
        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "failed"

    def test_resume_fires_on_run_end_failed_on_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Generic Exception from resume_flow fires on_run_end with status='failed'."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch(
                "fdsx.cli.main.engine.resume_flow",
                side_effect=ValueError("unexpected"),
            ),
        ):
            result = runner.invoke(app, ["resume", "--thread-id", "test-thread"])

        assert result.exit_code == 1
        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "failed"


# ---------------------------------------------------------------------------
# T009: TestTasksDirAggregateStatus
# ---------------------------------------------------------------------------


class TestTasksDirAggregateStatus:
    """T009: tasks-dir mode passes correct aggregate status to on_run_end."""

    def _invoke_with_results(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_results: list[dict],
    ) -> tuple[MagicMock, object]:
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        with (
            patch("fdsx.cli.main.execute_run_hooks") as mock_exec,
            patch("fdsx.cli.main.engine.run_tasks_dir", return_value=fake_results),
        ):
            result = runner.invoke(app, ["run", "--tasks-dir", str(tasks_dir)])

        return mock_exec, result

    def test_all_completed_passes_completed_and_exits_0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All tasks completed → status='completed', exit code 0."""
        fake_results = [{"status": "completed"}, {"status": "completed"}]
        mock_exec, result = self._invoke_with_results(
            tmp_path, monkeypatch, fake_results
        )

        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "completed"
        assert result.exit_code == 0

    def test_all_failed_passes_failed_and_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All tasks failed → status='failed', exit code 1."""
        fake_results = [{"status": "failed"}, {"status": "failed"}]
        mock_exec, result = self._invoke_with_results(
            tmp_path, monkeypatch, fake_results
        )

        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "failed"
        assert result.exit_code == 1

    def test_partial_passes_partial_and_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mixed results → status='partial', exit code 1."""
        fake_results = [{"status": "completed"}, {"status": "failed"}]
        mock_exec, result = self._invoke_with_results(
            tmp_path, monkeypatch, fake_results
        )

        end_calls = [
            c for c in mock_exec.call_args_list if c.kwargs.get("event") == "on_run_end"
        ]
        assert len(end_calls) == 1
        assert end_calls[0].kwargs["status"] == "partial"
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# T010: TestRunHookFailurePolicy
# ---------------------------------------------------------------------------


class TestRunHookFailurePolicy:
    """T010: Hook failure policy and merge order."""

    def test_merge_order_global_before_project(self) -> None:
        """collect_run_hooks returns global hooks before project hooks."""
        global_cfg = RunHookConfig(on_run_start=[HookEntry(command="global-hook")])
        project_cfg = RunHookConfig(on_run_start=[HookEntry(command="project-hook")])

        result = collect_run_hooks(
            "on_run_start",
            global_run_hooks=global_cfg,
            project_run_hooks=project_cfg,
        )

        commands = [h.command for h in result]
        assert commands == ["global-hook", "project-hook"]

    def test_failing_hook_warns_and_does_not_raise(self, caplog) -> None:
        """A hook exiting non-zero logs a warning and does not abort execution."""
        hook = HookEntry(command="false", on_failure="abort")  # type: ignore[arg-type]
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            with caplog.at_level(logging.WARNING, logger="fdsx.core.hooks"):
                execute_run_hooks([hook], status="starting", event="on_run_start")

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "Expected a WARNING log when run hook exits non-zero"
        )

    def test_timeout_warns_and_does_not_raise(self, caplog) -> None:
        """A timed-out hook logs a warning and does not abort execution."""
        import subprocess as _subprocess

        hook = HookEntry(command="sleep 999")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _subprocess.TimeoutExpired(
                cmd="sleep 999", timeout=30.0
            )
            with caplog.at_level(logging.WARNING, logger="fdsx.core.hooks"):
                execute_run_hooks([hook], status="starting", event="on_run_start")

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "Expected a WARNING log when run hook times out"
        )

    def test_on_run_end_only_hooks_are_collected_for_end_event(self) -> None:
        """collect_run_hooks with on_run_end only returns on_run_end hooks, not on_run_start."""
        cfg = RunHookConfig(
            on_run_start=[HookEntry(command="start-only")],
            on_run_end=[HookEntry(command="end-only")],
        )

        result = collect_run_hooks(
            "on_run_end",
            global_run_hooks=cfg,
            project_run_hooks=None,
        )

        commands = [h.command for h in result]
        assert commands == ["end-only"]
        assert "start-only" not in commands


# ---------------------------------------------------------------------------
# T011: TestYamlRejectionEndToEnd
# ---------------------------------------------------------------------------


class TestYamlRejectionEndToEnd:
    """T011: End-to-end YAML rejection for on_run_start/on_run_end in flow/state hooks."""

    _BASE_YAML = """\
name: FlowWithBadHook
description: Test hook rejection
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.result"
    end: true
"""

    def test_flow_level_on_run_start_rejected(self, tmp_path: Path) -> None:
        """on_run_start in flow-level hooks: block is rejected with a clear error."""
        flow_yaml = (
            self._BASE_YAML + "hooks:\n  on_run_start:\n    - command: echo hi\n"
        )
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, errors = load_flow(flow_path)

        assert flow is None
        assert errors
        assert any("global or project configuration" in e for e in errors)

    def test_flow_level_on_run_end_rejected(self, tmp_path: Path) -> None:
        """on_run_end in flow-level hooks: block is rejected with a clear error."""
        flow_yaml = self._BASE_YAML + "hooks:\n  on_run_end:\n    - command: echo bye\n"
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, errors = load_flow(flow_path)

        assert flow is None
        assert errors
        assert any("global or project configuration" in e for e in errors)

    def test_state_level_on_run_start_rejected(self, tmp_path: Path) -> None:
        """on_run_start in state-level hooks: block is rejected with a clear error."""
        flow_yaml = """\
name: FlowWithBadHook
description: Test state-level on_run_start rejection
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.result"
    end: true
    hooks:
      on_run_start:
        - command: echo hi
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, errors = load_flow(flow_path)

        assert flow is None
        assert errors
        assert any("global or project configuration" in e for e in errors)

    def test_state_level_on_run_end_rejected(self, tmp_path: Path) -> None:
        """on_run_end in state-level hooks: block is rejected with a clear error."""
        flow_yaml = """\
name: FlowWithBadHook
description: Test state-level on_run_end rejection
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.result"
    end: true
    hooks:
      on_run_end:
        - command: echo bye
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, errors = load_flow(flow_path)

        assert flow is None
        assert errors
        assert any("global or project configuration" in e for e in errors)


# ---------------------------------------------------------------------------
# T014: TestScrubRuleRegression
# ---------------------------------------------------------------------------

_FLOW_SCRUB_CHECK = """\
name: ScrubCheck
description: Confirms FDSX_HOOKS is absent from provider subprocess env
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "sh -c 'printf \\"%s\\" \\"${FDSX_HOOKS+PRESENT}\\" > out.txt'"
    result_path: $.result
    end: true
"""


class TestScrubRuleRegression:
    """T014: FDSX_HOOKS scrub rule regression for run-level hook values."""

    def test_on_run_start_value_scrubbed_from_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FDSX_HOOKS='on_run_start' inherited from parent is scrubbed before provider subprocess runs."""
        monkeypatch.setenv("FDSX_HOOKS", "on_run_start")
        monkeypatch.chdir(tmp_path)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_FLOW_SCRUB_CHECK)

        run_flow(flow_path, base_dir=tmp_path)

        out = (tmp_path / "out.txt").read_text().strip()
        assert out == "", (
            f"FDSX_HOOKS='on_run_start' should be scrubbed from provider subprocess env, got: {out!r}"
        )

    def test_on_run_end_value_scrubbed_from_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FDSX_HOOKS='on_run_end' inherited from parent is scrubbed before provider subprocess runs."""
        monkeypatch.setenv("FDSX_HOOKS", "on_run_end")
        monkeypatch.chdir(tmp_path)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_FLOW_SCRUB_CHECK)

        run_flow(flow_path, base_dir=tmp_path)

        out = (tmp_path / "out.txt").read_text().strip()
        assert out == "", (
            f"FDSX_HOOKS='on_run_end' should be scrubbed from provider subprocess env, got: {out!r}"
        )

"""Integration tests for US2: workflow lifecycle hooks (T016).

Tests cover Scenarios 1-7, 10-12, 14:
- Scenario 1:  on_workflow_start fires exactly once on fresh run_flow
- Scenario 2:  on_workflow_start does NOT fire on resume_flow
- Scenario 3:  on_workflow_end fires with status='completed' on normal completion
- Scenario 4:  on_workflow_end fires with status='failed' on provider failure
- Scenario 5:  on_workflow_end fires with status='aborted' when state hook aborts
- Scenario 6:  on_workflow_end does NOT fire when SIGINT interrupts execution
- Scenario 7:  on_workflow_end does NOT fire when SIGTERM interrupts execution
- Scenario 10: Declaring on_workflow_start/end in a state block raises a load error
- Scenario 11: Global → flow merge order for on_workflow_start hooks
- Scenario 12: Workflow hook failure warns and continues (never raises)
- Scenario 14: Inactivity timeout triggers on_workflow_end with failure status

TDD note: Tests 1-5, 10-12, 14 will FAIL (or ERROR) until T018-T024 land.
Tests 2, 6, 7 (negative assertions) pass trivially before implementation and
serve as regression guards after implementation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.loader import load_flow
from fdsx.models.flow import HookEntry

# ---------------------------------------------------------------------------
# Shared YAML fixtures (inline — no dependency on tests/fixtures/)
# ---------------------------------------------------------------------------

_SIMPLE_FLOW_YAML = """
name: HookTestFlow
description: Minimal system-provider flow for workflow hook testing
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.result"
    end: true
"""

_HOOK_FLOW_YAML = """
name: HookTestFlow
description: Flow with workflow-level hooks
start_at: step1
hooks:
  on_workflow_start:
    - command: "echo wf_start"
  on_workflow_end:
    - command: "echo wf_end"
states:
  step1:
    type: task
    provider: system
    command: echo done
    retry: 0
    result_path: "$.result"
    end: true
"""

_FAIL_FLOW_YAML = """
name: FailFlow
description: Flow that always fails
start_at: step1
hooks:
  on_workflow_end:
    - command: "echo wf_end"
states:
  step1:
    type: task
    provider: system
    command: "exit 1"
    retry: 0
    result_path: "$.result"
    end: true
"""

_ABORT_HOOK_FLOW_YAML = """
name: AbortHookFlow
description: Flow aborted by a state hook with on_failure=abort
start_at: step1
hooks:
  on_workflow_end:
    - command: "echo wf_end"
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.result"
    end: true
    hooks:
      on_state_start:
        - command: "exit 1"
          on_failure: abort
"""

_WAIT_FLOW_YAML = """
name: WaitTestFlow
description: Flow with wait state for resume testing
start_at: step1
hooks:
  on_workflow_start:
    - command: "echo wf_start"
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.step1_result"
    next: wait1
  wait1:
    type: wait
    message: "Continue?"
    choices:
      - "yes"
    result_path: "$.choice"
    end: true
"""


# ---------------------------------------------------------------------------
# Scenario 1 & 2: TestWorkflowStart
# ---------------------------------------------------------------------------


class TestWorkflowStart:
    """Scenarios 1 and 2: on_workflow_start fires on fresh run, not on resume."""

    def test_fires_once_on_fresh_run(self, tmp_path: Path) -> None:
        """Scenario 1: on_workflow_start fires exactly once at the start of run_flow."""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_HOOK_FLOW_YAML)
        base_dir = tmp_path / ".fdsx"

        with patch(
            "fdsx.core.engine.run.execute_workflow_hooks", create=True
        ) as mock_wh:
            run_flow(flow_path, base_dir=base_dir)

        start_calls = [
            c
            for c in mock_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_start"
        ]
        assert mock_wh.call_count >= 1, (
            "execute_workflow_hooks should be called at least once"
        )
        assert len(start_calls) == 1, (
            f"on_workflow_start should fire exactly once, got {len(start_calls)} calls"
        )
        assert start_calls[0].kwargs.get("status") == "starting"

    def test_does_not_fire_on_resume(self, tmp_path: Path) -> None:
        """Scenario 2: on_workflow_start must NOT fire when resuming from checkpoint."""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_WAIT_FLOW_YAML)
        base_dir = tmp_path / ".fdsx"
        thread_id = "no-start-on-resume-thread"

        # Step 1: Run until wait state, simulating a crash at the prompt to create checkpoint
        with (
            pytest.raises(RuntimeError),
            patch(
                "fdsx.core.engine.interrupts.display_wait_prompt",
                side_effect=RuntimeError("simulated crash at wait"),
            ),
        ):
            run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)

        # Step 2: Resume — on_workflow_start must NOT fire
        with (
            patch(
                "fdsx.core.engine.resume.execute_workflow_hooks", create=True
            ) as mock_wh,
            patch("builtins.input", return_value="1"),
        ):
            resume_flow(thread_id, base_dir, flow_path)

        start_calls = [
            c
            for c in mock_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_start"
        ]
        assert len(start_calls) == 0, (
            "on_workflow_start must NOT fire during resume_flow"
        )


# ---------------------------------------------------------------------------
# Scenarios 3-7: TestWorkflowEnd
# ---------------------------------------------------------------------------


class TestWorkflowEnd:
    """Scenarios 3-7: on_workflow_end fires or doesn't fire in various terminal states."""

    def test_fires_on_complete(self, tmp_path: Path) -> None:
        """Scenario 3: on_workflow_end fires with status='completed' on normal completion."""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_HOOK_FLOW_YAML)
        base_dir = tmp_path / ".fdsx"

        with patch(
            "fdsx.core.engine.run.execute_workflow_hooks", create=True
        ) as mock_wh:
            run_flow(flow_path, base_dir=base_dir)

        end_calls = [
            c
            for c in mock_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_end"
        ]
        assert len(end_calls) == 1, "on_workflow_end should fire on completion"
        assert end_calls[0].kwargs.get("status") == "completed"

    def test_fires_on_failed(self, tmp_path: Path) -> None:
        """Scenario 4: on_workflow_end fires with status='failed' when provider fails."""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_FAIL_FLOW_YAML)
        base_dir = tmp_path / ".fdsx"

        with (
            patch(
                "fdsx.core.engine.run.execute_workflow_hooks", create=True
            ) as mock_wh,
            pytest.raises(RuntimeError),
        ):
            run_flow(flow_path, base_dir=base_dir)

        end_calls = [
            c
            for c in mock_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_end"
        ]
        assert len(end_calls) == 1, "on_workflow_end should fire on provider failure"
        assert end_calls[0].kwargs.get("status") == "failed"

    def test_fires_on_aborted_by_state_hook(self, tmp_path: Path) -> None:
        """Scenario 5: on_workflow_end fires when a state hook abort policy triggers."""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_ABORT_HOOK_FLOW_YAML)
        base_dir = tmp_path / ".fdsx"

        with (
            patch(
                "fdsx.core.engine.run.execute_workflow_hooks", create=True
            ) as mock_wh,
            pytest.raises(RuntimeError),
        ):
            run_flow(flow_path, base_dir=base_dir)

        end_calls = [
            c
            for c in mock_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_end"
        ]
        assert len(end_calls) == 1, (
            "on_workflow_end should fire when a state hook aborts"
        )
        assert end_calls[0].kwargs.get("status") == "aborted"

    def test_does_not_fire_on_sigint(self, tmp_path: Path) -> None:
        """Scenario 6: on_workflow_end must NOT fire when SIGINT (SystemExit) interrupts execution."""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_SIMPLE_FLOW_YAML)
        base_dir = tmp_path / ".fdsx"
        thread_id = "sigint-test-thread"

        # Simulate SIGINT by raising SystemExit during provider execution
        with (
            patch(
                "fdsx.core.engine.run.execute_workflow_hooks", create=True
            ) as mock_wh,
            patch(
                "fdsx.providers.system._run_subprocess",
                side_effect=SystemExit(130),
            ),
            pytest.raises(SystemExit),
        ):
            run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)

        end_calls = [
            c
            for c in mock_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_end"
        ]
        assert len(end_calls) == 0, (
            "on_workflow_end must NOT fire when SIGINT interrupts execution"
        )

    def test_does_not_fire_on_sigterm(self, tmp_path: Path) -> None:
        """Scenario 7: on_workflow_end must NOT fire when SIGTERM (SystemExit) interrupts execution."""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_SIMPLE_FLOW_YAML)
        base_dir = tmp_path / ".fdsx"
        thread_id = "sigterm-test-thread"

        # Simulate SIGTERM by raising SystemExit during provider execution
        with (
            patch(
                "fdsx.core.engine.run.execute_workflow_hooks", create=True
            ) as mock_wh,
            patch(
                "fdsx.providers.system._run_subprocess",
                side_effect=SystemExit(143),
            ),
            pytest.raises(SystemExit),
        ):
            run_flow(flow_path, thread_id=thread_id, base_dir=base_dir)

        end_calls = [
            c
            for c in mock_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_end"
        ]
        assert len(end_calls) == 0, (
            "on_workflow_end must NOT fire when SIGTERM interrupts execution"
        )


# ---------------------------------------------------------------------------
# Scenario 10: TestWorkflowScope
# ---------------------------------------------------------------------------


class TestWorkflowScope:
    """Scenario 10: on_workflow_start/end declared in a state block must be rejected."""

    def test_declaring_workflow_hook_in_state_block_errors(
        self, tmp_path: Path
    ) -> None:
        """Scenario 10: YAML with on_workflow_start inside a state hooks block must fail validation."""
        flow_yaml = """
name: BadScopeFlow
description: Invalid flow using workflow hook in state block
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.result"
    end: true
    hooks:
      on_workflow_start:
        - command: "echo bad"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, _errors = load_flow(flow_path)

        assert flow is None, (
            "load_flow should fail when on_workflow_start is declared inside a state block"
        )

    def test_declaring_workflow_end_hook_in_state_block_errors(
        self, tmp_path: Path
    ) -> None:
        """Scenario 10b: YAML with on_workflow_end inside a state hooks block must fail validation."""
        flow_yaml = """
name: BadScopeFlow2
description: Invalid flow using workflow end hook in state block
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: "$.result"
    end: true
    hooks:
      on_workflow_end:
        - command: "echo bad"
"""
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        flow, _errors = load_flow(flow_path)

        assert flow is None, (
            "load_flow should fail when on_workflow_end is declared inside a state block"
        )


# ---------------------------------------------------------------------------
# Scenario 11: TestWorkflowMerge
# ---------------------------------------------------------------------------


class TestWorkflowMerge:
    """Scenario 11: Global → project → flow merge order for on_workflow_start."""

    def test_global_project_flow_merge_order(self, tmp_path: Path) -> None:
        """Scenario 11: collect_workflow_hooks merges in global → project → flow order."""
        from fdsx.core.hooks import collect_workflow_hooks
        from fdsx.models.flow import HookConfig

        global_cfg = HookConfig(on_workflow_start=[HookEntry(command="global-wf-hook")])
        project_cfg = HookConfig(
            on_workflow_start=[HookEntry(command="project-wf-hook")]
        )
        flow_cfg = HookConfig(on_workflow_start=[HookEntry(command="flow-wf-hook")])

        result = collect_workflow_hooks(
            "on_workflow_start",
            global_hooks=global_cfg,
            project_hooks=project_cfg,
            flow_hooks=flow_cfg,
        )

        commands = [h.command for h in result]
        assert commands == ["global-wf-hook", "project-wf-hook", "flow-wf-hook"], (
            f"Expected global → project → flow order, got: {commands}"
        )


# ---------------------------------------------------------------------------
# Scenario 12: TestWorkflowFailure
# ---------------------------------------------------------------------------


class TestWorkflowFailure:
    """Scenario 12: workflow hook failure warns and never raises."""

    def test_hook_failure_warns_and_continues(self, caplog) -> None:
        """Scenario 12: When a workflow hook fails, it warns and does not raise."""
        import logging

        from fdsx.core.hooks import execute_workflow_hooks

        hook = HookEntry(command="false", on_failure="abort")  # type: ignore[arg-type]
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            # Must NOT raise — abort policy is silently demoted to warn for workflow hooks
            with caplog.at_level(logging.WARNING, logger="fdsx.core.hooks"):
                execute_workflow_hooks(
                    [hook],
                    status="starting",
                    thread_id="t1",
                    flow_name="FailFlow",
                    event="on_workflow_start",
                )

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "Expected a WARNING log when workflow hook exits non-zero"
        )


# ---------------------------------------------------------------------------
# Scenario 14: TestWorkflowTimeout
# ---------------------------------------------------------------------------


class TestWorkflowTimeout:
    """Scenario 14: Inactivity timeout triggers on_workflow_end with failure status."""

    def test_fires_on_inactivity_timeout(self, tmp_path: Path) -> None:
        """Scenario 14: on_workflow_end fires with status='failed' on inactivity timeout."""
        from fdsx.providers.base import ProviderResult

        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(_HOOK_FLOW_YAML)
        base_dir = tmp_path / ".fdsx"

        inactivity_result = ProviderResult(
            exit_code=124,
            stdout="",
            stderr="Process killed due to inactivity timeout after 300 seconds (no output received)",
        )

        with (
            patch(
                "fdsx.core.engine.run.execute_workflow_hooks", create=True
            ) as mock_wh,
            patch(
                "fdsx.providers.system._run_subprocess", return_value=inactivity_result
            ),
            pytest.raises(RuntimeError),
        ):
            run_flow(flow_path, base_dir=base_dir)

        end_calls = [
            c
            for c in mock_wh.call_args_list
            if c.kwargs.get("event") == "on_workflow_end"
        ]
        assert len(end_calls) == 1, "on_workflow_end should fire on inactivity timeout"
        assert end_calls[0].kwargs.get("status") in ("failed", "aborted")

    def test_hook_hang_is_killed_after_timeout(self, caplog) -> None:
        """Scenario 14b: A slow workflow hook is killed after timeout_seconds; does not raise."""
        import logging

        from fdsx.core.hooks import execute_workflow_hooks

        hook = HookEntry(command="sleep 9999")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd="sleep 9999", timeout=2.0
            )
            # Must NOT raise TimeoutExpired
            with caplog.at_level(logging.WARNING, logger="fdsx.core.hooks"):
                execute_workflow_hooks(
                    [hook],
                    status="starting",
                    thread_id="t1",
                    flow_name="TestFlow",
                    event="on_workflow_start",
                    timeout_seconds=2.0,
                )

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "Expected a WARNING log when workflow hook times out"
        )

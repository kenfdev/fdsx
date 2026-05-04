"""Integration tests for workflow-level retry escalation (T001).

Mock _run_subprocess per project convention (never invoke real provider binaries).
All tests that require the escalation feature fail until implementation is complete.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.core.engine import run_flow
from fdsx.logging.recorder import RUNS_DIR_NAME, RunRecorder
from fdsx.providers.base import ProviderResult

FAIL = ProviderResult(exit_code=1, stdout="", stderr="provider error")
SUCCESS_CLAUDE = ProviderResult(exit_code=0, stdout="claude result", stderr="")
SUCCESS_CODEX = ProviderResult(exit_code=0, stdout="escalated result", stderr="")


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "flow.yaml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Shared YAML templates
# ---------------------------------------------------------------------------

_CLAUDE_TASK_WITH_ESCALATION = """\
name: escalation-test
description: Test retry escalation to codex
start_at: step1
states:
  step1:
    type: task
    provider: claude
    model: claude-sonnet-4-6
    prompt_template: do the thing
    result_path: $.result
    retry: 2
    end: true
retry_escalation:
  provider: codex
  model: gpt-4o
"""

_CLAUDE_TASK_NO_ESCALATION = """\
name: no-escalation-test
description: Identical task without retry_escalation
start_at: step1
states:
  step1:
    type: task
    provider: claude
    model: claude-sonnet-4-6
    prompt_template: do the thing
    result_path: $.result
    retry: 2
    end: true
"""

_SYSTEM_TASK_WITH_ESCALATION = """\
name: system-escalation-test
description: System task with retry_escalation declared
start_at: step1
states:
  step1:
    type: task
    provider: system
    command: "exit 1"
    result_path: $.result
    retry: 1
    end: true
retry_escalation:
  provider: codex
  model: gpt-4o
"""

_PROFILE_ESCALATION = """\
name: profile-escalation-test
description: Escalation via flow-level profile
start_at: step1
states:
  step1:
    type: task
    provider: claude
    model: claude-sonnet-4-6
    prompt_template: do the thing
    result_path: $.result
    retry: 2
    end: true
profiles:
  my-esc:
    provider: codex
    model: gpt-4o
retry_escalation:
  profile: my-esc
"""

_PARALLEL_WITH_ESCALATION = """\
name: parallel-escalation-test
description: Parallel branches with retry escalation
start_at: par
states:
  par:
    type: parallel
    branches:
      - provider: claude
        model: claude-sonnet-4-6
        prompt_template: branch A
        result_path: $.a
        retry: 1
      - provider: claude
        model: claude-sonnet-4-6
        prompt_template: branch B
        result_path: $.b
        retry: 1
    result_path: $.results
    end: true
retry_escalation:
  provider: codex
  model: gpt-4o
"""

_MAP_WITH_ESCALATION = """\
name: map-escalation-test
description: Map iterator with retry escalation
start_at: setup_items
states:
  setup_items:
    type: pass
    parameters:
      $.items:
        - a
        - b
    next: process
  process:
    type: map
    items_path: $.items
    iterator:
      states:
        - name: item_task
          type: task
          provider: claude
          model: claude-sonnet-4-6
          prompt_template: process item
          result_path: $.item_result
          retry: 1
    result_path: $.results
    end: true
retry_escalation:
  provider: codex
  model: gpt-4o
"""


class TestRetryEscalationBasic:
    def test_first_fail_escalated_retry_succeeds(self, tmp_path):
        """When attempt 0 fails, attempt 1 uses escalated (codex) provider and succeeds."""
        path = _write_yaml(tmp_path, _CLAUDE_TASK_WITH_ESCALATION)
        codex_calls: list = []

        def codex_side(args, **kwargs):
            codex_calls.append(args)
            return SUCCESS_CODEX

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", side_effect=codex_side),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            result = run_flow(path, base_dir=tmp_path)

        assert len(codex_calls) >= 1, "escalated provider (codex) was never called"
        assert result.results["result"] == "escalated result"

    def test_all_attempts_fail_error_names_last_provider(self, tmp_path):
        """After all retries fail, error message includes '(escalated from claude)' and names codex."""
        path = _write_yaml(tmp_path, _CLAUDE_TASK_WITH_ESCALATION)

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", return_value=FAIL),
            patch("fdsx.core.compiler.execution.time.sleep"),
            pytest.raises(Exception) as exc_info,
        ):
            run_flow(path, base_dir=tmp_path)

        msg = str(exc_info.value)
        assert "(escalated from claude)" in msg, (
            f"expected '(escalated from claude)' in error message, got: {msg!r}"
        )
        assert "codex" in msg.lower(), (
            f"expected 'codex' (last provider) in error message, got: {msg!r}"
        )

    def test_no_escalation_regression_guard(self, tmp_path):
        """Workflow without retry_escalation field behaves identically to today."""
        path = _write_yaml(tmp_path, _CLAUDE_TASK_NO_ESCALATION)
        call_count = [0]

        def claude_side(args, **kwargs):
            call_count[0] += 1
            return SUCCESS_CLAUDE

        with (
            patch("fdsx.providers.claude._run_subprocess", side_effect=claude_side),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            result = run_flow(path, base_dir=tmp_path)

        assert result.results["result"] == "claude result"
        assert call_count[0] == 1

    def test_profile_shape_escalation(self, tmp_path):
        """retry_escalation.profile resolves at load time; escalated retry uses codex."""
        path = _write_yaml(tmp_path, _PROFILE_ESCALATION)
        codex_calls: list = []

        def codex_side(args, **kwargs):
            codex_calls.append(args)
            return SUCCESS_CODEX

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", side_effect=codex_side),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            result = run_flow(path, base_dir=tmp_path)

        assert len(codex_calls) >= 1, "escalated codex provider was never called"
        assert result.results["result"] == "escalated result"

    def test_system_task_never_escalates(self, tmp_path):
        """Tasks with provider:system are not escalated regardless of retry_escalation."""
        path = _write_yaml(tmp_path, _SYSTEM_TASK_WITH_ESCALATION)
        codex_calls: list = []

        def codex_side(args, **kwargs):
            codex_calls.append(args)
            return SUCCESS_CODEX

        with (
            patch("fdsx.providers.codex._run_subprocess", side_effect=codex_side),
            pytest.raises(RuntimeError),
        ):
            run_flow(path, base_dir=tmp_path)

        assert len(codex_calls) == 0, "codex should never be called for a system task"


class TestRetryEscalationParallel:
    def test_parallel_branches_escalate_independently(self, tmp_path):
        """Each parallel branch independently escalates on first failure."""
        path = _write_yaml(tmp_path, _PARALLEL_WITH_ESCALATION)
        codex_calls: list = []

        def codex_side(args, **kwargs):
            codex_calls.append(args)
            return SUCCESS_CODEX

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", side_effect=codex_side),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            run_flow(path, base_dir=tmp_path)

        assert len(codex_calls) >= 2, (
            f"expected codex called for both branches, got {len(codex_calls)} calls"
        )


class TestRetryEscalationMap:
    def test_map_iterator_per_item_escalation(self, tmp_path):
        """Map iterator escalates on each item's retry independently."""
        path = _write_yaml(tmp_path, _MAP_WITH_ESCALATION)
        codex_calls: list = []

        def codex_side(args, **kwargs):
            codex_calls.append(args)
            return SUCCESS_CODEX

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", side_effect=codex_side),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            result = run_flow(
                path,
                base_dir=tmp_path,
            )

        assert len(codex_calls) >= 2, (
            f"expected codex called for each map item, got {len(codex_calls)} calls"
        )
        assert result.results.get("results") is not None


class TestRetryEscalationRecorder:
    def test_run_json_contains_escalation_fields(self, tmp_path):
        """After escalation fires, run.json state entry has escalation_activated, provider, model."""
        path = _write_yaml(tmp_path, _CLAUDE_TASK_WITH_ESCALATION)
        thread_id = "test-escalation-runlog"

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", return_value=SUCCESS_CODEX),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        run_json_path = tmp_path / RUNS_DIR_NAME / thread_id / "run.json"
        assert run_json_path.exists(), "run.json was not created"
        data = json.loads(run_json_path.read_text())

        step1_entry = next(
            (s for s in data.get("states", []) if s.get("name") == "step1"),
            None,
        )
        assert step1_entry is not None, "step1 state not found in run.json"
        assert step1_entry.get("escalation_activated") is True, (
            f"escalation_activated not set in run.json state: {step1_entry}"
        )
        assert step1_entry.get("escalation_provider") == "codex"
        assert step1_entry.get("escalation_model") == "gpt-4o"

    def test_record_state_escalation_is_idempotent(self, tmp_path):
        """Calling record_state_escalation twice for the same state is idempotent."""
        recorder = RunRecorder(thread_id="test-idempotent", flow_name="test")
        recorder.record_state_start("step1", "task")

        recorder.record_state_escalation("step1", "codex", "gpt-4o")
        recorder.record_state_escalation("step1", "codex", "gpt-4o")

        state = recorder._find_state_by_name("step1")
        assert state is not None
        assert state["escalation_activated"] is True
        assert state["escalation_provider"] == "codex"
        assert state["escalation_model"] == "gpt-4o"

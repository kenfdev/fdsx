"""Integration tests for T004: visible escalation activation during a run.

Verifies that escalation events emit human-readable lines to stderr at all
three call sites: top-level task states, parallel branches, and map iterations.

Mock _run_subprocess per project convention (never invoke real provider binaries).
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.core.engine import run_flow
from fdsx.providers.base import ProviderResult

FAIL = ProviderResult(exit_code=1, stdout="", stderr="provider error")
SUCCESS_CODEX = ProviderResult(exit_code=0, stdout="escalated result", stderr="")
SUCCESS_CLAUDE = ProviderResult(exit_code=0, stdout="claude result", stderr="")


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "flow.yaml"
    p.write_text(content)
    return p


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
description: Task without retry_escalation
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

_CLAUDE_TASK_SUCCEEDS_FIRST_ATTEMPT = """\
name: success-test
description: Task that succeeds immediately (no escalation)
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


class TestTopLevelTaskEscalationVisibility:
    def test_escalated_task_prints_escalation_line_to_stderr(
        self, tmp_path, monkeypatch, capsys
    ):
        """Top-level task that fails once then escalates: stderr contains '↑ step1 escalated to codex/gpt-4o'."""
        monkeypatch.chdir(tmp_path)
        path = _write_yaml(tmp_path, _CLAUDE_TASK_WITH_ESCALATION)

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", return_value=SUCCESS_CODEX),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            run_flow(path, base_dir=tmp_path)

        err = capsys.readouterr().err
        assert "↑" in err, f"escalation arrow missing in stderr: {err!r}"
        assert "step1" in err, f"'step1' missing in stderr: {err!r}"
        assert "escalated to" in err, f"'escalated to' missing in stderr: {err!r}"
        assert "codex" in err, f"'codex' missing in stderr: {err!r}"
        assert "gpt-4o" in err, f"'gpt-4o' missing in stderr: {err!r}"

    def test_task_without_escalation_config_no_escalation_line(
        self, tmp_path, monkeypatch, capsys
    ):
        """Task with no retry_escalation declared: no '↑ escalated' line in stderr."""
        monkeypatch.chdir(tmp_path)
        path = _write_yaml(tmp_path, _CLAUDE_TASK_NO_ESCALATION)

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=SUCCESS_CLAUDE),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            run_flow(path, base_dir=tmp_path)

        err = capsys.readouterr().err
        assert "↑ escalated" not in err, (
            f"unexpected escalation line in stderr: {err!r}"
        )

    def test_task_succeeds_first_attempt_no_escalation_line(
        self, tmp_path, monkeypatch, capsys
    ):
        """Task that succeeds on attempt 0 never emits an escalation line."""
        monkeypatch.chdir(tmp_path)
        path = _write_yaml(tmp_path, _CLAUDE_TASK_SUCCEEDS_FIRST_ATTEMPT)

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=SUCCESS_CLAUDE),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            run_flow(path, base_dir=tmp_path)

        err = capsys.readouterr().err
        assert "↑ escalated" not in err, (
            f"unexpected escalation line in stderr: {err!r}"
        )

    def test_escalation_line_fires_exactly_once_when_escalated_provider_also_fails(
        self, tmp_path, monkeypatch, capsys
    ):
        """When the escalated provider retries and fails further, the escalation line appears exactly once."""
        monkeypatch.chdir(tmp_path)
        path = _write_yaml(tmp_path, _CLAUDE_TASK_WITH_ESCALATION)

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", return_value=SUCCESS_CODEX),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            run_flow(path, base_dir=tmp_path)

        err = capsys.readouterr().err
        escalation_count = err.count("↑")
        assert escalation_count == 1, (
            f"expected exactly 1 escalation line, got {escalation_count}: {err!r}"
        )

    def test_system_task_with_escalation_never_emits_escalation_line(
        self, tmp_path, monkeypatch, capsys
    ):
        """system provider tasks with retry_escalation declared never emit an escalation line."""
        monkeypatch.chdir(tmp_path)
        path = _write_yaml(tmp_path, _SYSTEM_TASK_WITH_ESCALATION)

        with pytest.raises(RuntimeError):
            run_flow(path, base_dir=tmp_path)

        err = capsys.readouterr().err
        assert "↑ escalated" not in err, (
            f"unexpected escalation line for system task: {err!r}"
        )

    def test_quiet_mode_still_shows_escalation_line(
        self, tmp_path, monkeypatch, capsys
    ):
        """--quiet mode: escalation lines still appear (printed unconditionally)."""
        monkeypatch.chdir(tmp_path)
        path = _write_yaml(tmp_path, _CLAUDE_TASK_WITH_ESCALATION)

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", return_value=SUCCESS_CODEX),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            run_flow(path, base_dir=tmp_path, quiet=True)

        err = capsys.readouterr().err
        assert "↑" in err, f"escalation line missing in quiet mode stderr: {err!r}"
        assert "escalated to" in err


class TestParallelBranchEscalationVisibility:
    def test_parallel_two_branches_each_emit_escalation_line(
        self, tmp_path, monkeypatch, capsys
    ):
        """Parallel state with 2 branches that both escalate: stderr has [branch-1] and [branch-2] lines."""
        monkeypatch.chdir(tmp_path)
        path = _write_yaml(tmp_path, _PARALLEL_WITH_ESCALATION)

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", return_value=SUCCESS_CODEX),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            run_flow(path, base_dir=tmp_path)

        err = capsys.readouterr().err
        assert "[branch-1]" in err and "↑ escalated" in err, (
            f"[branch-1] escalation line missing: {err!r}"
        )
        assert "[branch-2]" in err, f"[branch-2] escalation line missing: {err!r}"


class TestMapIterationEscalationVisibility:
    def test_map_two_iterations_each_emit_escalation_line(
        self, tmp_path, monkeypatch, capsys
    ):
        """Map state with 2 items that both escalate: stderr has [iter-1/2] and [iter-2/2] lines."""
        monkeypatch.chdir(tmp_path)
        path = _write_yaml(tmp_path, _MAP_WITH_ESCALATION)

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", return_value=SUCCESS_CODEX),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            run_flow(path, base_dir=tmp_path)

        err = capsys.readouterr().err
        assert "[iter-1/2]" in err and "↑ escalated" in err, (
            f"[iter-1/2] escalation line missing: {err!r}"
        )
        assert "[iter-2/2]" in err, f"[iter-2/2] escalation line missing: {err!r}"

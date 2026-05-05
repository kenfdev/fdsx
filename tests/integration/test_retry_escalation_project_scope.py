"""Integration tests for project-scope retry escalation override (T003).

Behaviors tested:
- Project-only config escalation is used when flow has no retry_escalation.
- Manually merged FdsxConfig (global=claude/opus + project=codex/gpt-4o, no leaked
  provider_options) with a failing claude task: codex is called, opencode is not.
- Same merged config, flow declares retry_escalation: opencode/gpt-4o → opencode called.
- Flow declares retry_escalation: False, merged config has codex → RuntimeError, no codex call.
- End-to-end through load_config with YAML files: global block has provider_options,
  project block has only provider+model; FdsxConfig.retry_escalation.provider_options is None.

The load_config field-leakage test (behavior 5) fails until _FULL_REPLACE_KEYS is added
to _deep_merge — that is the primary RED signal for this phase.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

from fdsx.core.compiler import compile_flow
from fdsx.core.config import FdsxConfig, load_config
from fdsx.models.flow import EscalationConfig, Flow, TaskState
from fdsx.providers.base import ProviderResult

FAIL = ProviderResult(exit_code=1, stdout="", stderr="provider error")
SUCCESS_CLAUDE = ProviderResult(exit_code=0, stdout="claude result", stderr="")
SUCCESS_CODEX = ProviderResult(exit_code=0, stdout="codex result", stderr="")
SUCCESS_OPENCODE = ProviderResult(exit_code=0, stdout="opencode result", stderr="")


def _claude_task_flow(**flow_kwargs: object) -> Flow:
    return Flow(
        name="project-scope-escalation-test",
        description="Test project-scope retry escalation",
        start_at="step1",
        states={
            "step1": TaskState(
                type="task",
                provider="claude",
                model="claude-sonnet-4-6",
                prompt_template="do the thing",
                result_path="$.result",
                retry=1,
                end=True,
            )
        },
        **flow_kwargs,
    )


class TestProjectScopeEscalation:
    def test_project_only_config_escalation_fires_on_failure(self):
        """Project-only config with codex escalation: failing claude call escalates to codex."""
        flow = _claude_task_flow()
        config = FdsxConfig(
            retry_escalation=EscalationConfig(provider="codex", model="gpt-4o")
        )

        codex_calls: list = []

        def codex_side(args, **kwargs):
            codex_calls.append(args)
            return SUCCESS_CODEX

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", side_effect=codex_side),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            compiled = compile_flow(flow, config=config)
            compiled.graph.invoke({})

        assert len(codex_calls) >= 1, (
            "project-scope escalation provider (codex) was never called"
        )

    def test_merged_config_no_leaked_provider_options_escalates_to_codex(self):
        """Merged FdsxConfig (global=claude, project=codex, no leaked provider_options): codex called."""
        flow = _claude_task_flow()
        config = FdsxConfig(
            retry_escalation=EscalationConfig(
                provider="codex",
                model="gpt-4o",
                provider_options=None,
            )
        )

        codex_calls: list = []
        opencode_calls: list = []

        def codex_side(args, **kwargs):
            codex_calls.append(args)
            return SUCCESS_CODEX

        def opencode_side(args, **kwargs):
            opencode_calls.append(args)
            return SUCCESS_OPENCODE

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess", side_effect=codex_side),
            patch("fdsx.providers.opencode._run_subprocess", side_effect=opencode_side),
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            compiled = compile_flow(flow, config=config)
            compiled.graph.invoke({})

        assert len(codex_calls) >= 1, "codex should be called"
        assert len(opencode_calls) == 0, "opencode should not be called"

    def test_flow_level_escalation_overrides_merged_config(self):
        """flow.retry_escalation=opencode takes precedence over config.retry_escalation=codex."""
        flow = _claude_task_flow(
            retry_escalation=EscalationConfig(provider="opencode", model="gpt-4o")
        )
        config = FdsxConfig(
            retry_escalation=EscalationConfig(provider="codex", model="gpt-4o")
        )

        opencode_calls: list = []

        def opencode_side(args, **kwargs):
            opencode_calls.append(args)
            return SUCCESS_OPENCODE

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.opencode._run_subprocess", side_effect=opencode_side),
            patch("fdsx.providers.codex._run_subprocess") as mock_codex,
            patch("fdsx.core.compiler.execution.time.sleep"),
        ):
            compiled = compile_flow(flow, config=config)
            compiled.graph.invoke({})

        assert len(opencode_calls) >= 1, (
            "opencode should be called (flow-level escalation)"
        )
        mock_codex.assert_not_called()

    def test_false_sentinel_disables_merged_config_escalation(self):
        """flow.retry_escalation=False disables config escalation; RuntimeError surfaces."""
        flow = Flow.model_validate(
            {
                "name": "opt-out-project-scope",
                "description": "opt-out from project-scope config escalation",
                "start_at": "step1",
                "states": {
                    "step1": {
                        "type": "task",
                        "provider": "claude",
                        "model": "claude-sonnet-4-6",
                        "prompt_template": "do the thing",
                        "result_path": "$.result",
                        "retry": 1,
                        "end": True,
                    }
                },
                "retry_escalation": False,
            }
        )
        config = FdsxConfig(
            retry_escalation=EscalationConfig(provider="codex", model="gpt-4o")
        )

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=FAIL),
            patch("fdsx.providers.codex._run_subprocess") as mock_codex,
            patch("fdsx.core.compiler.execution.time.sleep"),
            pytest.raises(RuntimeError),
        ):
            compiled = compile_flow(flow, config=config)
            compiled.graph.invoke({})

        mock_codex.assert_not_called()

    def test_load_config_project_override_excludes_global_provider_options(
        self, tmp_path, monkeypatch
    ):
        """load_config: global block has provider_options; project block omits it — no field leakage."""
        xdg_dir = tmp_path / "xdg"
        fdsx_global_dir = xdg_dir / "fdsx"
        fdsx_global_dir.mkdir(parents=True)
        (fdsx_global_dir / "config.yaml").write_text(
            yaml.dump(
                {
                    "retry_escalation": {
                        "provider": "claude",
                        "model": "claude-opus-4-7",
                        "provider_options": {"permission_mode": "bypassPermissions"},
                    }
                }
            )
        )

        project_dir = tmp_path / "project"
        fdsx_project_dir = project_dir / ".fdsx"
        fdsx_project_dir.mkdir(parents=True)
        (fdsx_project_dir / "config.yaml").write_text(
            yaml.dump(
                {
                    "retry_escalation": {
                        "provider": "codex",
                        "model": "gpt-4o",
                    }
                }
            )
        )

        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
        cfg = load_config(project_dir=project_dir, load_global=True, load_project=True)

        assert cfg.retry_escalation is not None
        assert cfg.retry_escalation.provider == "codex"
        assert cfg.retry_escalation.model == "gpt-4o"
        assert cfg.retry_escalation.provider_options is None, (
            "provider_options from global config must not leak into project-scoped result"
        )

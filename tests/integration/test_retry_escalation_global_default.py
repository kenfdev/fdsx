"""Integration tests for global-config retry escalation default (T002).

Behaviors tested:
- Global config escalation is used when flow has no retry_escalation set.
- Workflow-level retry_escalation overrides the global config escalation.
- flow.retry_escalation=False (opt-out sentinel) disables global escalation.
- No global + no workflow = no escalation (regression guard).
- System tasks are never escalated even when global config has escalation.

All tests fail in RED because FdsxConfig.retry_escalation does not exist yet
(ValidationError: Extra inputs are not permitted) — the expected RED signal.
"""

from unittest.mock import patch

import pytest

from fdsx.core.compiler import compile_flow
from fdsx.core.config import FdsxConfig
from fdsx.models.flow import EscalationConfig, Flow, TaskState
from fdsx.providers.base import ProviderResult

FAIL = ProviderResult(exit_code=1, stdout="", stderr="provider error")
SUCCESS_CLAUDE = ProviderResult(exit_code=0, stdout="claude result", stderr="")
SUCCESS_CODEX = ProviderResult(exit_code=0, stdout="codex result", stderr="")
SUCCESS_OPENCODE = ProviderResult(exit_code=0, stdout="opencode result", stderr="")


def _claude_task_flow(**flow_kwargs: object) -> Flow:
    return Flow(
        name="global-escalation-test",
        description="Test global retry escalation fallback",
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


class TestGlobalConfigEscalation:
    def test_global_config_escalation_used_when_flow_has_none(self):
        """Global escalation fires when flow.retry_escalation is unset."""
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
            "global escalation provider (codex) was never called"
        )

    def test_workflow_escalation_overrides_global(self):
        """flow.retry_escalation takes precedence over FdsxConfig.retry_escalation."""
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
            "workflow escalation provider (opencode) was never called"
        )
        mock_codex.assert_not_called()

    def test_false_sentinel_opts_out_of_global_escalation(self):
        """retry_escalation: false on the flow disables global config escalation."""
        flow = Flow.model_validate(
            {
                "name": "opt-out",
                "description": "opt-out test",
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

    def test_no_global_no_workflow_no_escalation_regression(self):
        """When neither config nor flow has retry_escalation, no escalation fires."""
        flow = _claude_task_flow()
        config = FdsxConfig()

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=SUCCESS_CLAUDE),
            patch("fdsx.providers.codex._run_subprocess") as mock_codex,
        ):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        mock_codex.assert_not_called()
        assert result.get("result") == "claude result"

    def test_system_task_not_escalated_with_global_config(self):
        """System tasks are not escalated even when FdsxConfig.retry_escalation is set."""
        flow = Flow(
            name="system-global-escalation-test",
            description="System task should not escalate even with global config",
            start_at="step1",
            states={
                "step1": TaskState(
                    type="task",
                    provider="system",
                    command="exit 1",
                    result_path="$.result",
                    retry=1,
                    end=True,
                )
            },
        )
        config = FdsxConfig(
            retry_escalation=EscalationConfig(provider="codex", model="gpt-4o")
        )

        with (
            patch("fdsx.providers.codex._run_subprocess") as mock_codex,
            pytest.raises(RuntimeError),
        ):
            compiled = compile_flow(flow, config=config)
            compiled.graph.invoke({})

        mock_codex.assert_not_called()

"""Integration tests for end-to-end provider instruction wiring (T001 + T002).

Tests verify that provider-specific instruction options:
  - Flow from task state provider_options through merge to CLI invocation
  - Enforce Claude's system-prompt mutual exclusion after 3-level merge
  - Reject Claude-only options on Codex with migration guidance
  - Configure Codex developer instructions and multi-agent availability
  - Support variable substitution via {var} patterns
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from fdsx.core.compiler.helpers import _merge_provider_options
from fdsx.core.config import FdsxConfig, ProviderConfigs
from fdsx.core.engine import FlowValidationError, run_flow
from fdsx.models.flow import Flow, TaskState
from fdsx.providers.base import ProviderResult

FAKE_SUCCESS = ProviderResult(exit_code=0, stdout="ok", stderr="")


class TestSystemPromptMutexValidation:
    """Verify mutual-exclusion validation fires after 3-level merge."""

    def test_both_fields_set_after_merge_raises_flow_validation_error(self):
        """Both fields set after merge → FlowValidationError naming both fields and state."""
        config = FdsxConfig(
            providers=ProviderConfigs(
                claude={"system_prompt": "Config-level prompt"},
            )
        )
        flow = Flow(
            name="test",
            description="Test",
            start_at="step1",
            states={
                "step1": TaskState(
                    type="task",
                    provider="claude",
                    model="claude-sonnet",
                    prompt_template="do the thing",
                    result_path="$.result",
                    end=True,
                    provider_options={"append_system_prompt": "Task-level append"},
                )
            },
        )

        with pytest.raises(FlowValidationError) as exc_info:
            _merge_provider_options(
                config,
                flow,
                "claude",
                flow.states["step1"].provider_options,
                state_name="step1",
            )

        exc_message = str(exc_info.value)
        assert "system_prompt" in exc_message
        assert "append_system_prompt" in exc_message
        assert "step1" in exc_message

    def test_task_level_both_fields_set_raises(self):
        """Task-level provider_options with both fields set raises at merge time."""
        flow = Flow(
            name="test",
            description="Test",
            start_at="step1",
            states={
                "step1": TaskState(
                    type="task",
                    provider="claude",
                    model="claude-sonnet",
                    prompt_template="do the thing",
                    result_path="$.result",
                    end=True,
                    provider_options={
                        "system_prompt": "Base",
                        "append_system_prompt": "Append",
                    },
                )
            },
        )

        with pytest.raises(FlowValidationError) as exc_info:
            _merge_provider_options(
                None,
                flow,
                "claude",
                flow.states["step1"].provider_options,
                state_name="step1",
            )

        exc_message = str(exc_info.value)
        assert "system_prompt" in exc_message
        assert "append_system_prompt" in exc_message


class TestClaudeSystemPromptEndToEnd:
    """Verify system_prompt reaches the Claude CLI invocation."""

    def _write_flow_yaml(self, tmp_path: Path, flow_dict: dict) -> Path:
        flow_yaml = yaml.dump(flow_dict)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)
        return flow_path

    def test_system_prompt_reaches_cli(self, tmp_path, monkeypatch):
        """Claude state with system_prompt → mocked _run_subprocess gets --system-prompt."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        flow_dict = {
            "name": "test-system-prompt",
            "description": "Test system prompt flow",
            "start_at": "step1",
            "states": {
                "step1": {
                    "type": "task",
                    "provider": "claude",
                    "model": "claude-sonnet",
                    "prompt_template": "do the thing",
                    "result_path": "$.result",
                    "end": True,
                    "provider_options": {"system_prompt": "You are a reviewer."},
                }
            },
        }
        flow_path = self._write_flow_yaml(tmp_path, flow_dict)

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=fake_run_subprocess
        ):
            run_flow(flow_path, base_dir=tmp_path)

        assert len(captured_args) == 1
        args = captured_args[0]
        assert "--system-prompt" in args
        idx = args.index("--system-prompt")
        assert args[idx + 1] == "You are a reviewer."

    def test_append_system_prompt_reaches_cli(self, tmp_path, monkeypatch):
        """Claude state with append_system_prompt → mocked _run_subprocess gets --append-system-prompt."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        flow_dict = {
            "name": "test-system-prompt",
            "description": "Test system prompt flow",
            "start_at": "step1",
            "states": {
                "step1": {
                    "type": "task",
                    "provider": "claude",
                    "model": "claude-sonnet",
                    "prompt_template": "do the thing",
                    "result_path": "$.result",
                    "end": True,
                    "provider_options": {"append_system_prompt": "Additional context."},
                }
            },
        }
        flow_path = self._write_flow_yaml(tmp_path, flow_dict)

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=fake_run_subprocess
        ):
            run_flow(flow_path, base_dir=tmp_path)

        assert len(captured_args) == 1
        args = captured_args[0]
        assert "--append-system-prompt" in args
        idx = args.index("--append-system-prompt")
        assert args[idx + 1] == "Additional context."


class TestProviderSpecificInstructions:
    """Provider-specific instruction fields are validated and reach their CLI."""

    def test_codex_with_append_system_prompt_fails_with_migration_guidance(
        self, tmp_path, monkeypatch
    ):
        """Codex append_system_prompt fails before execution and names its replacement."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        flow_dict = {
            "name": "test",
            "description": "Test codex strip flow",
            "start_at": "step1",
            "states": {
                "step1": {
                    "type": "task",
                    "provider": "codex",
                    "model": "codex-mini",
                    "prompt_template": "do the thing",
                    "result_path": "$.result",
                    "end": True,
                    "provider_options": {"append_system_prompt": "Some appended text"},
                }
            },
        }
        flow_yaml = yaml.dump(flow_dict)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        with (
            patch("fdsx.providers.codex._run_subprocess") as fake_run,
            pytest.raises(FlowValidationError) as exc_info,
        ):
            run_flow(flow_path, base_dir=tmp_path)

        message = str(exc_info.value)
        assert "append_system_prompt" in message
        assert "developer_instructions" in message
        fake_run.assert_not_called()

    def test_codex_instructions_and_agent_switch_reach_cli(self, tmp_path, monkeypatch):
        """Codex instructions resolve variables and disable multi-agent via -c."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        flow_dict = {
            "name": "test-codex-instructions",
            "description": "Test Codex developer instruction wiring",
            "start_at": "review",
            "states": {
                "review": {
                    "type": "task",
                    "provider": "codex",
                    "model": "gpt-5.6",
                    "prompt_template": "review the change",
                    "result_path": "$.result",
                    "end": True,
                    "provider_options": {
                        "developer_instructions": (
                            "Reviewer: {reviewer_name}\nDo not delegate."
                        ),
                        "agents_enabled": False,
                    },
                }
            },
        }
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(yaml.dump(flow_dict))

        with patch(
            "fdsx.providers.codex._run_subprocess", return_value=FAKE_SUCCESS
        ) as fake_run:
            run_flow(
                flow_path,
                inputs={"reviewer_name": "Alice"},
                base_dir=tmp_path,
            )

        args = fake_run.call_args.kwargs["args"]
        config_overrides = [
            args[index + 1] for index, value in enumerate(args) if value == "-c"
        ]
        assert (
            'developer_instructions="Reviewer: Alice\\nDo not delegate."'
            in config_overrides
        )
        assert "agents.enabled=false" in config_overrides

    def test_parallel_codex_developer_instructions_resolve_variables(
        self, tmp_path, monkeypatch
    ):
        """Parallel Codex branches resolve developer instruction variables."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        flow_dict = {
            "name": "parallel-codex-instructions",
            "description": "Test parallel Codex developer instructions",
            "start_at": "review",
            "states": {
                "review": {
                    "type": "parallel",
                    "branches": [
                        {
                            "provider": "codex",
                            "model": "gpt-5.6",
                            "prompt_template": "review",
                            "provider_options": {
                                "developer_instructions": ("Reviewer: {reviewer_name}"),
                            },
                        }
                    ],
                    "result_path": "$.reviews",
                    "end": True,
                }
            },
        }
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(yaml.dump(flow_dict))

        with patch(
            "fdsx.providers.codex._run_subprocess", return_value=FAKE_SUCCESS
        ) as fake_run:
            run_flow(
                flow_path,
                inputs={"reviewer_name": "Alice"},
                base_dir=tmp_path,
            )

        args = fake_run.call_args.kwargs["args"]
        config_overrides = [
            args[index + 1] for index, value in enumerate(args) if value == "-c"
        ]
        assert 'developer_instructions="Reviewer: Alice"' in config_overrides

    def test_map_codex_developer_instructions_resolve_item_variables(
        self, tmp_path, monkeypatch
    ):
        """Map Codex iterator tasks resolve item variables in instructions."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        flow_dict = {
            "name": "map-codex-instructions",
            "description": "Test map Codex developer instructions",
            "start_at": "review_items",
            "states": {
                "review_items": {
                    "type": "map",
                    "items_path": "$.items",
                    "iterator": {
                        "states": [
                            {
                                "type": "task",
                                "name": "review",
                                "provider": "codex",
                                "model": "gpt-5.6",
                                "prompt_template": "review {item.name}",
                                "result_path": "$.review",
                                "provider_options": {
                                    "developer_instructions": ("Reviewer: {item.name}"),
                                },
                            }
                        ]
                    },
                    "result_path": "$.reviews",
                    "end": True,
                }
            },
        }
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(yaml.dump(flow_dict))

        with patch(
            "fdsx.providers.codex._run_subprocess", return_value=FAKE_SUCCESS
        ) as fake_run:
            run_flow(
                flow_path,
                inputs={"items": [{"name": "Alice"}]},  # type: ignore[dict-item]
                base_dir=tmp_path,
            )

        args = fake_run.call_args.kwargs["args"]
        config_overrides = [
            args[index + 1] for index, value in enumerate(args) if value == "-c"
        ]
        assert 'developer_instructions="Reviewer: Alice"' in config_overrides


class TestSystemPromptVariableSubstitution:
    """System prompt fields support {variable} substitution from state_dict."""

    def test_append_system_prompt_with_variable_resolved(self, tmp_path, monkeypatch):
        """append_system_prompt: 'Reviewer: {reviewer_name}' resolves to actual value."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()

        flow_dict = {
            "name": "test-var-subst",
            "description": "Test variable substitution flow",
            "start_at": "step1",
            "states": {
                "step1": {
                    "type": "task",
                    "provider": "claude",
                    "model": "claude-sonnet",
                    "prompt_template": "do the thing",
                    "result_path": "$.result",
                    "end": True,
                    "provider_options": {
                        "append_system_prompt": "Reviewer: {reviewer_name}"
                    },
                }
            },
        }
        flow_yaml = yaml.dump(flow_dict)
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(flow_yaml)

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=fake_run_subprocess
        ):
            run_flow(flow_path, inputs={"reviewer_name": "Alice"}, base_dir=tmp_path)

        assert len(captured_args) == 1
        args = captured_args[0]
        assert "--append-system-prompt" in args
        idx = args.index("--append-system-prompt")
        assert args[idx + 1] == "Reviewer: Alice"

    def test_config_level_system_prompt_reaches_cli(self, tmp_path, monkeypatch):
        """Config-level system_prompt with no conflict reaches Claude CLI argv."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".fdsx").mkdir()
        config_path = tmp_path / ".fdsx" / "config.yaml"
        config_path.write_text(
            yaml.dump({"providers": {"claude": {"system_prompt": "You are helpful."}}})
        )
        flow_dict = {
            "name": "test",
            "description": "Test",
            "start_at": "step1",
            "states": {
                "step1": {
                    "type": "task",
                    "provider": "claude",
                    "model": "claude-sonnet",
                    "prompt_template": "do it",
                    "result_path": "$.result",
                    "end": True,
                }
            },
        }
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(yaml.dump(flow_dict))
        captured_args = []

        def fake(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch("fdsx.providers.claude._run_subprocess", side_effect=fake):
            run_flow(flow_path, base_dir=tmp_path / ".fdsx")
        args = captured_args[0]
        assert "--system-prompt" in args
        idx = args.index("--system-prompt")
        assert args[idx + 1] == "You are helpful."

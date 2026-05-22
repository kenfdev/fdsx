"""Validation: on_wait_start / on_wait_end rejected on non-wait states, accepted on wait states."""

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from fdsx.core.loader import load_flow
from fdsx.models.flow import StateHookConfig

_WAIT_STATE_YAML = textwrap.dedent("""\
    name: WaitHookValidationTest
    description: wait hooks validation test
    start_at: await_approval
    states:
      await_approval:
        type: wait
        message: "Approve the request?"
        choices:
          - approve
          - reject
        result_path: $.approval
        hooks:
          {hook_key}:
            - command: echo hook fired
        end: true
""")


class TestWaitHookRejectedOnStateHookConfig:
    """StateHookConfig (used by task/choice/parallel/map/fail states) must reject wait hook keys."""

    @pytest.mark.parametrize("hook_key", ["on_wait_start", "on_wait_end"])
    def test_state_hook_config_rejects_wait_hook_keys(self, hook_key: str) -> None:
        with pytest.raises(ValidationError):
            StateHookConfig.model_validate({hook_key: [{"command": "echo bad"}]})

    def test_on_wait_start_rejected_on_task_state_via_load_flow(
        self, tmp_path: Path
    ) -> None:
        yaml_text = textwrap.dedent("""\
            name: TestFlow
            description: test
            start_at: s1
            states:
              s1:
                type: task
                provider: system
                command: echo hi
                hooks:
                  on_wait_start:
                    - command: echo bad
                end: true
        """)
        path = tmp_path / "flow.yaml"
        path.write_text(yaml_text)
        _, errors = load_flow(path)
        assert errors, "Expected a validation error for on_wait_start on a task state"

    def test_on_wait_end_rejected_on_task_state_via_load_flow(
        self, tmp_path: Path
    ) -> None:
        yaml_text = textwrap.dedent("""\
            name: TestFlow
            description: test
            start_at: s1
            states:
              s1:
                type: task
                provider: system
                command: echo hi
                hooks:
                  on_wait_end:
                    - command: echo bad
                end: true
        """)
        path = tmp_path / "flow.yaml"
        path.write_text(yaml_text)
        _, errors = load_flow(path)
        assert errors, "Expected a validation error for on_wait_end on a task state"

    def test_on_wait_start_rejected_on_fail_state_via_load_flow(
        self, tmp_path: Path
    ) -> None:
        yaml_text = textwrap.dedent("""\
            name: TestFlow
            description: test
            start_at: s1
            states:
              s1:
                type: fail
                error: TestError
                cause: something broke
                hooks:
                  on_wait_start:
                    - command: echo bad
        """)
        path = tmp_path / "flow.yaml"
        path.write_text(yaml_text)
        _, errors = load_flow(path)
        assert errors, "Expected a validation error for on_wait_start on a fail state"


class TestWaitHookAcceptedOnWaitState:
    """on_wait_start and on_wait_end must be valid fields on a wait state."""

    def test_on_wait_start_accepted_on_wait_state(self, tmp_path: Path) -> None:
        path = tmp_path / "flow.yaml"
        path.write_text(_WAIT_STATE_YAML.format(hook_key="on_wait_start"))
        flow, errors = load_flow(path)
        assert not errors, f"Unexpected validation errors: {errors}"
        assert flow is not None
        hooks = flow.states["await_approval"].hooks
        assert hooks is not None
        # WaitStateHookConfig must expose on_wait_start; fails until WaitStateHookConfig is added
        hook_list = getattr(hooks, "on_wait_start", None)
        assert hook_list is not None, (
            "hooks.on_wait_start is None — WaitStateHookConfig not yet implemented"
        )
        assert len(hook_list) == 1

    def test_on_wait_end_accepted_on_wait_state(self, tmp_path: Path) -> None:
        path = tmp_path / "flow.yaml"
        path.write_text(_WAIT_STATE_YAML.format(hook_key="on_wait_end"))
        flow, errors = load_flow(path)
        assert not errors, f"Unexpected validation errors: {errors}"
        assert flow is not None
        hooks = flow.states["await_approval"].hooks
        assert hooks is not None
        hook_list = getattr(hooks, "on_wait_end", None)
        assert hook_list is not None, (
            "hooks.on_wait_end is None — WaitStateHookConfig not yet implemented"
        )
        assert len(hook_list) == 1

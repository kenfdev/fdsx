"""Integration tests for preserving workflow inputs across checkpoint resume."""

from unittest.mock import patch

import pytest
import yaml

from fdsx.core.engine import resume_flow, run_flow
from fdsx.providers.base import ProviderResult


@pytest.mark.parametrize(
    ("inputs", "prompt_template", "expected_prompt"),
    [
        (
            {"task": "review task", "source": "/tmp/source.md"},
            "{task}|{source}",
            "review task|/tmp/source.md",
        ),
        ({"context": "custom input"}, "{context}", "custom input"),
    ],
)
def test_resume_parallel_branch_preserves_inputs(
    tmp_path, inputs, prompt_template, expected_prompt
):
    """Workflow inputs remain available after checkpoint resume."""
    flow_path = tmp_path / "flow.yaml"
    flow_path.write_text(
        yaml.safe_dump(
            {
                "name": "Resume built-in inputs",
                "description": "Preserve task inputs across resume",
                "start_at": "approval",
                "states": {
                    "approval": {
                        "type": "wait",
                        "mode": "prompt",
                        "message": "Continue?",
                        "choices": ["yes"],
                        "result_path": "$.approval",
                        "next": "review",
                    },
                    "review": {
                        "type": "parallel",
                        "branches": [
                            {
                                "provider": "claude",
                                "model": "test-model",
                                "prompt_template": prompt_template,
                                "retry": 0,
                            }
                        ],
                        "result_path": "$.reviews",
                        "end": True,
                    },
                },
            }
        )
    )
    base_dir = tmp_path / ".fdsx"
    thread_id = "resume-built-in-inputs"

    with (
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=RuntimeError("simulated crash"),
        ),
        pytest.raises(RuntimeError, match="Flow execution failed"),
    ):
        run_flow(
            flow_path,
            inputs=inputs,
            thread_id=thread_id,
            base_dir=base_dir,
        )

    captured_args: list[list[str]] = []

    def fake_run_subprocess(args, **kwargs):
        captured_args.append(list(args))
        return ProviderResult(exit_code=0, stdout="APPROVED", stderr="")

    with (
        patch("builtins.input", return_value="1"),
        patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=fake_run_subprocess,
        ),
    ):
        resume_flow(thread_id, base_dir=base_dir, flow_path=flow_path)

    assert expected_prompt in captured_args[0]

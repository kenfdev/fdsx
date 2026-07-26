from pathlib import Path
from unittest.mock import patch

from fdsx.core.engine import run_flow
from fdsx.providers.base import ProviderResult


def test_repeated_state_prompt_receives_one_based_current_iteration(
    tmp_path: Path,
) -> None:
    flow_path = tmp_path / "workflow.yaml"
    flow_path.write_text(
        """
name: state-iteration
description: Expose current state entry count
max_loop: 3
start_at: generate
states:
  generate:
    type: task
    provider: claude
    model: test-model
    prompt_template: "iteration={state.iteration}"
    result_path: $.value
    next: repeat
  repeat:
    type: choice
    choices:
      - variable: $.value
        operator: equals
        value: done
        next: finished
    default: generate
  finished:
    type: pass
    end: true
""".lstrip()
    )
    outputs = [
        ProviderResult(exit_code=0, stdout="again", stderr=""),
        ProviderResult(exit_code=0, stdout="done", stderr=""),
    ]
    prompts: list[str] = []

    def fake_run(args: list[str], **_: object) -> ProviderResult:
        prompts.append(args[args.index("-p") + 1])
        return outputs[len(prompts) - 1]

    with patch("fdsx.providers.claude._run_subprocess", side_effect=fake_run):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert prompts == ["iteration=1", "iteration=2"]

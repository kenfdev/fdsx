from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.core.engine import run_flow
from fdsx.core.engine.validate import FlowValidationError
from fdsx.providers.base import ProviderResult


def _write_merge_workflow(tmp_path: Path) -> Path:
    (tmp_path / "ledger.schema.json").write_text(
        """
{
  "type": "array",
  "items": {
    "type": "object",
    "required": ["status"],
    "properties": {
      "id": {"type": "string"},
      "status": {"type": "string"}
    }
  }
}
""".strip()
    )
    path = tmp_path / "workflow.yaml"
    path.write_text(
        """
name: merge-ledger
description: Maintain a keyed ledger across repeated state execution
max_loop: 5
start_at: update
states:
  update:
    type: task
    provider: claude
    model: test-model
    prompt_template: Update ledger
    retry: 0
    structured_output:
      schema: ledger.schema.json
      result_path: $.ledger
      merge:
        strategy: upsert
        key: id
    next: converged
  converged:
    type: choice
    choices:
      - variable: $.ledger[0].status
        operator: equals
        value: closed
        next: done
    default: update
  done:
    type: pass
    end: true
""".lstrip()
    )
    return path


def test_repeated_structured_lists_upsert_by_key_without_dropping_items(
    tmp_path: Path,
) -> None:
    flow_path = _write_merge_workflow(tmp_path)
    outputs = [
        ProviderResult(
            exit_code=0,
            stdout='[{"id":"a","status":"open"},{"id":"b","status":"open"}]',
            stderr="",
        ),
        ProviderResult(
            exit_code=0,
            stdout='[{"id":"a","status":"closed"},{"id":"c","status":"open"}]',
            stderr="",
        ),
    ]

    with patch("fdsx.providers.claude._run_subprocess", side_effect=outputs):
        result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results["ledger"] == [
        {"id": "a", "status": "closed"},
        {"id": "b", "status": "open"},
        {"id": "c", "status": "open"},
    ]


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ('[{"status":"open"}]', "missing merge key 'id'"),
        (
            '[{"id":"a","status":"open"},{"id":"a","status":"closed"}]',
            "duplicate merge key 'a'",
        ),
    ],
)
def test_invalid_upsert_batch_is_rejected(
    tmp_path: Path, output: str, message: str
) -> None:
    flow_path = _write_merge_workflow(tmp_path)
    fake = ProviderResult(exit_code=0, stdout=output, stderr="")

    with (
        patch("fdsx.providers.claude._run_subprocess", return_value=fake),
        pytest.raises(RuntimeError, match=message),
    ):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)


def test_merge_result_path_must_be_top_level(tmp_path: Path) -> None:
    flow_path = _write_merge_workflow(tmp_path)
    flow_path.write_text(
        flow_path.read_text().replace(
            "result_path: $.ledger", "result_path: $.review.ledger"
        )
    )

    with pytest.raises(FlowValidationError, match="single top-level state key"):
        run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)


def test_repeated_parallel_branch_merges_its_prior_structured_list(
    tmp_path: Path,
) -> None:
    (tmp_path / "ledger.schema.json").write_text(
        """
{
  "type": "array",
  "items": {
    "type": "object",
    "required": ["id", "status"],
    "properties": {
      "id": {"type": "string"},
      "status": {"type": "string"}
    }
  }
}
""".strip()
    )
    flow_path = tmp_path / "parallel.yaml"
    flow_path.write_text(
        """
name: parallel-merge
description: Preserve a branch-local ledger across parallel iterations
max_loop: 3
start_at: review
states:
  review:
    type: parallel
    branches:
      - name: reviewer
        provider: system
        command: >-
          if [ {state.iteration} -eq 1 ];
          then printf '[{"id":"a","status":"open"},{"id":"b","status":"open"}]';
          else printf '[{"id":"a","status":"closed"}]';
          fi
        structured_output:
          schema: ledger.schema.json
          result_path: $.ledger
          merge:
            strategy: upsert
            key: id
    result_path: $.reviews
    next: converged
  converged:
    type: choice
    choices:
      - variable: $.reviews[0].ledger[0].status
        operator: equals
        value: closed
        next: done
    default: review
  done:
    type: pass
    end: true
""".lstrip()
    )

    result = run_flow(flow_path, base_dir=tmp_path / ".fdsx", quiet=True)

    assert result.results["reviews"][0]["ledger"] == [
        {"id": "a", "status": "closed"},
        {"id": "b", "status": "open"},
    ]

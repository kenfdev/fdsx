from pathlib import Path

import pytest

from fdsx.core.engine import run_flow
from fdsx.core.engine.validate import FlowValidationError


def _write_gate_flow(
    tmp_path: Path,
    *,
    security_value: bool = True,
    advisory_command: str = """printf '{"approved":false}'""",
) -> Path:
    (tmp_path / "review.schema.json").write_text(
        """
{
  "type": "object",
  "required": ["approved"],
  "properties": {"approved": {"type": "boolean"}}
}
""".strip()
    )
    path = tmp_path / "workflow.yaml"
    path.write_text(
        f"""
name: required-gate
description: Gate on selected named branches
start_at: review
states:
  review:
    type: parallel
    branches:
      - name: security
        provider: system
        retry: 0
        command: "printf '{{\\"approved\\":{str(security_value).lower()}}}'"
        structured_output:
          schema: review.schema.json
          result_path: $.review
      - name: style
        provider: system
        retry: 0
        command: "{advisory_command.replace('"', chr(92) + chr(34))}"
        structured_output:
          schema: review.schema.json
          result_path: $.review
    result_path: $.reviews
    gate:
      required: [security]
      field: $.review.approved
      expected: true
      result_path: $.approved
    next: route
  route:
    type: choice
    choices:
      - variable: $.approved
        operator: equals
        value: true
        next: accepted
    default: rejected
  accepted:
    type: pass
    parameters:
      $.outcome: accepted
    end: true
  rejected:
    type: pass
    parameters:
      $.outcome: rejected
    end: true
""".lstrip()
    )
    return path


def test_required_branch_approval_sets_boolean_gate_and_routes(
    tmp_path: Path,
) -> None:
    result = run_flow(
        _write_gate_flow(tmp_path), base_dir=tmp_path / ".fdsx", quiet=True
    )

    assert result.results["approved"] is True
    assert result.results["outcome"] == "accepted"
    assert [review["name"] for review in result.results["reviews"]] == [
        "security",
        "style",
    ]


def test_required_branch_rejection_sets_false_gate(tmp_path: Path) -> None:
    result = run_flow(
        _write_gate_flow(tmp_path, security_value=False),
        base_dir=tmp_path / ".fdsx",
        quiet=True,
    )

    assert result.results["approved"] is False
    assert result.results["outcome"] == "rejected"


def test_advisory_execution_failure_is_retained_without_blocking_gate(
    tmp_path: Path,
) -> None:
    result = run_flow(
        _write_gate_flow(tmp_path, advisory_command="exit 7"),
        base_dir=tmp_path / ".fdsx",
        quiet=True,
    )

    assert result.results["approved"] is True
    assert result.results["reviews"][1]["name"] == "style"
    assert result.results["reviews"][1]["exit_code"] == 7


def test_required_execution_failure_fails_parallel_state(tmp_path: Path) -> None:
    path = _write_gate_flow(tmp_path)
    path.write_text(
        path.read_text().replace(
            """command: "printf '{\\"approved\\":true}'" """.strip(),
            'command: "exit 9"',
            1,
        )
    )

    with pytest.raises(RuntimeError, match="required branch 'security' failed"):
        run_flow(path, base_dir=tmp_path / ".fdsx", quiet=True)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (
            lambda text: text.replace("name: style", "name: security"),
            "branch names must be unique",
        ),
        (
            lambda text: text.replace("required: [security]", "required: [missing]"),
            "unknown branch",
        ),
        (
            lambda text: text.replace(
                "result_path: $.reviews\n    gate:",
                "result_path: $.reviews\n    min_success: 1\n    gate:",
            ),
            "gate and min_success are mutually exclusive",
        ),
    ],
)
def test_invalid_gate_configuration_is_rejected_before_execution(
    tmp_path: Path, edit: object, message: str
) -> None:
    path = _write_gate_flow(tmp_path)
    transform = edit
    assert callable(transform)
    path.write_text(transform(path.read_text()))

    with pytest.raises(FlowValidationError, match=message):
        run_flow(path, base_dir=tmp_path / ".fdsx", quiet=True)

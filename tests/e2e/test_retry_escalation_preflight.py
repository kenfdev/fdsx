"""E2E pre-flight rejection tests for retry_escalation misconfiguration (T001).

Tests 1-5 expect exit code 2 (validation failure). They fail until
EscalationConfig is added to Flow and load_flow propagates its errors.
Test 6 (happy path) is a regression guard and should pass throughout.
"""

from pathlib import Path

import pytest

from tests.e2e.cli_test_utils import run_fdsx

_TASK = """\
states:
  step1:
    type: task
    provider: system
    command: echo done
    result_path: $.result
    end: true
"""


def _yaml(retry_escalation_block: str, extra_top: str = "") -> str:
    return (
        "name: preflight-test\n"
        "description: Pre-flight validation test\n"
        "start_at: step1\n"
        f"{extra_top}" + _TASK + f"retry_escalation:\n{retry_escalation_block}\n"
    )


@pytest.fixture()
def tmp_cwd(tmp_path: Path) -> Path:
    (tmp_path / ".fdsx").mkdir()
    return tmp_path


class TestRetryEscalationPreflightRejection:
    def test_both_profile_and_provider_exits_code_2(self, tmp_cwd):
        """YAML with both profile and provider set must be rejected at load time."""
        content = _yaml("  profile: p\n  provider: claude\n  model: claude-3-haiku\n")
        (tmp_cwd / "flow.yaml").write_text(content)
        result = run_fdsx(["run", str(tmp_cwd / "flow.yaml")], cwd=tmp_cwd)
        assert result.returncode == 2, (
            f"expected exit code 2, got {result.returncode}\nstderr: {result.stderr}"
        )

    def test_provider_without_model_exits_code_2(self, tmp_cwd):
        """YAML with provider but no model must be rejected."""
        content = _yaml("  provider: claude\n")
        (tmp_cwd / "flow.yaml").write_text(content)
        result = run_fdsx(["run", str(tmp_cwd / "flow.yaml")], cwd=tmp_cwd)
        assert result.returncode == 2, (
            f"expected exit code 2, got {result.returncode}\nstderr: {result.stderr}"
        )

    def test_unknown_profile_exits_code_2_with_retry_escalation_in_stderr(
        self, tmp_cwd
    ):
        """YAML with retry_escalation.profile referencing a missing profile must be rejected."""
        content = _yaml("  profile: missing-profile\n")
        (tmp_cwd / "flow.yaml").write_text(content)
        result = run_fdsx(["run", str(tmp_cwd / "flow.yaml")], cwd=tmp_cwd)
        assert result.returncode == 2, (
            f"expected exit code 2, got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "retry_escalation" in result.stderr, (
            f"expected 'retry_escalation' in stderr: {result.stderr!r}"
        )

    def test_unknown_provider_exits_code_2(self, tmp_cwd):
        """YAML with retry_escalation.provider set to an unknown value must be rejected."""
        content = _yaml("  provider: not-a-real-provider\n  model: some-model\n")
        (tmp_cwd / "flow.yaml").write_text(content)
        result = run_fdsx(["run", str(tmp_cwd / "flow.yaml")], cwd=tmp_cwd)
        assert result.returncode == 2, (
            f"expected exit code 2, got {result.returncode}\nstderr: {result.stderr}"
        )

    def test_system_provider_exits_code_2(self, tmp_cwd):
        """retry_escalation with provider:system must be rejected (system not a valid LLM target)."""
        content = _yaml("  provider: system\n  model: irrelevant\n")
        (tmp_cwd / "flow.yaml").write_text(content)
        result = run_fdsx(["run", str(tmp_cwd / "flow.yaml")], cwd=tmp_cwd)
        assert result.returncode == 2, (
            f"expected exit code 2, got {result.returncode}\nstderr: {result.stderr}"
        )

    def test_valid_escalation_happy_path_exits_0(self, tmp_cwd):
        """Valid retry_escalation with system task (no LLM needed) exits 0."""
        content = _yaml("  provider: claude\n  model: claude-3-haiku\n")
        (tmp_cwd / "flow.yaml").write_text(content)
        result = run_fdsx(["run", str(tmp_cwd / "flow.yaml")], cwd=tmp_cwd)
        assert result.returncode == 0, (
            f"expected exit code 0, got {result.returncode}\nstderr: {result.stderr}"
        )

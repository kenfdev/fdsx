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
    def test_profile_field_is_rejected_with_clear_error(self, tmp_cwd):
        """YAML with only profile: foo must be rejected; stderr names 'profile' or 'retry_escalation'."""
        content = _yaml("  profile: foo\n")
        (tmp_cwd / "flow.yaml").write_text(content)
        result = run_fdsx(["run", str(tmp_cwd / "flow.yaml")], cwd=tmp_cwd)
        assert result.returncode == 2, (
            f"expected exit code 2, got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "profile" in result.stderr or "retry_escalation" in result.stderr, (
            f"expected 'profile' or 'retry_escalation' in stderr: {result.stderr!r}"
        )

    def test_profile_field_is_rejected_when_provider_set_too(self, tmp_cwd):
        """YAML with both profile and provider set must be rejected (extra-field guard catches profile)."""
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


class TestProjectScopeRetryEscalation:
    """E2E tests for retry_escalation when both global and project configs are present (T003).

    Verifies that .fdsx/config.yaml (project scope) is validated and applied correctly
    when a global config also declares retry_escalation.
    """

    def test_project_config_missing_model_exits_code_2(self, tmp_path, monkeypatch):
        """Project .fdsx/config.yaml with retry_escalation missing model → exit 2."""
        xdg_dir = tmp_path / "xdg"
        fdsx_global_dir = xdg_dir / "fdsx"
        fdsx_global_dir.mkdir(parents=True)
        (fdsx_global_dir / "config.yaml").write_text(
            "retry_escalation:\n  provider: claude\n  model: claude-sonnet-4-6\n"
        )

        project_cwd = tmp_path / "proj"
        (project_cwd / ".fdsx").mkdir(parents=True)
        (project_cwd / ".fdsx" / "config.yaml").write_text(
            "retry_escalation:\n  provider: codex\n"
        )
        flow_content = (
            "name: proj-scope-missing-model\ndescription: test\nstart_at: step1\n"
            + _TASK
        )
        (project_cwd / "flow.yaml").write_text(flow_content)

        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
        result = run_fdsx(["run", str(project_cwd / "flow.yaml")], cwd=project_cwd)
        assert result.returncode == 2, (
            f"expected exit code 2, got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "retry_escalation" in result.stderr, (
            f"expected 'retry_escalation' in stderr: {result.stderr!r}"
        )

    def test_project_config_overrides_global_and_exits_0(self, tmp_path, monkeypatch):
        """Project config overrides global retry_escalation (with provider_options) → exit 0."""
        xdg_dir = tmp_path / "xdg"
        fdsx_global_dir = xdg_dir / "fdsx"
        fdsx_global_dir.mkdir(parents=True)
        (fdsx_global_dir / "config.yaml").write_text(
            "retry_escalation:\n"
            "  provider: claude\n"
            "  model: claude-opus-4-7\n"
            "  provider_options:\n"
            "    permission_mode: bypassPermissions\n"
        )

        project_cwd = tmp_path / "proj"
        (project_cwd / ".fdsx").mkdir(parents=True)
        (project_cwd / ".fdsx" / "config.yaml").write_text(
            "retry_escalation:\n  provider: claude\n  model: claude-sonnet-4-6\n"
        )
        flow_content = (
            "name: proj-scope-override\ndescription: test\nstart_at: step1\n" + _TASK
        )
        (project_cwd / "flow.yaml").write_text(flow_content)

        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
        result = run_fdsx(["run", str(project_cwd / "flow.yaml")], cwd=project_cwd)
        assert result.returncode == 0, (
            f"expected exit code 0, got {result.returncode}\nstderr: {result.stderr}"
        )


class TestGlobalConfigRetryEscalation:
    """E2E tests for retry_escalation declared in .fdsx/config.yaml (T002)."""

    def test_global_config_missing_model_exits_code_2(self, tmp_cwd):
        """config.yaml with retry_escalation missing model: exits 2 and names retry_escalation."""
        (tmp_cwd / ".fdsx" / "config.yaml").write_text(
            "retry_escalation:\n  provider: claude\n"
        )
        flow_content = (
            "name: global-config-test\ndescription: test\nstart_at: step1\n" + _TASK
        )
        (tmp_cwd / "flow.yaml").write_text(flow_content)
        result = run_fdsx(["run", str(tmp_cwd / "flow.yaml")], cwd=tmp_cwd)
        assert result.returncode == 2, (
            f"expected exit code 2, got {result.returncode}\nstderr: {result.stderr}"
        )
        assert "retry_escalation" in result.stderr, (
            f"expected 'retry_escalation' in stderr: {result.stderr!r}"
        )

    def test_global_config_valid_escalation_exits_0(self, tmp_cwd):
        """config.yaml with valid retry_escalation + system task exits 0."""
        (tmp_cwd / ".fdsx" / "config.yaml").write_text(
            "retry_escalation:\n  provider: claude\n  model: claude-sonnet-4-6\n"
        )
        flow_content = (
            "name: global-config-valid-test\n"
            "description: test\n"
            "start_at: step1\n" + _TASK
        )
        (tmp_cwd / "flow.yaml").write_text(flow_content)
        result = run_fdsx(["run", str(tmp_cwd / "flow.yaml")], cwd=tmp_cwd)
        assert result.returncode == 0, (
            f"expected exit code 0, got {result.returncode}\nstderr: {result.stderr}"
        )

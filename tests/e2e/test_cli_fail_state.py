"""E2E tests: CLI exit codes, stderr, and run.json for fail state flows."""

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.e2e.cli_test_utils import run_fdsx

SIMPLE_FAIL_FLOW = textwrap.dedent("""\
    name: simple-fail
    description: Simple flow that routes to a fail state
    start_at: setup
    states:
      setup:
        type: task
        provider: system
        command: echo hello
        next: fail_it
      fail_it:
        type: fail
        error: ServiceError
        cause: downstream service unavailable
""")

FAIL_MISSING_ERROR_FLOW = textwrap.dedent("""\
    name: missing-error-fail
    description: Invalid flow with missing error field
    start_at: fail_it
    states:
      fail_it:
        type: fail
        cause: some cause
""")

FAIL_WITH_NEXT_FLOW = textwrap.dedent("""\
    name: fail-with-next
    description: Invalid flow with next field on fail state
    start_at: fail_it
    states:
      fail_it:
        type: fail
        error: SomeError
        cause: some cause
        next: nonexistent
""")

FAIL_BAD_VARREF_FLOW = textwrap.dedent("""\
    name: bad-varref-fail
    description: Flow with undefined variable in cause
    start_at: fail_it
    states:
      fail_it:
        type: fail
        error: SomeError
        cause: "value was: {undefined_var}"
""")

THREAD_ID = "e2e-fail-test"


@pytest.fixture(autouse=True)
def _init_fdsx_dir(tmp_path: Path) -> None:
    (tmp_path / ".fdsx").mkdir()


def _run_fail_flow(
    tmp_path: Path, thread_id: str = THREAD_ID
) -> subprocess.CompletedProcess[str]:
    flow_path = tmp_path / "flow.yaml"
    flow_path.write_text(SIMPLE_FAIL_FLOW)
    return run_fdsx(
        ["run", str(flow_path), "--thread-id", thread_id],
        cwd=str(tmp_path),
    )


def _run_json_path(tmp_path: Path, thread_id: str = THREAD_ID) -> Path:
    return tmp_path / ".fdsx" / "runs" / thread_id / "run.json"


class TestRunExitCode:
    def test_run_fail_flow_exits_with_code_1(self, tmp_path):
        proc = _run_fail_flow(tmp_path)

        assert proc.returncode == 1

    def test_run_fail_flow_does_not_exit_0(self, tmp_path):
        proc = _run_fail_flow(tmp_path, thread_id="e2e-not-zero")

        assert proc.returncode != 0


class TestRunStderr:
    def test_run_fail_flow_stderr_mentions_aborted(self, tmp_path):
        proc = _run_fail_flow(tmp_path, thread_id="e2e-stderr-aborted")

        assert "aborted" in proc.stderr.lower()

    def test_run_fail_flow_stderr_contains_error_name(self, tmp_path):
        proc = _run_fail_flow(tmp_path, thread_id="e2e-stderr-error")

        assert "ServiceError" in proc.stderr

    def test_run_fail_flow_stderr_contains_cause(self, tmp_path):
        proc = _run_fail_flow(tmp_path, thread_id="e2e-stderr-cause")

        assert "downstream service unavailable" in proc.stderr

    def test_run_fail_flow_stderr_contains_fail_state_name(self, tmp_path):
        proc = _run_fail_flow(tmp_path, thread_id="e2e-stderr-state")

        assert "fail_it" in proc.stderr


class TestRunJsonContent:
    def test_run_json_has_fail_state_entry_with_type_fail(self, tmp_path):
        thread_id = "e2e-runjson-type"
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(SIMPLE_FAIL_FLOW)
        run_fdsx(["run", str(flow_path), "--thread-id", thread_id], cwd=str(tmp_path))

        data = json.loads(_run_json_path(tmp_path, thread_id).read_text())
        fail_entry = next(s for s in data["states"] if s["name"] == "fail_it")
        assert fail_entry["type"] == "fail"
        assert fail_entry["status"] == "error"
        assert fail_entry["error_name"] == "ServiceError"
        assert fail_entry["error_cause"] == "downstream service unavailable"


class TestResumeAfterFail:
    def test_resume_after_fail_exits_with_code_1(self, tmp_path):
        thread_id = "e2e-resume-exit1"
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(SIMPLE_FAIL_FLOW)
        run_fdsx(["run", str(flow_path), "--thread-id", thread_id], cwd=str(tmp_path))

        proc = run_fdsx(
            ["resume", "--thread-id", thread_id],
            cwd=str(tmp_path),
        )

        assert proc.returncode == 1

    def test_resume_after_fail_stderr_reproduces_summary_line(self, tmp_path):
        thread_id = "e2e-resume-stderr"
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(SIMPLE_FAIL_FLOW)
        run_fdsx(["run", str(flow_path), "--thread-id", thread_id], cwd=str(tmp_path))

        proc = run_fdsx(
            ["resume", "--thread-id", thread_id],
            cwd=str(tmp_path),
        )

        assert "aborted" in proc.stderr.lower()
        assert "ServiceError" in proc.stderr

    def test_resume_after_fail_does_not_add_new_run_json_entries(self, tmp_path):
        thread_id = "e2e-resume-no-entries"
        flow_path = tmp_path / "flow.yaml"
        flow_path.write_text(SIMPLE_FAIL_FLOW)
        run_fdsx(["run", str(flow_path), "--thread-id", thread_id], cwd=str(tmp_path))
        run_json_path = _run_json_path(tmp_path, thread_id)
        count_before = len(json.loads(run_json_path.read_text())["states"])

        run_fdsx(["resume", "--thread-id", thread_id], cwd=str(tmp_path))

        count_after = len(json.loads(run_json_path.read_text())["states"])
        assert count_after == count_before


class TestValidateFailFlows:
    def test_validate_fail_missing_error_field_exits_2(self, tmp_path):
        flow_path = tmp_path / "missing_error.yaml"
        flow_path.write_text(FAIL_MISSING_ERROR_FLOW)

        proc = run_fdsx(["validate", str(flow_path)], cwd=str(tmp_path))

        assert proc.returncode == 2
        assert proc.stderr.strip() != ""

    def test_validate_fail_with_next_field_exits_2(self, tmp_path):
        flow_path = tmp_path / "fail_with_next.yaml"
        flow_path.write_text(FAIL_WITH_NEXT_FLOW)

        proc = run_fdsx(["validate", str(flow_path)], cwd=str(tmp_path))

        assert proc.returncode == 2

    def test_validate_fail_bad_varref_exits_2(self, tmp_path):
        flow_path = tmp_path / "bad_varref.yaml"
        flow_path.write_text(FAIL_BAD_VARREF_FLOW)

        proc = run_fdsx(["validate", str(flow_path)], cwd=str(tmp_path))

        assert proc.returncode == 2

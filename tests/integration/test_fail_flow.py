"""Integration tests: fail state execution, recording, checkpoint, and resume behavior."""

import json
import textwrap
from pathlib import Path

import pytest

from fdsx.core.engine import FlowResult, FlowValidationError, resume_flow, run_flow
from fdsx.logging.recorder import RUN_FILENAME, RUNS_DIR_NAME

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

VARIABLE_FAIL_FLOW = textwrap.dedent("""\
    name: variable-fail
    description: Flow where fail state cause uses a variable
    start_at: capture
    states:
      capture:
        type: task
        provider: system
        command: echo captured-value
        result_path: $.input
        next: fail_it
      fail_it:
        type: fail
        error: CaptureError
        cause: "got: {input}"
""")

BAD_VARREF_FAIL_FLOW = textwrap.dedent("""\
    name: bad-varref-fail
    description: Flow with undefined variable reference in fail state cause
    start_at: fail_it
    states:
      fail_it:
        type: fail
        error: SomeError
        cause: "value was: {undefined_var}"
""")


def _write_flow(tmp_path: Path, content: str, name: str = "flow.yaml") -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


def _read_run_json(base_dir: Path, thread_id: str) -> dict:
    path = base_dir / RUNS_DIR_NAME / thread_id / RUN_FILENAME
    return json.loads(path.read_text())


class TestRunFlowFailResult:
    def test_fail_state_returns_aborted_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, SIMPLE_FAIL_FLOW)

        result = run_flow(flow_path, thread_id="t-aborted-status", base_dir=tmp_path)

        assert result.status == "aborted"

    def test_fail_state_abort_state_matches_declared_state_name(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, SIMPLE_FAIL_FLOW)

        result = run_flow(flow_path, thread_id="t-abort-name", base_dir=tmp_path)

        assert result.abort_state == "fail_it"

    def test_flow_result_is_correct_type(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, SIMPLE_FAIL_FLOW)

        result = run_flow(flow_path, thread_id="t-type-check", base_dir=tmp_path)

        assert isinstance(result, FlowResult)


class TestRecorderFailStateEntry:
    def test_run_json_fail_entry_has_type_fail(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, SIMPLE_FAIL_FLOW)
        thread_id = "t-recorder-type"

        run_flow(flow_path, thread_id=thread_id, base_dir=tmp_path)

        data = _read_run_json(tmp_path, thread_id)
        fail_entry = next(s for s in data["states"] if s["name"] == "fail_it")
        assert fail_entry["type"] == "fail"

    def test_run_json_fail_entry_has_error_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, SIMPLE_FAIL_FLOW)
        thread_id = "t-recorder-status"

        run_flow(flow_path, thread_id=thread_id, base_dir=tmp_path)

        data = _read_run_json(tmp_path, thread_id)
        fail_entry = next(s for s in data["states"] if s["name"] == "fail_it")
        assert fail_entry["status"] == "error"

    def test_run_json_fail_entry_has_error_name_and_cause(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, SIMPLE_FAIL_FLOW)
        thread_id = "t-recorder-fields"

        run_flow(flow_path, thread_id=thread_id, base_dir=tmp_path)

        data = _read_run_json(tmp_path, thread_id)
        fail_entry = next(s for s in data["states"] if s["name"] == "fail_it")
        assert fail_entry["error_name"] == "ServiceError"
        assert fail_entry["error_cause"] == "downstream service unavailable"


class TestVariableSubstitutionInCause:
    def test_fail_state_cause_resolves_captured_variable(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, VARIABLE_FAIL_FLOW)
        thread_id = "t-var-cause"

        run_flow(flow_path, thread_id=thread_id, base_dir=tmp_path)

        data = _read_run_json(tmp_path, thread_id)
        fail_entry = next(s for s in data["states"] if s["name"] == "fail_it")
        assert "captured-value" in fail_entry["error_cause"]


class TestCheckpointSentinel:
    def test_resume_after_fail_requires_recovery_state(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, SIMPLE_FAIL_FLOW)
        thread_id = "t-resume-aborted"

        run_flow(flow_path, thread_id=thread_id, base_dir=tmp_path)
        with pytest.raises(RuntimeError, match="explicit recovery state"):
            resume_flow(thread_id, base_dir=tmp_path)

    def test_resume_after_fail_does_not_append_new_state_entries(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, SIMPLE_FAIL_FLOW)
        thread_id = "t-resume-no-new-entries"

        run_flow(flow_path, thread_id=thread_id, base_dir=tmp_path)
        state_count_before = len(_read_run_json(tmp_path, thread_id)["states"])

        with pytest.raises(RuntimeError, match="explicit recovery state"):
            resume_flow(thread_id, base_dir=tmp_path)

        state_count_after = len(_read_run_json(tmp_path, thread_id)["states"])
        assert state_count_after == state_count_before


class TestFlowValidationUndefinedVariable:
    def test_undefined_variable_in_cause_raises_flow_validation_error(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        flow_path = _write_flow(tmp_path, BAD_VARREF_FAIL_FLOW)

        with pytest.raises(FlowValidationError):
            run_flow(flow_path, thread_id="t-bad-var", base_dir=tmp_path)

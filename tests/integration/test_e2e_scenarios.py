"""Comprehensive end-to-end scenario tests with run log verification (T071, T072)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core import engine
from fdsx.core.loader import load_flow
from fdsx.providers.base import ProviderResult


class TestScenario1LinearFlow:
    """Scenario 1: Simple linear flow (Plan → Implement → Review)."""

    def test_linear_flow_state_transitions(self, tmp_path, monkeypatch):
        """Verify state transitions and final JSON output."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "simple_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        thread_id = "test-scenario1"
        result = engine.run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        assert "plan" in result
        assert "implementation" in result
        assert "review" in result
        assert "Plan:" in result["plan"]
        assert "Implementation:" in result["implementation"]
        assert "Review:" in result["review"]

    def test_linear_flow_run_log_schema(self, tmp_path, monkeypatch):
        """Verify runs/<thread_id>.json conforms to Run Log Format schema."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "simple_flow.yaml"
        thread_id = "test-scenario1-log"

        engine.run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        run_log = self._read_run_log(tmp_path, thread_id)

        assert run_log["thread_id"] == thread_id
        assert run_log["flow_name"] == "Simple Plan-Implement-Review Flow"
        assert "started_at" in run_log
        assert run_log["status"] == "completed"
        assert "completed_at" in run_log
        assert "final_variables" in run_log
        assert "states" in run_log
        assert isinstance(run_log["states"], list)

        state_names = [s["name"] for s in run_log["states"]]
        assert "plan" in state_names
        assert "implement" in state_names
        assert "review" in state_names

        for state in run_log["states"]:
            assert "name" in state
            assert "type" in state
            assert "started_at" in state
            assert "completed_at" in state
            assert "duration_seconds" in state
            assert "status" in state
            assert "output_preview" in state
            assert "variables_set" in state

        assert run_log["final_variables"]["plan"]
        assert run_log["final_variables"]["implementation"]
        assert run_log["final_variables"]["review"]

    def test_linear_flow_run_log_final_variables(self, tmp_path, monkeypatch):
        """Verify final_variables contains expected keys."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "simple_flow.yaml"
        thread_id = "test-scenario1-vars"

        engine.run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        run_log = self._read_run_log(tmp_path, thread_id)
        final_vars = run_log["final_variables"]

        assert "plan" in final_vars
        assert "implementation" in final_vars
        assert "review" in final_vars

    @staticmethod
    def _read_run_log(base_dir: Path, thread_id: str) -> dict:
        from fdsx.logging.recorder import RUNS_DIR_NAME, RUN_FILENAME
        log_path = base_dir / RUNS_DIR_NAME / thread_id / RUN_FILENAME
        assert log_path.exists(), f"Run log not found at {log_path}"
        with open(log_path, "r") as f:
            return json.load(f)


class TestScenario2ParallelVote:
    """Scenario 2: Parallel review + majority vote."""

    def test_parallel_review_aggregation_and_routing(self, tmp_path, monkeypatch):
        """Verify parallel execution, aggregation result, choice routing."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "parallel_review.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = engine.run_flow(path, thread_id="test-scenario2", base_dir=tmp_path)

        assert "reviews" in result
        assert len(result["reviews"]) == 3
        for review in result["reviews"]:
            assert "output" in review

        assert "decision" in result
        assert result["decision"] == "APPROVED"
        assert "approved_result" in result

    def test_parallel_run_log_has_branch_details(self, tmp_path, monkeypatch):
        """Verify parallel state entries include branch-level details."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "parallel_review.yaml"
        thread_id = "test-scenario2-log"

        engine.run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        run_log = TestScenario1LinearFlow._read_run_log(tmp_path, thread_id)

        parallel_states = [s for s in run_log["states"] if s["type"] == "parallel"]
        assert len(parallel_states) >= 1

        parallel_state = parallel_states[0]
        assert "branches" in parallel_state
        assert len(parallel_state["branches"]) == 3
        for branch in parallel_state["branches"]:
            assert "index" in branch
            assert "provider" in branch
            assert "status" in branch
            assert "duration_seconds" in branch

    def test_parallel_final_variables(self, tmp_path, monkeypatch):
        """Verify final_variables contains parallel and aggregation results."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "parallel_review.yaml"
        thread_id = "test-scenario2-vars"

        engine.run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        run_log = TestScenario1LinearFlow._read_run_log(tmp_path, thread_id)
        final_vars = run_log["final_variables"]

        assert "reviews" in final_vars
        assert len(final_vars["reviews"]) == 3
        assert "decision" in final_vars
        assert final_vars["decision"] == "APPROVED"


class TestScenario3WaitWebhook:
    """Scenario 3: Wait state + webhook notification."""

    def test_wait_webhook_routing(self, tmp_path, monkeypatch):
        """Verify wait state + webhook → choice routing."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "wait_webhook.yaml"

        with patch("fdsx.notify.webhook.send_webhook") as mock_webhook:
            mock_webhook.return_value = True

            with patch("builtins.input", return_value="1"):
                result = engine.run_flow(path, thread_id="test-scenario3", base_dir=tmp_path)

            assert mock_webhook.call_count == 1
            assert "plan_output" in result
            assert "approval_decision" in result
            assert result["approval_decision"] == "approve"
            assert "implementation_output" in result

    def test_wait_webhook_run_log(self, tmp_path, monkeypatch):
        """Verify run log for wait state scenario."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "wait_webhook.yaml"
        thread_id = "test-scenario3-log"

        with patch("fdsx.notify.webhook.send_webhook") as mock_webhook:
            mock_webhook.return_value = True

            with patch("builtins.input", return_value="1"):
                engine.run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        run_log = TestScenario1LinearFlow._read_run_log(tmp_path, thread_id)

        assert run_log["status"] == "completed"
        state_names = [s["name"] for s in run_log["states"]]
        assert "plan" in state_names
        assert "approval_gate" in state_names
        assert "implement" in state_names

        wait_state = next(s for s in run_log["states"] if s["name"] == "approval_gate")
        assert wait_state["type"] == "wait"

        assert "final_variables" in run_log
        assert run_log["final_variables"]["approval_decision"] == "approve"


class TestScenario4CheckpointResume:
    """Scenario 4: Checkpoint/resume with run log append."""

    def test_checkpoint_resume_completes_flow(self, monkeypatch):
        """Run flow → interrupt at implement → resume → verify completion."""
        repo_root = Path.cwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            base_dir = Path(tmpdir) / ".fdsx"
            thread_id = "test-scenario4"
            flow_path = repo_root / "tests" / "fixtures" / "checkpoint_flow.yaml"

            with patch(
                "fdsx.providers.system.SystemProvider.execute",
                side_effect=[
                    ProviderResult(exit_code=0, stdout="plan output", stderr=""),
                    Exception("simulated crash on implement state"),
                ],
            ):
                with pytest.raises(RuntimeError, match="Flow execution failed"):
                    engine.run_flow(
                        flow_path,
                        thread_id=thread_id,
                        base_dir=base_dir,
                    )

            manager = CheckpointManager(base_dir=base_dir)
            assert manager.verify_checkpoint(thread_id)

            result = engine.resume_flow(
                thread_id,
                base_dir,
                flow_path,
            )

            assert result.get("plan_output") == "plan output"
            assert result.get("implement_output") == "implement output"
            assert result.get("review_output") == "review output"

    def test_resume_appends_to_existing_run_log(self, monkeypatch):
        """Verify resume appends new state entries to existing run log."""
        repo_root = Path.cwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            base_dir = Path(tmpdir) / ".fdsx"
            thread_id = "test-scenario4-append"
            flow_path = repo_root / "tests" / "fixtures" / "checkpoint_flow.yaml"

            with patch(
                "fdsx.providers.system.SystemProvider.execute",
                side_effect=[
                    ProviderResult(exit_code=0, stdout="plan output", stderr=""),
                    Exception("simulated crash"),
                ],
            ):
                with pytest.raises(RuntimeError, match="simulated crash"):
                    engine.run_flow(
                        flow_path,
                        thread_id=thread_id,
                        base_dir=base_dir,
                    )

            initial_log = TestScenario1LinearFlow._read_run_log(base_dir, thread_id)
            initial_started_at = initial_log["started_at"]

            engine.resume_flow(
                thread_id,
                base_dir,
                flow_path,
            )

            resumed_log = TestScenario1LinearFlow._read_run_log(base_dir, thread_id)
            resumed_state_names = [s["name"] for s in resumed_log["states"]]
            resumed_started_at = resumed_log["started_at"]

            assert resumed_started_at == initial_started_at, (
                "started_at must not change on resume"
            )
            assert "plan" in resumed_state_names
            assert "implement" in resumed_state_names
            assert "review" in resumed_state_names
            assert len(resumed_log["states"]) > len(initial_log["states"]), (
                "Resume should append new state entries, not overwrite"
            )

            assert resumed_log["status"] == "completed"
            assert "final_variables" in resumed_log
            assert resumed_log["final_variables"]["review_output"] == "review output"

    def test_checkpoint_resume_run_log_schema(self, monkeypatch):
        """Verify run log schema after checkpoint/resume."""
        repo_root = Path.cwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.chdir(tmpdir)
            base_dir = Path(tmpdir) / ".fdsx"
            thread_id = "test-scenario4-schema"
            flow_path = repo_root / "tests" / "fixtures" / "checkpoint_flow.yaml"

            engine.run_flow(
                flow_path,
                thread_id=thread_id,
                base_dir=base_dir,
            )

            run_log = TestScenario1LinearFlow._read_run_log(base_dir, thread_id)

            assert run_log["thread_id"] == thread_id
            assert "started_at" in run_log
            assert "completed_at" in run_log
            assert run_log["status"] == "completed"
            assert "states" in run_log
            assert "final_variables" in run_log

            for state in run_log["states"]:
                assert "name" in state
                assert "type" in state
                assert "status" in state
                assert state["status"] in ("completed", "success")


class TestScenario5ExtractionChoice:
    """Scenario 5: Extraction + Choice routing."""

    def test_extraction_drives_choice_routing(self, tmp_path, monkeypatch):
        """Verify keyword extraction drives correct branch."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "extraction_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = engine.run_flow(path, thread_id="test-scenario5", base_dir=tmp_path)

        assert "raw_output" in result
        assert "decision" in result
        assert result["decision"] == "APPROVED"
        assert "approved_result" in result

    def test_extraction_run_log(self, tmp_path, monkeypatch):
        """Verify run log for extraction + choice scenario."""
        repo_root = Path.cwd()
        monkeypatch.chdir(tmp_path)
        path = repo_root / "tests" / "fixtures" / "extraction_flow.yaml"
        thread_id = "test-scenario5-log"

        engine.run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        run_log = TestScenario1LinearFlow._read_run_log(tmp_path, thread_id)

        assert run_log["status"] == "completed"
        state_names = [s["name"] for s in run_log["states"]]
        assert "echo_state" in state_names
        assert "decision" in state_names
        assert "approved_path" in state_names

        echo_state = next(s for s in run_log["states"] if s["name"] == "echo_state")
        assert echo_state["type"] == "task"
        assert echo_state["status"] in ("completed", "success")
        assert "output_preview" in echo_state
        assert "variables_set" in echo_state

        assert "final_variables" in run_log
        assert run_log["final_variables"]["decision"] == "APPROVED"
        assert run_log["final_variables"]["raw_output"]
        assert "APPROVED" in run_log["final_variables"]["raw_output"]

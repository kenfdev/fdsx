"""Integration tests for v2 streaming format (T025/T026).

These tests verify that the v2 streaming format correctly handles:
- Wait state flows that pause and resume (T025)
- Non-wait flows that complete normally (T026)
"""

from unittest.mock import patch

from fdsx.core.engine import FlowResult, run_flow
from tests import FIXTURES_DIR


class TestStreamingV2:
    def test_wait_state_flow_pauses_and_resumes(self, tmp_path):
        """T025: Wait state flow pauses at interrupt and resumes with approval decision."""
        base_dir = tmp_path / ".fdsx"
        path = FIXTURES_DIR / "wait_approval.yaml"

        with patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            return_value="approve",
        ):
            result = run_flow(path, base_dir=base_dir)

        assert isinstance(result, FlowResult)
        assert result.status == "completed", (
            f"Expected completed status, got: {result.status}. results={result.results}"
        )
        assert result.results.get("approval_decision") == "approve", (
            f"Expected approval_decision='approve', got: {result.results}"
        )

    def test_non_wait_flow_completes_normally(self, tmp_path):
        """T026: Non-wait linear flow completes with all expected result keys present."""
        base_dir = tmp_path / ".fdsx"
        path = FIXTURES_DIR / "checkpoint_flow.yaml"

        result = run_flow(path, base_dir=base_dir)

        assert isinstance(result, FlowResult)
        assert result.status == "completed", (
            f"Expected completed status, got: {result.status}. results={result.results}"
        )
        assert "plan_output" in result.results, (
            f"plan_output missing from results: {list(result.results.keys())}"
        )
        assert "implement_output" in result.results, (
            f"implement_output missing from results: {list(result.results.keys())}"
        )
        assert "review_output" in result.results, (
            f"review_output missing from results: {list(result.results.keys())}"
        )

"""Integration tests for FlowResult structured return type (T001, T002)."""

from fdsx.core.engine import FlowResult, run_flow
from tests import FIXTURES_DIR


class TestFlowResultNormalCompletion:
    def test_normal_flow_returns_flow_result_with_completed_status(self, tmp_path):
        """T001: Normal workflow returns FlowResult with status='completed'."""
        path = FIXTURES_DIR / "simple_flow.yaml"

        result = run_flow(path, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        assert result.status == "completed"
        assert result.abort_state is None
        assert isinstance(result.results, dict)


class TestFlowResultAbortDetection:
    def test_abort_state_returns_flow_result_with_aborted_status(self, tmp_path):
        """T002: Workflow ending at abort_* state returns status='aborted' with abort_state set."""
        flow_yaml = tmp_path / "abort_flow.yaml"
        flow_yaml.write_text(
            "name: Abort Test Flow\n"
            "description: Test flow that ends at an abort state\n"
            "start_at: abort_blocked\n"
            "states:\n"
            "  abort_blocked:\n"
            "    type: task\n"
            "    provider: system\n"
            "    command: echo blocked\n"
            "    result_path: $.abort_output\n"
            "    end: true\n"
        )

        result = run_flow(flow_yaml, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        assert result.status == "aborted"
        assert result.abort_state == "abort_blocked"

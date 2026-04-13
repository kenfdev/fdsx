from fdsx.core.compiler import compile_flow
from fdsx.core.engine import FlowResult, run_flow
from fdsx.core.loader import load_flow
from tests import FIXTURES_DIR


class TestLinearFlow:
    def test_end_to_end_linear_flow(self, tmp_path):
        path = FIXTURES_DIR / "simple_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

        compiled = compile_flow(flow)
        assert compiled is not None

        result = run_flow(path, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        assert "plan" in result.results
        assert "implementation" in result.results
        assert "review" in result.results
        assert "Plan:" in result.results["plan"]
        assert "Implementation:" in result.results["implementation"]
        assert "Review:" in result.results["review"]

    def test_linear_flow_with_inputs(self, tmp_path):
        path = FIXTURES_DIR / "simple_flow.yaml"

        result = run_flow(path, inputs={"task": "test task"}, base_dir=tmp_path)

        assert "plan" in result.results
        assert "implementation" in result.results

    def test_linear_flow_thread_id(self, tmp_path):
        path = FIXTURES_DIR / "simple_flow.yaml"

        thread_id = "test-thread-123"
        result = run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        assert "plan" in result.results

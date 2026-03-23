from pathlib import Path

from fdsx.core.compiler import compile_flow
from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow


class TestLinearFlow:
    def test_end_to_end_linear_flow(self, tmp_path):
        path = Path("tests/fixtures/simple_flow.yaml")

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

        compiled = compile_flow(flow)
        assert compiled is not None

        result = run_flow(path, base_dir=tmp_path)

        assert "plan" in result
        assert "implementation" in result
        assert "review" in result
        assert "Plan:" in result["plan"]
        assert "Implementation:" in result["implementation"]
        assert "Review:" in result["review"]

    def test_linear_flow_with_inputs(self, tmp_path):
        path = Path("tests/fixtures/simple_flow.yaml")

        result = run_flow(path, inputs={"task": "test task"}, base_dir=tmp_path)

        assert "plan" in result
        assert "implementation" in result

    def test_linear_flow_thread_id(self, tmp_path):
        path = Path("tests/fixtures/simple_flow.yaml")

        thread_id = "test-thread-123"
        result = run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        assert "plan" in result

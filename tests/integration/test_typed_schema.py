"""Integration tests for TypedDict schema always being used (Phase 2)."""

from fdsx.core.compiler import compile_flow
from fdsx.core.engine import FlowResult, run_flow
from fdsx.core.loader import load_flow
from tests import FIXTURES_DIR


class TestTypedSchemaLinearFlow:
    def test_non_parallel_flow_produces_correct_results(self, tmp_path):
        """Non-parallel linear flow runs correctly with TypedDict schema."""
        path = FIXTURES_DIR / "simple_flow.yaml"

        result = run_flow(path, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        assert "plan" in result.results
        assert "implementation" in result.results
        assert "review" in result.results

    def test_non_parallel_flow_compiled_graph_uses_named_channels(self):
        """Compiled graph for non-parallel flow has named channels, not __root__."""
        path = FIXTURES_DIR / "simple_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

        compiled = compile_flow(flow)
        assert compiled is not None

        # With TypedDict schema, LangGraph registers named channels.
        # The graph must NOT have a '__root__' channel.
        channels = compiled.graph.channels
        assert "__root__" not in channels, (
            "Non-parallel flow must use named channels (TypedDict), not __root__"
        )

        # Named result channels from simple_flow.yaml must be present
        assert "plan" in channels
        assert "implementation" in channels
        assert "review" in channels

        # remaining_steps managed channel must be present (Phase 2)
        assert "remaining_steps" in channels

from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow
from tests import FIXTURES_DIR


class TestChoiceFlow:
    def test_choice_flow_matches_first_rule(self, tmp_path):
        path = FIXTURES_DIR / "choice_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "status" in result

    def test_choice_flow_explicit_status(self, tmp_path):
        path = FIXTURES_DIR / "choice_flow.yaml"

        result = run_flow(path, inputs={"initial_status": "ready"}, base_dir=tmp_path)

        assert "status" in result

    def test_choice_flow_strips_stdout_newline(self, tmp_path):
        path = FIXTURES_DIR / "choice_flow.yaml"

        result = run_flow(path, inputs={"initial_status": "ready"}, base_dir=tmp_path)

        assert result["status"] == "ready"
        assert "proceed_result" in result
        assert result["proceed_result"] == "Proceeding with execution"

    def test_choice_flow_default_branch(self, tmp_path):
        """Test that the default branch is taken when no rule matches."""
        path = FIXTURES_DIR / "choice_flow_default.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "status" in result
        assert result["status"] == "unknown_status"
        assert "error_result" in result
        assert result["error_result"] == "Unknown status"

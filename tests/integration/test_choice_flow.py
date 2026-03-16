from pathlib import Path

from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow


class TestChoiceFlow:
    def test_choice_flow_matches_first_rule(self):
        path = Path("tests/fixtures/choice_flow.yaml")

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path)

        assert "status" in result

    def test_choice_flow_explicit_status(self):
        path = Path("tests/fixtures/choice_flow.yaml")

        result = run_flow(path, inputs={"initial_status": "ready"})

        assert "status" in result

    def test_choice_flow_strips_stdout_newline(self):
        path = Path("tests/fixtures/choice_flow.yaml")

        result = run_flow(path, inputs={"initial_status": "ready"})

        assert result["status"] == "ready"
        assert "proceed_result" in result
        assert result["proceed_result"] == "Proceeding with execution"

    def test_choice_flow_default_branch(self):
        """Test that the default branch is taken when no rule matches."""
        path = Path("tests/fixtures/choice_flow_default.yaml")

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path)

        assert "status" in result
        assert result["status"] == "unknown_status"
        assert "error_result" in result
        assert result["error_result"] == "Unknown status"

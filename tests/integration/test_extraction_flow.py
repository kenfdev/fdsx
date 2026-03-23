from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow
from fdsx.models.flow import Branch, ExtractRule, Flow, ParallelState
from tests import FIXTURES_DIR


class TestExtractionFlow:
    def test_keyword_extraction_choice_routing(self, tmp_path):
        """Test keyword extraction and correct Choice routing."""
        path = FIXTURES_DIR / "extraction_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "decision" in result
        assert result["decision"] == "APPROVED"
        assert "approved_result" in result

    def test_regex_extraction_choice_routing(self, tmp_path):
        """Test regex extraction from structured output."""
        path = FIXTURES_DIR / "regex_extraction_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "extracted_status" in result
        assert result["extracted_status"] == "success"
        assert "success_result" in result

    def test_json_extraction_choice_routing(self, tmp_path):
        """Test JSON extraction from raw JSON output."""
        path = FIXTURES_DIR / "json_extraction_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "extracted_status" in result
        assert result["extracted_status"] == "approved"
        assert "approved_result" in result

    def test_json_codeblock_extraction_choice_routing(self, tmp_path):
        """Test JSON extraction from ```json code block output (T024)."""
        path = FIXTURES_DIR / "json_codeblock_extraction_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "extracted_status" in result
        assert result["extracted_status"] == "approved"
        assert "approved_result" in result


class TestParallelBranchExtraction:
    def test_parallel_branch_extraction_failure_marked_as_error(self):
        """Regression test: parallel branch extraction failure should be marked as error with exit_code 1."""
        flow = Flow(
            name="Parallel Extraction Failure Test",
            description="Test flow for parallel branch extraction failure testing",
            start_at="parallel_state",
            states={
                "parallel_state": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(
                            provider="system",
                            command="echo hello",
                            extract=ExtractRule(
                                strategy=["keyword"],
                                pattern="APPROVED|REJECTED",
                                result_path="$.decision",
                            ),
                            retry=0,
                        ),
                    ],
                    result_path="$.parallel_results",
                    min_success=0,
                    end=True,
                ),
            },
        )

        from fdsx.core.compiler import compile_flow

        compiled = compile_flow(flow)
        result = compiled.graph.invoke({})

        assert "parallel_results" in result
        assert len(result["parallel_results"]) == 1
        branch_result = result["parallel_results"][0]
        assert branch_result["exit_code"] == 1
        assert "error" in branch_result
        assert "Extraction failed" in branch_result["error"]

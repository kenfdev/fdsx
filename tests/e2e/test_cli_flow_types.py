from tests.e2e.cli_test_utils import fixture_path, run_fdsx


class TestCLIE2EPhase2:
    """End-to-end CLI tests for Phase 2 flow types."""

    def test_parallel_review_yaml_validates(self):
        """Test parallel_review.yaml validates successfully."""
        result = run_fdsx(["validate", fixture_path("parallel_review.yaml")])
        assert result.returncode == 0

    def test_parallel_review_yaml_runs(self):
        """Test parallel_review.yaml runs successfully via CLI."""
        result = run_fdsx(["run", fixture_path("parallel_review.yaml")])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr

    def test_extraction_flow_yaml_validates(self):
        """Test extraction_flow.yaml validates successfully."""
        result = run_fdsx(["validate", fixture_path("extraction_flow.yaml")])
        assert result.returncode == 0

    def test_extraction_flow_yaml_runs(self):
        """Test extraction_flow.yaml runs successfully via CLI."""
        result = run_fdsx(["run", fixture_path("extraction_flow.yaml")])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr

    def test_loop_flow_yaml_validates(self):
        """Test loop_flow.yaml validates successfully."""
        result = run_fdsx(["validate", fixture_path("loop_flow.yaml")])
        assert result.returncode == 0

    def test_loop_flow_yaml_runs(self):
        """Test loop exhaustion is a non-success CLI outcome."""
        result = run_fdsx(["run", fixture_path("loop_flow.yaml")])
        assert result.returncode == 1, f"stderr: {result.stderr}"
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        assert "max_loop_reached" in result.stderr

from tests.e2e.cli_test_utils import fixture_path, run_fdsx


class TestCLIMapFlow:
    """End-to-end CLI tests for map flow type."""

    def test_map_basic_yaml_validates(self):
        """Test map_basic.yaml validates successfully."""
        result = run_fdsx(["validate", fixture_path("map_basic.yaml")])
        assert result.returncode == 0

    def test_map_basic_yaml_runs(self):
        """Test map_basic.yaml runs successfully via CLI."""
        result = run_fdsx(["run", fixture_path("map_basic.yaml")])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr
        # Map-specific: verify iteration actually happened
        assert "map, 3 items" in result.stderr
        assert "iter-1/3" in result.stderr
        assert "iter-3/3" in result.stderr
        assert "3 items, 0 failed" in result.stderr

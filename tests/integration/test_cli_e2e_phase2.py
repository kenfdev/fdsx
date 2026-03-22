import subprocess


def get_fdsx_command():
    """Get the fdsx command using uv to exercise the entry point."""
    return ["uv", "run", "fdsx"]


class TestCLIE2EPhase2:
    """End-to-end CLI tests for Phase 2 flow types."""

    def test_parallel_review_yaml_validates(self):
        """Test parallel_review.yaml validates successfully."""
        result = subprocess.run(
            get_fdsx_command()
            + [
                "validate",
                "tests/fixtures/parallel_review.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_parallel_review_yaml_runs(self):
        """Test parallel_review.yaml runs successfully via CLI."""
        result = subprocess.run(
            get_fdsx_command()
            + [
                "run",
                "tests/fixtures/parallel_review.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr

    def test_extraction_flow_yaml_validates(self):
        """Test extraction_flow.yaml validates successfully."""
        result = subprocess.run(
            get_fdsx_command()
            + [
                "validate",
                "tests/fixtures/extraction_flow.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_extraction_flow_yaml_runs(self):
        """Test extraction_flow.yaml runs successfully via CLI."""
        result = subprocess.run(
            get_fdsx_command()
            + [
                "run",
                "tests/fixtures/extraction_flow.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr

    def test_loop_flow_yaml_validates(self):
        """Test loop_flow.yaml validates successfully."""
        result = subprocess.run(
            get_fdsx_command()
            + [
                "validate",
                "tests/fixtures/loop_flow.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_loop_flow_yaml_runs(self):
        """Test loop_flow.yaml runs successfully via CLI with loop behavior."""
        result = subprocess.run(
            get_fdsx_command()
            + [
                "run",
                "tests/fixtures/loop_flow.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # Verify loop completion message on stderr
        assert "Loop completed" in result.stderr
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr

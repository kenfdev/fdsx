import subprocess


def get_fdsx_command():
    """Get the fdsx command using uv to exercise the entry point."""
    return ["uv", "run", "fdsx"]


class TestCLIE2E:
    def test_validate_valid_flow(self):
        result = subprocess.run(
            get_fdsx_command()
            + [
                "validate",
                "tests/fixtures/simple_flow.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_validate_invalid_flow(self):
        result = subprocess.run(
            get_fdsx_command()
            + [
                "validate",
                "tests/fixtures/invalid_flows/missing_start_at.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert result.stderr, "Expected error message on stderr"
        assert len(result.stderr.strip()) > 0

    def test_run_simple_flow(self):
        result = subprocess.run(
            get_fdsx_command()
            + [
                "run",
                "tests/fixtures/simple_flow.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr

    def test_run_with_input(self):
        result = subprocess.run(
            get_fdsx_command()
            + [
                "run",
                "tests/fixtures/simple_flow.yaml",
                "--input",
                "task=hello",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr

    def test_run_with_input_uses_value(self):
        """R2-F4: --input value must be consumed; workflow completes successfully."""
        result = subprocess.run(
            get_fdsx_command()
            + [
                "run",
                "tests/fixtures/input_flow.yaml",
                "--input",
                "task=world",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # FR-1.3: No JSON on stdout
        assert result.stdout == ""
        # FR-1.1: Completion message on stderr
        assert "completed successfully" in result.stderr

    def test_run_with_invalid_flow_returns_exit_code_2(self):
        result = subprocess.run(
            get_fdsx_command()
            + [
                "run",
                "tests/fixtures/invalid_flows/missing_start_at.yaml",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2

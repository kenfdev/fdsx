from tests.e2e.cli_test_utils import run_fdsx


class TestAddCommand:
    def test_split_subcommand_removed(self):
        """fdsx split foo.md should return exit 2 with unknown command error."""
        result = run_fdsx(["split", "foo.md"])
        assert result.returncode == 2
        assert (
            "No such command" in result.stderr
            or "no such command" in result.stderr.lower()
        )

    def test_add_nonexistent_file(self):
        """fdsx add nonexistent.md should return exit 2 with 'not found' error."""
        result = run_fdsx(["add", "nonexistent.md"])
        assert result.returncode == 2
        assert "not found" in result.stderr.lower()

    def test_add_missing_required_arg_shows_help(self):
        """fdsx add with no arguments should return exit 2."""
        result = run_fdsx(["add"])
        assert result.returncode == 2

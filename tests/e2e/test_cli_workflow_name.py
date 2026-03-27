"""Integration tests for workflow name display in validate command."""

from tests.e2e.cli_test_utils import fixture_path, run_fdsx


class TestWorkflowNameDisplay:
    def test_validate_output_includes_flow_name(self):
        """fdsx validate should show flow.name in success message."""
        result = run_fdsx(["validate", fixture_path("simple_flow.yaml")])
        assert result.returncode == 0
        assert "Flow 'Simple Plan-Implement-Review Flow' is valid." in result.stdout

    def test_validate_workflows_name_display_alpha(self):
        """discover_workflows should find the alpha workflow with its flow.name."""
        from pathlib import Path

        from fdsx.core.selector import discover_workflows

        fixtures_dir = Path(__file__).parent.parent / "fixtures"
        workflows_dir = fixtures_dir / "workflows_name_display"
        results = discover_workflows(workflows_dir)

        names = [r[2] for r in results]
        assert any("Code Review" in name for name in names)
        assert any("Deploy Pipeline" in name for name in names)

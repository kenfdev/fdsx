"""Unit tests for workflow name display (flow.name as display_name)."""

from __future__ import annotations

from pathlib import Path

import yaml

from fdsx.core.selector import (
    _build_workflow_selection_prompt,
    discover_workflows,
)


def _minimal_workflow(name: str, description: str) -> str:
    """Return minimal valid workflow YAML content."""
    return yaml.dump(
        {
            "name": name,
            "description": description,
            "start_at": "s",
            "states": {
                "s": {
                    "type": "task",
                    "provider": "system",
                    "command": f"echo {name}",
                    "result_path": "$.x",
                    "end": True,
                }
            },
        }
    )


class TestDiscoverWorkflowsFlowName:
    def test_display_name_is_flow_name_not_filename(self, tmp_path):
        """display_name should be flow.name, not the file stem."""
        (tmp_path / "myfile.yaml").write_text(
            _minimal_workflow("My Workflow Name", "Does things")
        )

        results = discover_workflows(tmp_path)

        assert len(results) == 1
        assert results[0][2] == "My Workflow Name"

    def test_display_name_is_flow_name_for_directory_workflow(self, tmp_path):
        """display_name for directory workflow should be flow.name, not dirname."""
        wf_dir = tmp_path / "some-directory"
        wf_dir.mkdir()
        (wf_dir / "workflow.yaml").write_text(
            _minimal_workflow("Directory Workflow", "A workflow")
        )

        results = discover_workflows(tmp_path)

        assert len(results) == 1
        assert results[0][2] == "Directory Workflow"

    def test_duplicate_names_get_filepath_disambiguation(self, tmp_path):
        """Two workflows with the same flow.name should have filepath appended."""
        (tmp_path / "alpha.yaml").write_text(
            _minimal_workflow("Code Review", "Review workflow alpha")
        )
        beta_dir = tmp_path / "beta"
        beta_dir.mkdir()
        (beta_dir / "workflow.yaml").write_text(
            _minimal_workflow("Code Review", "Review workflow beta")
        )

        results = discover_workflows(tmp_path)

        assert len(results) == 2
        display_names = [r[2] for r in results]
        assert "Code Review" in display_names[0] or "Code Review" in display_names[1]
        has_disambiguation = any("(" in dn and ")" in dn for dn in display_names)
        assert has_disambiguation, f"Expected disambiguation in {display_names}"

    def test_unique_names_no_disambiguation(self, tmp_path):
        """Workflows with unique names should not have filepath appended."""
        (tmp_path / "alpha.yaml").write_text(
            _minimal_workflow("Alpha Review", "Alpha workflow")
        )
        (tmp_path / "beta.yaml").write_text(
            _minimal_workflow("Beta Review", "Beta workflow")
        )

        results = discover_workflows(tmp_path)

        assert len(results) == 2
        display_names = [r[2] for r in results]
        assert "Alpha Review" in display_names
        assert "Beta Review" in display_names
        for dn in display_names:
            assert "(" not in dn, f"Unexpected disambiguation in {dn}"

    def test_disambiguation_format_shows_relative_path(self, tmp_path):
        """Disambiguation should show relative path from workflows_dir."""
        (tmp_path / "alpha.yaml").write_text(
            _minimal_workflow("Code Review", "Alpha review")
        )
        sub_dir = tmp_path / "subdir"
        sub_dir.mkdir()
        (sub_dir / "workflow.yaml").write_text(
            _minimal_workflow("Code Review", "Subdir review")
        )

        results = discover_workflows(tmp_path)

        display_names = [r[2] for r in results]
        disambiguated = [dn for dn in display_names if "(" in dn]
        assert len(disambiguated) == 2
        for dn in disambiguated:
            assert "alpha.yaml" in dn or "subdir" in dn

    def test_sorted_by_flow_name_not_filename(self, tmp_path):
        """Results should be sorted by flow.name, not filename."""
        (tmp_path / "z-workflow.yaml").write_text(
            _minimal_workflow("Alpha Workflow", "First alphabetically")
        )
        (tmp_path / "a-workflow.yaml").write_text(
            _minimal_workflow("Zeta Workflow", "Last alphabetically")
        )

        results = discover_workflows(tmp_path)

        display_names = [r[2] for r in results]
        assert display_names == ["Alpha Workflow", "Zeta Workflow"]


class TestBuildWorkflowSelectionPromptFlowNames:
    def test_prompt_contains_flow_names(self):
        """_build_workflow_selection_prompt should use flow names in output."""
        workflows = [
            (Path("plan.yaml"), "Plan workflow", "Plan"),
            (Path("review.yaml"), "Review workflow", "Code Review"),
        ]
        prompt = _build_workflow_selection_prompt("Do something", workflows)
        assert "Plan" in prompt
        assert "Code Review" in prompt

    def test_prompt_contains_flow_names_with_disambiguation(self):
        """Prompt should contain flow names even when disambiguation is present."""
        workflows = [
            (Path("alpha.yaml"), "Alpha workflow", "Code Review (alpha.yaml)"),
            (
                Path("beta/workflow.yaml"),
                "Beta workflow",
                "Code Review (beta/workflow.yaml)",
            ),
        ]
        prompt = _build_workflow_selection_prompt("Review code", workflows)
        assert "Code Review" in prompt
        assert "alpha.yaml" in prompt
        assert "beta/workflow.yaml" in prompt

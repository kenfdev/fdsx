"""Integration tests for plan-implement-review example workflow (T005-T008).

Tests the refactored workflow that uses prompt_file references instead of
inline prompt_template, with profile references resolved via config_profiles.
"""

import importlib.resources

import pytest
import yaml

from fdsx.core.compiler import compile_flow
from fdsx.core.loader import load_flow


class TestExampleWorkflow:
    """Tests for plan-implement-review workflow loading and structure."""

    @pytest.fixture
    def workflow_path(self):
        return (
            importlib.resources.files("fdsx.examples.workflows")
            / "plan-implement-review"
            / "workflow.yaml"
        )

    @pytest.fixture
    def workflow_dir(self, workflow_path):
        return workflow_path.parent

    @pytest.fixture
    def config_profiles(self):
        return {
            "smarty": {"provider": "claude", "model": "claude-sonnet-4-6"},
            "specialist": {"provider": "codex", "model": "gpt-5.4"},
            "doer": {"provider": "opencode", "model": "opencode-go/minimax-m2.5"},
        }

    def test_workflow_loads_with_config_profiles(self, workflow_path, config_profiles):
        """T007: load_flow with config_profiles resolves profile references successfully."""
        flow, errors = load_flow(workflow_path, config_profiles=config_profiles)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

    def test_workflow_compiles_after_profile_resolution(
        self, workflow_path, config_profiles
    ):
        """T007: Flow compiles successfully after profile resolution."""
        flow, errors = load_flow(workflow_path, config_profiles=config_profiles)
        assert flow is not None, f"Failed to load: {errors}"

        compiled = compile_flow(flow)
        assert compiled is not None

    def test_profile_keys_present_in_raw_yaml(self, workflow_path):
        """T007: Verify profile keys exist in raw YAML before load_flow resolution.

        The raw YAML must have profile references on task states and parallel
        branches because load_flow resolves (and deletes) them during loading.
        """
        with open(workflow_path) as f:
            data = yaml.safe_load(f)

        assert "profiles" not in data, (
            "profiles block should be removed from workflow YAML"
        )

        plan_state = data["states"]["plan"]
        assert plan_state.get("profile") == "smarty", (
            "plan state should have profile: smarty"
        )

        implement_state = data["states"]["implement"]
        assert implement_state.get("profile") == "doer", (
            "implement state should have profile: doer"
        )

        parallel_review = data["states"]["parallel_review"]
        assert len(parallel_review["branches"]) == 2
        assert parallel_review["branches"][0].get("profile") == "specialist"
        assert parallel_review["branches"][1].get("profile") == "specialist"

    def test_prompt_file_references_resolve_to_existing_files(
        self, workflow_path, workflow_dir
    ):
        """T007: Verify prompt_file references resolve to existing files.

        Uses Path resolution relative to workflow YAML's parent directory,
        matching how _resolve_prompt_files works in loader.py.
        """
        plan_prompt_path = workflow_dir / "plan-prompt.txt"
        implement_prompt_path = workflow_dir / "implement-prompt.txt"

        assert plan_prompt_path.exists(), (
            f"plan-prompt.txt should exist at {plan_prompt_path}"
        )
        assert implement_prompt_path.exists(), (
            f"implement-prompt.txt should exist at {implement_prompt_path}"
        )

    def test_prompt_files_have_expected_content(self, workflow_dir):
        """T005/T006: Verify extracted prompt files contain expected text."""
        plan_prompt_path = workflow_dir / "plan-prompt.txt"
        implement_prompt_path = workflow_dir / "implement-prompt.txt"

        plan_content = plan_prompt_path.read_text()
        assert "You are a planning agent" in plan_content
        assert "Task: {task}" in plan_content

        implement_content = implement_prompt_path.read_text()
        assert "You are an implementation agent" in implement_content
        assert "Plan:\n{plan}" in implement_content
        assert (
            "Previous review feedback (if any):\n{review_feedback}" in implement_content
        )

    def test_profile_references_resolved_to_provider_model(
        self, workflow_path, config_profiles
    ):
        """T007: After load_flow, profile references are resolved to provider/model."""
        flow, errors = load_flow(workflow_path, config_profiles=config_profiles)
        assert flow is not None, f"Failed to load: {errors}"

        plan_state = flow.states["plan"]
        assert plan_state.provider == "claude"
        assert plan_state.model == "claude-sonnet-4-6"

        implement_state = flow.states["implement"]
        assert implement_state.provider == "opencode"
        assert implement_state.model == "opencode-go/minimax-m2.5"

    def test_parallel_branch_profiles_resolved(self, workflow_path, config_profiles):
        """T007: Parallel branch profile references are resolved correctly."""
        flow, errors = load_flow(workflow_path, config_profiles=config_profiles)
        assert flow is not None, f"Failed to load: {errors}"

        parallel_review = flow.states["parallel_review"]
        assert len(parallel_review.branches) == 2
        assert parallel_review.branches[0].provider == "codex"
        assert parallel_review.branches[0].model == "gpt-5.4"
        assert parallel_review.branches[1].provider == "codex"
        assert parallel_review.branches[1].model == "gpt-5.4"

    def test_prompt_template_still_present_on_parallel_branches(self, workflow_path):
        """T007: Parallel branches retain inline prompt_template (not converted to prompt_file)."""
        with open(workflow_path) as f:
            data = yaml.safe_load(f)

        parallel_review = data["states"]["parallel_review"]
        assert "prompt_template" in parallel_review["branches"][0]
        assert "prompt_file" not in parallel_review["branches"][0]
        assert "prompt_template" in parallel_review["branches"][1]
        assert "prompt_file" not in parallel_review["branches"][1]

    def test_load_flow_without_config_profiles_fails_for_missing_profiles(
        self, workflow_path
    ):
        """T007: load_flow without config_profiles fails because profiles are not in YAML."""
        flow, errors = load_flow(workflow_path)
        assert flow is None
        assert len(errors) > 0
        assert any("not found" in err for err in errors)

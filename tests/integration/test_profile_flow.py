"""Integration tests for profile-based provider resolution."""

from fdsx.core.compiler import compile_flow
from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow
from tests import FIXTURES_DIR


class TestProfileFlow:
    """Tests for profile-based provider resolution via load_flow."""

    def test_load_flow_resolves_profile_to_provider_model(self):
        """load_flow resolves profile references to provider/model before Pydantic validation."""
        path = FIXTURES_DIR / "profile_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

        plan_state = flow.states["plan"]
        assert plan_state.provider == "claude"
        assert plan_state.model == "claude-sonnet-4-6"
        assert not hasattr(plan_state, "profile") or plan_state.profile is None

    def test_load_flow_resolves_multiple_profile_tasks(self):
        """Multiple tasks using profiles are all resolved correctly."""
        path = FIXTURES_DIR / "profile_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        plan_state = flow.states["plan"]
        assert plan_state.provider == "claude"
        assert plan_state.model == "claude-sonnet-4-6"

        review_state = flow.states["review"]
        assert review_state.provider == "claude"
        assert review_state.model == "claude-sonnet-4-6"

    def test_load_flow_preserves_non_profile_tasks(self):
        """Tasks without profiles are unchanged."""
        path = FIXTURES_DIR / "profile_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        implement_state = flow.states["implement"]
        assert implement_state.provider == "system"
        assert (
            implement_state.command
            == "echo 'Implementation: def hello(): print(Hello World)'"
        )

    def test_load_flow_with_config_profiles(self):
        """Config profiles are merged with workflow profiles."""
        path = FIXTURES_DIR / "profile_flow.yaml"

        config_profiles = {"config_profile": {"provider": "opencode", "model": "o4"}}
        flow, errors = load_flow(path, config_profiles=config_profiles)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

    def test_compile_flow_after_profile_resolution(self):
        """Flow compiles successfully after profile resolution."""
        path = FIXTURES_DIR / "profile_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        compiled = compile_flow(flow)
        assert compiled is not None

    def test_run_flow_with_profiles(self, tmp_path):
        """Profile-based flow executes end-to-end."""
        path = FIXTURES_DIR / "profile_flow.yaml"

        result = run_flow(path, base_dir=tmp_path)

        assert "plan" in result
        assert "implementation" in result
        assert "review" in result


class TestProfileFlowErrors:
    """Tests for profile resolution error handling."""

    def test_error_when_profile_not_found(self, tmp_path):
        """Error returned when task references non-existent profile."""
        from fdsx.core.loader import load_flow
        import yaml

        flow_dict = {
            "name": "Bad Profile Flow",
            "description": "Flow with missing profile reference",
            "start_at": "task1",
            "states": {
                "task1": {
                    "type": "task",
                    "profile": "nonexistent",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
        }
        bad_path = tmp_path / "bad_profile.yaml"
        with open(bad_path, "w") as f:
            yaml.dump(flow_dict, f)

        flow, errors = load_flow(bad_path)
        assert flow is None
        assert len(errors) == 1
        assert "not found" in errors[0]
        assert "nonexistent" in errors[0]

    def test_error_when_profile_and_provider_mutually_exclusive(self, tmp_path):
        """Error returned when task has both profile and provider."""
        from fdsx.core.loader import load_flow
        import yaml

        flow_dict = {
            "name": "XOR Flow",
            "description": "Flow with profile and provider",
            "start_at": "task1",
            "profiles": {"my_profile": {"provider": "claude", "model": "sonnet"}},
            "states": {
                "task1": {
                    "type": "task",
                    "profile": "my_profile",
                    "provider": "claude",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
        }
        xor_path = tmp_path / "xor_profile.yaml"
        with open(xor_path, "w") as f:
            yaml.dump(flow_dict, f)

        flow, errors = load_flow(xor_path)
        assert flow is None
        assert len(errors) == 1
        assert "mutually exclusive" in errors[0]

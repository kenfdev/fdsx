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
        from unittest.mock import patch

        from fdsx.providers.base import ProviderResult

        path = FIXTURES_DIR / "profile_flow.yaml"

        fake = ProviderResult(exit_code=0, stdout="mocked output", stderr="")
        with patch("fdsx.providers.claude._run_subprocess", return_value=fake):
            result = run_flow(path, base_dir=tmp_path)

        assert "plan" in result
        assert "implementation" in result
        assert "review" in result


class TestProfileFlowErrors:
    """Tests for profile resolution error handling."""

    def test_error_when_profile_not_found(self, tmp_path):
        """Error returned when task references non-existent profile."""
        import yaml

        from fdsx.core.loader import load_flow

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
        import yaml

        from fdsx.core.loader import load_flow

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


class TestValidateFlowProfileErrors:
    """T021/T022: Tests for profile errors surfaced via validate_flow()."""

    def test_validate_flow_catches_xor_violation(self, tmp_path):
        """validate_flow returns is_valid=False when task has both profile and provider."""
        import yaml

        from fdsx.core.engine.validate import validate_flow

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

        is_valid, errors, _flow_name = validate_flow(xor_path)
        assert is_valid is False
        assert len(errors) == 1
        assert "mutually exclusive" in errors[0]

    def test_validate_flow_catches_missing_profile(self, tmp_path):
        """validate_flow returns is_valid=False when task references non-existent profile."""
        import yaml

        from fdsx.core.engine.validate import validate_flow

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

        is_valid, errors, _flow_name = validate_flow(bad_path)
        assert is_valid is False
        assert len(errors) == 1
        assert "not found" in errors[0]
        assert "nonexistent" in errors[0]
        assert "task1" in errors[0]

    def test_validate_flow_error_message_format(self, tmp_path):
        """Error messages from validate_flow match spec format (state name, profile name)."""
        import yaml

        from fdsx.core.engine.validate import validate_flow

        flow_dict = {
            "name": "Missing Profile Flow",
            "description": "Flow with missing profile",
            "start_at": "my_task",
            "states": {
                "my_task": {
                    "type": "task",
                    "profile": "missing_profile",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
        }
        flow_path = tmp_path / "missing_profile.yaml"
        with open(flow_path, "w") as f:
            yaml.dump(flow_dict, f)

        is_valid, errors, _flow_name = validate_flow(flow_path)
        assert is_valid is False
        assert len(errors) == 1
        assert "my_task" in errors[0]
        assert "missing_profile" in errors[0]


class TestParallelProfileFlow:
    """Tests for profile resolution in parallel workflow branches."""

    def test_load_parallel_flow_resolves_branch_profiles(self):
        """load_flow resolves profile on each parallel branch to provider/model."""
        path = FIXTURES_DIR / "profile_parallel_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

        review_state = flow.states["review_parallel"]
        assert review_state.branches[0].provider == "claude"
        assert review_state.branches[0].model == "claude-sonnet-4-6"
        assert review_state.branches[1].provider == "codex"
        assert review_state.branches[1].model == "gpt-5.4"

    def test_compile_parallel_flow_with_profiles(self):
        """Parallel flow with profile-based branches compiles successfully."""
        path = FIXTURES_DIR / "profile_parallel_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        compiled = compile_flow(flow)
        assert compiled is not None

    def test_run_parallel_flow_with_profiles(self, tmp_path):
        """Parallel flow with profile-based branches executes end-to-end via run_flow."""
        import yaml

        flow_dict = {
            "name": "Parallel Profile Execution",
            "description": "Parallel branches resolved from profiles",
            "start_at": "review_parallel",
            "version": "1.0",
            "profiles": {
                "echo_approve": {
                    "provider": "system",
                },
                "echo_reject": {
                    "provider": "system",
                },
            },
            "states": {
                "review_parallel": {
                    "type": "parallel",
                    "branches": [
                        {
                            "profile": "echo_approve",
                            "command": 'echo "APPROVED"',
                            "extract": {
                                "strategy": ["keyword"],
                                "pattern": "APPROVED|REJECTED",
                                "result_path": "$.decision",
                            },
                            "retry": 0,
                        },
                        {
                            "profile": "echo_reject",
                            "command": 'echo "REJECTED"',
                            "extract": {
                                "strategy": ["keyword"],
                                "pattern": "APPROVED|REJECTED",
                                "result_path": "$.decision",
                            },
                            "retry": 0,
                        },
                    ],
                    "result_path": "$.reviews",
                    "end": True,
                }
            },
        }
        flow_path = tmp_path / "parallel_profile_run.yaml"
        with open(flow_path, "w") as f:
            yaml.dump(flow_dict, f)

        result = run_flow(flow_path, base_dir=tmp_path)

        assert "reviews" in result
        assert len(result["reviews"]) == 2


class TestBackwardCompatibility:
    """T032: Backward-compatibility tests confirming existing workflows without profiles work identically."""

    def test_simple_flow_loads_compiles_and_runs_without_profiles(self, tmp_path):
        """Workflows without profiles section load, compile, and run with same results."""
        path = FIXTURES_DIR / "simple_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

        compiled = compile_flow(flow)
        assert compiled is not None

        result = run_flow(path, base_dir=tmp_path)

        assert "plan" in result
        assert "implementation" in result
        assert "review" in result
        assert "Plan:" in result["plan"]
        assert "Implementation:" in result["implementation"]
        assert "Review:" in result["review"]

    def test_parallel_review_flow_loads_compiles_and_runs_without_profiles(
        self, tmp_path
    ):
        """Parallel workflows without profiles load, compile, and run correctly."""
        path = FIXTURES_DIR / "parallel_review.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

        compiled = compile_flow(flow)
        assert compiled is not None

        result = run_flow(path, base_dir=tmp_path)

        assert "reviews" in result
        assert "decision" in result


class TestProfilesOptional:
    """T034: Tests verifying profiles section is optional at all config levels."""

    def test_simple_flow_has_no_profiles_section(self):
        """simple_flow.yaml has no profiles key."""
        path = FIXTURES_DIR / "simple_flow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert flow.profiles is None

    def test_parallel_review_flow_has_no_profiles_section(self):
        """parallel_review.yaml has no profiles key."""
        path = FIXTURES_DIR / "parallel_review.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert flow.profiles is None

    def test_workflow_without_profiles_loads_without_errors_or_warnings(self, tmp_path):
        """Minimal workflow without profiles loads without errors or deprecation warnings."""
        import warnings

        import yaml

        flow_dict = {
            "name": "Minimal No Profile Flow",
            "description": "Workflow with no profiles section",
            "start_at": "task1",
            "version": "1.0",
            "states": {
                "task1": {
                    "type": "task",
                    "provider": "system",
                    "command": "echo 'hello'",
                    "result_path": "$.output",
                    "next": "end",
                },
                "end": {
                    "type": "pass",
                    "end": True,
                },
            },
        }
        flow_path = tmp_path / "no_profiles_flow.yaml"
        with open(flow_path, "w") as f:
            yaml.dump(flow_dict, f)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            flow, errors = load_flow(flow_path)

            assert flow is not None, f"Failed to load: {errors}"
            assert flow.profiles is None

            deprecation_warnings = [
                x for x in w if issubclass(x.category, DeprecationWarning)
            ]
            assert len(deprecation_warnings) == 0, (
                f"Unexpected deprecation warnings: {deprecation_warnings}"
            )


class TestCascadingProfileOverrides:
    """T018: Integration tests for workflow-level profile override of config-level profile."""

    def test_workflow_profile_overrides_config_profile(self, tmp_path):
        """Workflow-level profile definition overrides config-level profile (full replacement, not deep merge)."""
        import yaml

        from fdsx.core.loader import load_flow

        flow_dict = {
            "name": "Override Flow",
            "description": "Workflow profile overrides config profile",
            "start_at": "task1",
            "profiles": {"smart_guy": {"provider": "codex", "model": "gpt"}},
            "states": {
                "task1": {
                    "type": "task",
                    "profile": "smart_guy",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                    "next": "end",
                },
                "end": {
                    "type": "pass",
                    "end": True,
                },
            },
        }
        flow_path = tmp_path / "override_flow.yaml"
        with open(flow_path, "w") as f:
            yaml.dump(flow_dict, f)

        config_profiles = {"smart_guy": {"provider": "claude", "model": "sonnet"}}
        flow, errors = load_flow(flow_path, config_profiles=config_profiles)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

        task_state = flow.states["task1"]
        assert task_state.provider == "codex"
        assert task_state.model == "gpt"

    def test_config_profile_available_when_not_in_workflow(self, tmp_path):
        """Config-level profile is used when workflow doesn't define it."""
        import yaml

        from fdsx.core.loader import load_flow

        flow_dict = {
            "name": "Config Only Flow",
            "description": "Uses config-level profile",
            "start_at": "task1",
            "profiles": {"other_profile": {"provider": "opencode", "model": "o1"}},
            "states": {
                "task1": {
                    "type": "task",
                    "profile": "config_profile",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                    "next": "end",
                },
                "end": {
                    "type": "pass",
                    "end": True,
                },
            },
        }
        flow_path = tmp_path / "config_only_flow.yaml"
        with open(flow_path, "w") as f:
            yaml.dump(flow_dict, f)

        config_profiles = {"config_profile": {"provider": "claude", "model": "sonnet"}}
        flow, errors = load_flow(flow_path, config_profiles=config_profiles)
        assert flow is not None, f"Failed to load: {errors}"
        assert len(errors) == 0

        task_state = flow.states["task1"]
        assert task_state.provider == "claude"
        assert task_state.model == "sonnet"

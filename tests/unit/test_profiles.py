"""Unit tests for profile resolution."""

from fdsx.core.profiles import merge_profiles, resolve_profiles_in_flow


class TestMergeProfiles:
    """Tests for merge_profiles function."""

    def test_both_none_returns_empty_dict(self):
        """When both inputs are None, returns empty dict."""
        result = merge_profiles(None, None)
        assert result == {}

    def test_config_only_returns_config_copy(self):
        """When only config_profiles provided, returns copy of it."""
        config = {"profile1": {"provider": "claude", "model": "sonnet"}}
        result = merge_profiles(config, None)
        assert result == config

    def test_workflow_only_returns_workflow_copy(self):
        """When only workflow_profiles provided, returns copy of it."""
        workflow = {"profile2": {"provider": "codex", "model": "gpt"}}
        result = merge_profiles(None, workflow)
        assert result == workflow

    def test_workflow_overrides_config(self):
        """Workflow-level profiles override config-level with same name."""
        config = {"shared": {"provider": "claude", "model": "sonnet"}}
        workflow = {"shared": {"provider": "codex", "model": "gpt"}}
        result = merge_profiles(config, workflow)
        assert result["shared"] == {"provider": "codex", "model": "gpt"}

    def test_both_merged(self):
        """Both config and workflow profiles are in result."""
        config = {"from_config": {"provider": "claude", "model": "sonnet"}}
        workflow = {"from_workflow": {"provider": "codex", "model": "gpt"}}
        result = merge_profiles(config, workflow)
        assert result == {
            "from_config": {"provider": "claude", "model": "sonnet"},
            "from_workflow": {"provider": "codex", "model": "gpt"},
        }

    def test_does_not_mutate_inputs(self):
        """Original dicts are not modified."""
        config = {"profile1": {"provider": "claude"}}
        workflow = {"profile2": {"provider": "codex"}}
        merge_profiles(config, workflow)
        assert "profile2" not in config
        assert "profile1" not in workflow


class TestResolveProfilesInFlow:
    """Tests for resolve_profiles_in_flow function."""

    def _make_flow(self, states, profiles=None):
        """Helper to create a minimal flow dict."""
        data = {
            "name": "Test Flow",
            "description": "Test flow",
            "start_at": "task1",
            "states": states,
        }
        if profiles is not None:
            data["profiles"] = profiles
        return data

    def test_resolves_profile_to_provider_model(self):
        """Profile reference is resolved to provider and model."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "profile": "smart_guy",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet-4-6"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        assert data["states"]["task1"]["provider"] == "claude"
        assert data["states"]["task1"]["model"] == "sonnet-4-6"
        assert "profile" not in data["states"]["task1"]

    def test_resolves_profile_extra_fields_to_provider_options(self):
        """Extra fields in profile become provider_options."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "profile": "smart_guy",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
            profiles={
                "smart_guy": {
                    "provider": "claude",
                    "model": "sonnet-4-6",
                    "permission_mode": "plan",
                }
            },
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        assert data["states"]["task1"]["provider_options"] == {
            "permission_mode": "plan"
        }

    def test_resolves_profile_without_extras_no_provider_options(self):
        """Profile without extra fields does not set empty provider_options."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "profile": "simple",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
            profiles={"simple": {"provider": "claude", "model": "sonnet"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        assert "provider_options" not in data["states"]["task1"]

    def test_error_when_profile_and_provider_both_present(self):
        """Task with both profile and provider returns error."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "profile": "smart_guy",
                    "provider": "claude",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert len(errors) == 1
        assert "mutually exclusive" in errors[0]
        assert "profile" in data["states"]["task1"]

    def test_error_when_profile_and_model_both_present(self):
        """Task with both profile and model returns error."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "profile": "smart_guy",
                    "model": "sonnet",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert len(errors) == 1
        assert "mutually exclusive" in errors[0]

    def test_error_when_profile_not_found(self):
        """Reference to non-existent profile returns error."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "profile": "nonexistent",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert len(errors) == 1
        assert "not found" in errors[0]
        assert "nonexistent" in errors[0]
        assert "task1" in errors[0]
        assert "workflow YAML" in errors[0] or ".fdsx/config.yaml" in errors[0]

    def test_noop_when_no_profile(self):
        """Task without profile is left unchanged."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "provider": "system",
                    "command": "echo hi",
                    "result_path": "$.output",
                }
            }
        )
        original = dict(data["states"]["task1"])
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        assert data["states"]["task1"] == original

    def test_ignores_non_task_states(self):
        """Choice and parallel states are not processed."""
        data = self._make_flow(
            {
                "choice1": {
                    "type": "choice",
                    "profile": "smart_guy",
                    "choices": [],
                },
                "parallel1": {
                    "type": "parallel",
                    "profile": "smart_guy",
                    "branches": [],
                    "result_path": "$.output",
                },
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        assert "profile" in data["states"]["choice1"]
        assert "profile" in data["states"]["parallel1"]

    def test_merges_config_and_workflow_profiles(self):
        """Config profiles are merged with workflow profiles."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "profile": "config_profile",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
            profiles={"workflow_profile": {"provider": "codex"}},
        )
        config_profiles = {"config_profile": {"provider": "claude", "model": "sonnet"}}
        data, errors = resolve_profiles_in_flow(data, config_profiles)

        assert errors == []
        assert data["states"]["task1"]["provider"] == "claude"
        assert data["states"]["task1"]["model"] == "sonnet"

    def test_workflow_profile_overrides_config_profile(self):
        """Workflow-level profile overrides config-level with same name."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "profile": "shared",
                    "prompt_template": "Hello",
                    "result_path": "$.output",
                }
            },
            profiles={"shared": {"provider": "codex", "model": "gpt"}},
        )
        config_profiles = {"shared": {"provider": "claude", "model": "sonnet"}}
        data, errors = resolve_profiles_in_flow(data, config_profiles)

        assert errors == []
        assert data["states"]["task1"]["provider"] == "codex"
        assert data["states"]["task1"]["model"] == "gpt"

    def test_malformed_profiles_returns_error(self):
        """Malformed profiles (list instead of mapping) returns error."""
        data = {
            "name": "Test Flow",
            "description": "Test flow",
            "start_at": "step1",
            "states": {"step1": {"type": "task", "profile": "foo"}},
            "profiles": ["not", "a", "mapping"],
        }
        _result, errors = resolve_profiles_in_flow(data)
        assert len(errors) == 1
        assert "mapping" in errors[0]

    def test_multiple_tasks_resolved(self):
        """Multiple tasks with profiles are all resolved."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "profile": "profile_a",
                    "prompt_template": "Task 1",
                    "result_path": "$.out1",
                },
                "task2": {
                    "type": "task",
                    "profile": "profile_b",
                    "prompt_template": "Task 2",
                    "result_path": "$.out2",
                },
            },
            profiles={
                "profile_a": {"provider": "claude", "model": "sonnet"},
                "profile_b": {"provider": "codex", "model": "gpt"},
            },
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        assert data["states"]["task1"]["provider"] == "claude"
        assert data["states"]["task1"]["model"] == "sonnet"
        assert data["states"]["task2"]["provider"] == "codex"
        assert data["states"]["task2"]["model"] == "gpt"


class TestParallelBranchProfileResolution(TestResolveProfilesInFlow):
    """Tests for profile resolution in parallel state branches."""

    def test_parallel_branch_profile_resolved(self):
        """A parallel state branch with profile gets resolved to provider/model."""
        data = self._make_flow(
            {
                "review": {
                    "type": "parallel",
                    "branches": [
                        {
                            "profile": "smart_guy",
                            "prompt_template": "Review code",
                        }
                    ],
                    "result_path": "$.reviews",
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet-4-6"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        branch = data["states"]["review"]["branches"][0]
        assert branch["provider"] == "claude"
        assert branch["model"] == "sonnet-4-6"
        assert "profile" not in branch

    def test_parallel_branches_different_profiles(self):
        """Each branch in a parallel state can use a different profile."""
        data = self._make_flow(
            {
                "review": {
                    "type": "parallel",
                    "branches": [
                        {
                            "profile": "profile_a",
                            "prompt_template": "Branch A",
                        },
                        {
                            "profile": "profile_b",
                            "prompt_template": "Branch B",
                        },
                    ],
                    "result_path": "$.reviews",
                }
            },
            profiles={
                "profile_a": {"provider": "claude", "model": "sonnet"},
                "profile_b": {"provider": "codex", "model": "gpt"},
            },
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        assert data["states"]["review"]["branches"][0]["provider"] == "claude"
        assert data["states"]["review"]["branches"][0]["model"] == "sonnet"
        assert data["states"]["review"]["branches"][1]["provider"] == "codex"
        assert data["states"]["review"]["branches"][1]["model"] == "gpt"

    def test_parallel_branch_xor_validation(self):
        """Branch with both profile and provider returns error."""
        data = self._make_flow(
            {
                "review": {
                    "type": "parallel",
                    "branches": [
                        {
                            "profile": "smart_guy",
                            "provider": "claude",
                            "prompt_template": "Review code",
                        }
                    ],
                    "result_path": "$.reviews",
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert len(errors) == 1
        assert "mutually exclusive" in errors[0]
        assert "branch 0" in errors[0]

    def test_parallel_branch_missing_profile(self):
        """Branch referencing nonexistent profile returns error."""
        data = self._make_flow(
            {
                "review": {
                    "type": "parallel",
                    "branches": [
                        {
                            "profile": "nonexistent",
                            "prompt_template": "Review code",
                        }
                    ],
                    "result_path": "$.reviews",
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert len(errors) == 1
        assert "not found" in errors[0]
        assert "nonexistent" in errors[0]
        assert "branch 0" in errors[0]

    def test_parallel_branch_extra_fields_to_provider_options(self):
        """Extra profile fields become provider_options on branch."""
        data = self._make_flow(
            {
                "review": {
                    "type": "parallel",
                    "branches": [
                        {
                            "profile": "smart_guy",
                            "prompt_template": "Review code",
                        }
                    ],
                    "result_path": "$.reviews",
                }
            },
            profiles={
                "smart_guy": {
                    "provider": "claude",
                    "model": "sonnet",
                    "permission_mode": "plan",
                }
            },
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        branch = data["states"]["review"]["branches"][0]
        assert branch["provider_options"] == {"permission_mode": "plan"}


class TestCascadingProfileOverrides:
    """T014: Tests for 3-level cascading profile overrides (global -> project -> workflow)."""

    def test_workflow_overrides_project_level(self):
        """When both project-config and workflow define same profile name, workflow wins (full replacement)."""
        config_profiles = {"shared": {"provider": "claude", "model": "sonnet"}}
        workflow_profiles = {"shared": {"provider": "codex", "model": "gpt"}}
        result = merge_profiles(config_profiles, workflow_profiles)
        assert result["shared"] == {"provider": "codex", "model": "gpt"}

    def test_project_overrides_global_level(self):
        """Simulate 3-level merge: chaining merge_profiles(global, project) then (result, workflow)."""
        global_profiles = {"fast": {"provider": "claude", "model": "haiku"}}
        project_profiles = {"fast": {"provider": "codex", "model": "gpt"}}
        config_merged = merge_profiles(global_profiles, project_profiles)
        assert config_merged["fast"] == {"provider": "codex", "model": "gpt"}

    def test_global_only_profile_accessible(self):
        """A profile defined only at global level is available after merging all 3 levels."""
        global_profiles = {"global_profile": {"provider": "claude", "model": "sonnet"}}
        project_profiles = {"project_profile": {"provider": "opencode", "model": "o1"}}
        workflow_profiles = {"workflow_profile": {"provider": "codex", "model": "gpt"}}
        config_merged = merge_profiles(global_profiles, project_profiles)
        result = merge_profiles(config_merged, workflow_profiles)
        assert "global_profile" in result
        assert result["global_profile"] == {"provider": "claude", "model": "sonnet"}
        assert "project_profile" in result
        assert "workflow_profile" in result

    def test_full_replacement_not_deep_merge(self):
        """When workflow overrides a profile, it replaces the entire dict (fields from lower level are NOT inherited)."""
        config_profiles = {
            "smart": {"provider": "claude", "model": "sonnet", "extra": "field"}
        }
        workflow_profiles = {"smart": {"provider": "codex", "model": "gpt"}}
        result = merge_profiles(config_profiles, workflow_profiles)
        assert result["smart"] == {"provider": "codex", "model": "gpt"}
        assert "extra" not in result["smart"]

    def test_three_level_cascade_full_override(self):
        """Full 3-level cascade: global -> project -> workflow, workflow completely replaces project profile."""
        global_profiles = {"profile": {"provider": "global", "model": "global-model"}}
        project_profiles = {
            "profile": {"provider": "project", "model": "project-model"}
        }
        workflow_profiles = {
            "profile": {"provider": "workflow", "model": "workflow-model"}
        }
        config_merged = merge_profiles(global_profiles, project_profiles)
        result = merge_profiles(config_merged, workflow_profiles)
        assert result["profile"] == {"provider": "workflow", "model": "workflow-model"}


class TestExtractFallbackProfileResolution:
    """Tests for extract.fallback profile resolution."""

    def _make_flow(self, states, profiles=None):
        """Helper to create a minimal flow dict."""
        data = {
            "name": "Test Flow",
            "description": "Test flow",
            "start_at": "task1",
            "states": states,
        }
        if profiles is not None:
            data["profiles"] = profiles
        return data

    def test_extract_fallback_profile_resolved(self):
        """Task with extract.fallback.profile gets fallback resolved to provider."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "provider": "claude",
                    "prompt_template": "Classify this",
                    "result_path": "$.output",
                    "extract": {
                        "strategy": ["json"],
                        "pattern": ".*",
                        "result_path": "$.extracted",
                        "fallback": {
                            "profile": "smart_guy",
                            "prompt": "Fallback classify",
                        },
                    },
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet-4-6"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        fallback = data["states"]["task1"]["extract"]["fallback"]
        assert fallback["provider"] == "claude"
        assert "profile" not in fallback

    def test_extract_fallback_xor_profile_and_provider(self):
        """Fallback with both profile and provider returns XOR error."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "provider": "claude",
                    "prompt_template": "Classify this",
                    "result_path": "$.output",
                    "extract": {
                        "strategy": ["json"],
                        "pattern": ".*",
                        "result_path": "$.extracted",
                        "fallback": {
                            "profile": "smart_guy",
                            "provider": "claude",
                            "prompt": "Fallback classify",
                        },
                    },
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet-4-6"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert len(errors) == 1
        assert "mutually exclusive" in errors[0]
        assert "extract.fallback" in errors[0]

    def test_extract_fallback_missing_profile(self):
        """Fallback referencing nonexistent profile returns error."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "provider": "claude",
                    "prompt_template": "Classify this",
                    "result_path": "$.output",
                    "extract": {
                        "strategy": ["json"],
                        "pattern": ".*",
                        "result_path": "$.extracted",
                        "fallback": {
                            "profile": "nonexistent",
                            "prompt": "Fallback classify",
                        },
                    },
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet-4-6"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert len(errors) == 1
        assert "not found" in errors[0]
        assert "nonexistent" in errors[0]

    def test_extract_fallback_no_profile_unchanged(self):
        """Fallback without profile is left unchanged."""
        data = self._make_flow(
            {
                "task1": {
                    "type": "task",
                    "provider": "claude",
                    "prompt_template": "Classify this",
                    "result_path": "$.output",
                    "extract": {
                        "strategy": ["json"],
                        "pattern": ".*",
                        "result_path": "$.extracted",
                        "fallback": {
                            "provider": "claude",
                            "prompt": "Fallback classify",
                        },
                    },
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet-4-6"}},
        )
        original = dict(data["states"]["task1"]["extract"]["fallback"])
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        assert data["states"]["task1"]["extract"]["fallback"] == original

    def test_parallel_branch_extract_fallback_profile_resolved(self):
        """Parallel branch with extract.fallback.profile gets fallback resolved."""
        data = self._make_flow(
            {
                "review": {
                    "type": "parallel",
                    "branches": [
                        {
                            "provider": "claude",
                            "prompt_template": "Review code",
                            "extract": {
                                "strategy": ["json"],
                                "pattern": ".*",
                                "result_path": "$.extracted",
                                "fallback": {
                                    "profile": "smart_guy",
                                    "prompt": "Fallback classify",
                                },
                            },
                        }
                    ],
                    "result_path": "$.reviews",
                }
            },
            profiles={"smart_guy": {"provider": "claude", "model": "sonnet-4-6"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        fallback = data["states"]["review"]["branches"][0]["extract"]["fallback"]
        assert fallback["provider"] == "claude"
        assert "profile" not in fallback


class TestMapIteratorProfileResolution(TestResolveProfilesInFlow):
    """Tests for profile resolution in map iterator states."""

    def test_map_iterator_profile_resolves_to_provider_model(self):
        """Map iterator state with profile gets resolved to provider/model."""
        data = self._make_flow(
            {
                "process": {
                    "type": "map",
                    "items_path": "$.items",
                    "iterator": {
                        "states": [
                            {
                                "name": "step1",
                                "type": "task",
                                "profile": "fast",
                                "prompt_template": "Process item",
                                "result_path": "$.result",
                            }
                        ]
                    },
                    "result_path": "$.results",
                }
            },
            profiles={"fast": {"provider": "claude", "model": "haiku"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        iter_state = data["states"]["process"]["iterator"]["states"][0]
        assert iter_state["provider"] == "claude"
        assert iter_state["model"] == "haiku"
        assert "profile" not in iter_state

    def test_map_iterator_profile_xor_explicit_provider(self):
        """Iterator state with both profile and provider returns XOR error."""
        data = self._make_flow(
            {
                "process": {
                    "type": "map",
                    "items_path": "$.items",
                    "iterator": {
                        "states": [
                            {
                                "name": "step1",
                                "type": "task",
                                "profile": "fast",
                                "provider": "claude",
                                "prompt_template": "Process item",
                                "result_path": "$.result",
                            }
                        ]
                    },
                    "result_path": "$.results",
                }
            },
            profiles={"fast": {"provider": "claude", "model": "haiku"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert len(errors) == 1
        assert "mutually exclusive" in errors[0]
        assert "step1" in errors[0]

    def test_map_iterator_extract_fallback_profile_resolved(self):
        """Iterator state with extract.fallback.profile gets fallback resolved."""
        data = self._make_flow(
            {
                "process": {
                    "type": "map",
                    "items_path": "$.items",
                    "iterator": {
                        "states": [
                            {
                                "name": "step1",
                                "type": "task",
                                "provider": "claude",
                                "prompt_template": "Process item",
                                "result_path": "$.result",
                                "extract": {
                                    "strategy": ["json"],
                                    "pattern": ".*",
                                    "result_path": "$.extracted",
                                    "fallback": {
                                        "profile": "smart",
                                        "prompt": "Fallback prompt",
                                    },
                                },
                            }
                        ]
                    },
                    "result_path": "$.results",
                }
            },
            profiles={"smart": {"provider": "claude", "model": "sonnet"}},
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        fallback = data["states"]["process"]["iterator"]["states"][0]["extract"][
            "fallback"
        ]
        assert fallback["provider"] == "claude"
        assert "profile" not in fallback
        assert "step1" in repr(data)  # iter state name present in structure

    def test_map_iterator_multiple_states_all_resolved(self):
        """Multiple iterator states each with different profiles are all resolved."""
        data = self._make_flow(
            {
                "process": {
                    "type": "map",
                    "items_path": "$.items",
                    "iterator": {
                        "states": [
                            {
                                "name": "step1",
                                "type": "task",
                                "profile": "profile_a",
                                "prompt_template": "Step 1",
                                "result_path": "$.r1",
                            },
                            {
                                "name": "step2",
                                "type": "task",
                                "profile": "profile_b",
                                "prompt_template": "Step 2",
                                "result_path": "$.r2",
                            },
                        ]
                    },
                    "result_path": "$.results",
                }
            },
            profiles={
                "profile_a": {"provider": "claude", "model": "sonnet"},
                "profile_b": {"provider": "codex", "model": "gpt"},
            },
        )
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        states = data["states"]["process"]["iterator"]["states"]
        assert states[0]["provider"] == "claude"
        assert states[0]["model"] == "sonnet"
        assert "profile" not in states[0]
        assert states[1]["provider"] == "codex"
        assert states[1]["model"] == "gpt"
        assert "profile" not in states[1]

    def test_map_iterator_no_profile_unchanged(self):
        """Iterator state without profile is left unchanged."""
        data = self._make_flow(
            {
                "process": {
                    "type": "map",
                    "items_path": "$.items",
                    "iterator": {
                        "states": [
                            {
                                "name": "step1",
                                "type": "task",
                                "provider": "claude",
                                "prompt_template": "Process item",
                                "result_path": "$.result",
                            }
                        ]
                    },
                    "result_path": "$.results",
                }
            },
        )
        original = dict(data["states"]["process"]["iterator"]["states"][0])
        data, errors = resolve_profiles_in_flow(data)

        assert errors == []
        assert data["states"]["process"]["iterator"]["states"][0] == original

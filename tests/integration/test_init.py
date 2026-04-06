"""Integration tests for the init module (src/fdsx/core/init.py).

Covers template discovery, config.yaml content validation, selective template
copying, and atomic generation (interrupt safety).  Does not duplicate coverage
already in tests/integration/test_auto_init.py or
tests/integration/test_scaffold_gitignore.py.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from fdsx.core.init import (
    _resolve_xdg_templates_dir,
    discover_templates,
    generate_config_yaml,
    scaffold,
)
from fdsx.models.init import InitConfig, ProviderSelection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _known_builtin_template_names() -> set[str]:
    return {"full-impl", "simple-impl", "self-improve"}


# ---------------------------------------------------------------------------
# TestDiscoverTemplates
# ---------------------------------------------------------------------------


class TestDiscoverTemplates:
    def test_returns_builtin_templates(self):
        """discover_templates() returns known builtins with source='builtin'."""
        templates = discover_templates()
        builtin_names = {t.name for t in templates if t.source == "builtin"}
        assert builtin_names == _known_builtin_template_names()

    def test_builtin_templates_have_workflow_yaml(self):
        """Each builtin template has a workflow.yaml file at its path."""
        templates = discover_templates()
        for t in templates:
            if t.source == "builtin":
                assert (t.path / "workflow.yaml").is_file(), (
                    f"Builtin template {t.name!r} is missing workflow.yaml"
                )

    def test_discovers_user_templates_from_xdg_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """User template dir set via XDG_CONFIG_HOME is scanned with source='user'."""
        xdg_templates = tmp_path / "user_templates" / "fdsx" / "templates" / "workflows"
        xdg_templates.mkdir(parents=True)
        (xdg_templates / "my-workflow").mkdir()
        (xdg_templates / "my-workflow" / "workflow.yaml").write_text(
            "name: my-workflow\n"
        )

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr(
            "fdsx.core.init._resolve_xdg_templates_dir",
            lambda: xdg_templates,
        )

        templates = discover_templates()
        user_names = {t.name for t in templates if t.source == "user"}
        assert "my-workflow" in user_names

    def test_ignores_non_template_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Subdirectories without workflow.yaml are skipped."""
        user_templates_root = tmp_path / "user_templates"
        user_templates_root.mkdir()
        (user_templates_root / "not-a-template").mkdir()
        (user_templates_root / "also-invalid").mkdir()
        (user_templates_root / "also-invalid" / "README.txt").write_text("doc")

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr(
            "fdsx.core.init._resolve_xdg_templates_dir",
            lambda: user_templates_root,
        )

        templates = discover_templates()
        user_names = {t.name for t in templates if t.source == "user"}
        assert "not-a-template" not in user_names
        assert "also-invalid" not in user_names

    def test_no_user_templates_when_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When XDG dir does not exist, only builtin templates are returned."""
        nonexistent = tmp_path / "does_not_exist"
        monkeypatch.setattr(
            "fdsx.core.init._resolve_xdg_templates_dir",
            lambda: nonexistent,
        )

        templates = discover_templates()
        assert all(t.source == "builtin" for t in templates)

    def test_resolves_xdg_config_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """_resolve_xdg_templates_dir respects XDG_CONFIG_HOME."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        result = _resolve_xdg_templates_dir()
        expected = tmp_path / "fdsx" / "templates" / "workflows"
        assert result == expected

    def test_resolves_default_xdg_when_unset(self, monkeypatch: pytest.MonkeyPatch):
        """When XDG_CONFIG_HOME is unset, defaults to ~/.config/fdsx/templates/workflows."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        result = _resolve_xdg_templates_dir()
        expected = Path.home() / ".config" / "fdsx" / "templates" / "workflows"
        assert result == expected


# ---------------------------------------------------------------------------
# TestConfigYamlContent
# ---------------------------------------------------------------------------


class TestConfigYamlContent:
    def _make_profile_assignments(self):
        return {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }

    def test_single_provider_profiles_and_options(self):
        """claude-only config: all 5 profiles use claude provider and model."""
        profile_assignments = self._make_profile_assignments()
        providers = [ProviderSelection(provider="claude", model="claude-sonnet-4-7")]
        config_yaml = generate_config_yaml(profile_assignments, providers)
        parsed = yaml.safe_load(config_yaml)

        for profile_name in ["smarty", "doer", "specialist", "generalist", "behemoth"]:
            assert profile_name in parsed["profiles"]
            assert parsed["profiles"][profile_name]["provider"] == "claude"
            assert parsed["profiles"][profile_name]["model"] == "claude-sonnet-4-7"
        assert parsed["providers"]["claude"]["dangerously_skip_permissions"] is True

    def test_multiple_providers_all_profiles(self):
        """claude + codex: both providers sections present."""
        profile_assignments = {
            "smarty": ProviderSelection(provider="claude", model="claude-sonnet-4-7"),
            "doer": ProviderSelection(provider="codex", model="codex-model"),
            "specialist": ProviderSelection(provider="claude", model="claude-opus-4"),
            "generalist": ProviderSelection(
                provider="claude", model="claude-sonnet-4-7"
            ),
            "behemoth": ProviderSelection(provider="claude", model="claude-sonnet-4-7"),
        }
        providers = [
            ProviderSelection(provider="claude", model="claude-sonnet-4-7"),
            ProviderSelection(provider="codex", model="codex-model"),
        ]
        config_yaml = generate_config_yaml(profile_assignments, providers)
        parsed = yaml.safe_load(config_yaml)

        assert "claude" in parsed["providers"]
        assert "codex" in parsed["providers"]

    def test_all_providers_max_permissions(self):
        """All four providers get their respective max-permission keys."""
        profile_assignments = {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }
        providers = [
            ProviderSelection(provider="claude", model="claude-sonnet-4-7"),
            ProviderSelection(provider="codex", model="codex-model"),
            ProviderSelection(provider="gemini", model="gemini-model"),
            ProviderSelection(provider="opencode", model="opencode-model"),
        ]
        config_yaml = generate_config_yaml(profile_assignments, providers)
        parsed = yaml.safe_load(config_yaml)

        assert parsed["providers"]["claude"]["dangerously_skip_permissions"] is True
        assert (
            parsed["providers"]["codex"]["dangerously_bypass_approvals_and_sandbox"]
            is True
        )
        assert parsed["providers"]["gemini"]["yolo"] is True
        assert parsed["providers"]["opencode"]["permission"] == "auto-edit"

    def test_generated_yaml_is_parseable(self):
        """Round-trip: generate -> yaml.safe_load returns a valid dict."""
        profile_assignments = self._make_profile_assignments()
        providers = [ProviderSelection(provider="claude", model="claude-sonnet-4-7")]
        config_yaml = generate_config_yaml(profile_assignments, providers)
        parsed = yaml.safe_load(config_yaml)
        assert isinstance(parsed, dict)
        assert "profiles" in parsed

    def test_no_extra_providers_in_output(self):
        """Only selected providers appear in the providers section."""
        profile_assignments = self._make_profile_assignments()
        providers = [ProviderSelection(provider="claude", model="claude-sonnet-4-7")]
        config_yaml = generate_config_yaml(profile_assignments, providers)
        parsed = yaml.safe_load(config_yaml)
        assert set(parsed["providers"].keys()) == {"claude"}


# ---------------------------------------------------------------------------
# TestConfigTemplateScaffold
# ---------------------------------------------------------------------------


class TestConfigTemplateScaffold:
    """Verify generated config.yaml contains the task_splitter scaffold block."""

    def _make_profile_assignments(self):
        return {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }

    def test_generated_config_contains_task_splitter_block(self):
        config_yaml = generate_config_yaml(
            self._make_profile_assignments(),
            [ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
        )
        assert "task_splitter:" in config_yaml
        assert "  profile: generalist" in config_yaml

    def test_generated_config_contains_extra_instructions_examples(self):
        config_yaml = generate_config_yaml(
            self._make_profile_assignments(),
            [ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
        )
        assert "extra_instructions" in config_yaml
        assert "Split into smaller tasks" in config_yaml
        assert "Prefer fewer, larger tasks" in config_yaml
        assert "shared/ directory" in config_yaml

    def test_scaffold_generates_config_with_extra_instructions_examples(
        self, tmp_path: Path
    ):
        """scaffold() produces config.yaml on disk with extra_instructions examples."""
        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=[],
            profile_assignments=self._make_profile_assignments(),
        )
        scaffold(tmp_path, config)

        config_path = tmp_path / ".fdsx" / "config.yaml"
        assert config_path.is_file(), "scaffold() must create .fdsx/config.yaml"
        content = config_path.read_text()

        assert "extra_instructions" in content
        assert "Split into smaller tasks" in content
        assert "Prefer fewer, larger tasks" in content
        assert "shared/ directory" in content


# ---------------------------------------------------------------------------
# TestSelectiveTemplateCopy
# ---------------------------------------------------------------------------


class TestSelectiveTemplateCopy:
    def _make_profile_assignments(self):
        return {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }

    def test_only_selected_templates_copied(self, tmp_path: Path):
        """Only the templates passed to InitConfig are created in .fdsx/workflows/."""
        all_templates = discover_templates()
        selected = [t for t in all_templates if t.name == "full-impl"]

        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=selected,
            profile_assignments=self._make_profile_assignments(),
        )
        scaffold(tmp_path, config)

        workflows_dir = tmp_path / ".fdsx" / "workflows"
        created_workflow_names = {d.name for d in workflows_dir.iterdir() if d.is_dir()}
        assert created_workflow_names == {"full-impl"}

    def test_no_templates_creates_empty_workflows_dir(self, tmp_path: Path):
        """Empty templates list still creates workflows/ and config.yaml."""
        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=[],
            profile_assignments=self._make_profile_assignments(),
        )
        result = scaffold(tmp_path, config)

        assert (tmp_path / ".fdsx" / "workflows").is_dir()
        assert ".fdsx/config.yaml" in result.created
        workflows_dir = tmp_path / ".fdsx" / "workflows"
        assert list(workflows_dir.iterdir()) == []

    def test_template_files_content_matches_source(self, tmp_path: Path):
        """Files copied from a selected template have identical content to source."""
        all_templates = discover_templates()
        selected = [t for t in all_templates if t.name == "full-impl"]
        assert selected, "full-impl must be available as a builtin template"

        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=selected,
            profile_assignments=self._make_profile_assignments(),
        )
        scaffold(tmp_path, config)

        source_dir = selected[0].path
        dest_dir = tmp_path / ".fdsx" / "workflows" / "full-impl"

        for source_file in source_dir.iterdir():
            if source_file.name == "__init__.py" or not source_file.is_file():
                continue
            dest_file = dest_dir / source_file.name
            assert dest_file.read_text() == source_file.read_text(), (
                f"Content mismatch for {source_file.name}"
            )


# ---------------------------------------------------------------------------
# TestAtomicGeneration
# ---------------------------------------------------------------------------


class TestAtomicGeneration:
    def _make_profile_assignments(self):
        return {
            name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
            for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
        }

    def test_no_partial_fdsx_on_write_failure(self, tmp_path: Path):
        """If Path.write_text fails mid-template-copy, no .fdsx/ dir remains."""
        all_templates = discover_templates()
        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=all_templates,
            profile_assignments=self._make_profile_assignments(),
        )

        original_write = Path.write_text

        def bad_write_text(self, data):
            if "workflow.yaml" in str(self):
                raise OSError("simulated write failure")
            return original_write(self, data)

        with patch.object(Path, "write_text", bad_write_text), pytest.raises(OSError):
            scaffold(tmp_path, config)

        assert not (tmp_path / ".fdsx").exists()
        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".fdsx.tmp.")]
        assert leftover == []

    def test_successful_scaffold_no_temp_dirs(self, tmp_path: Path):
        """After a successful scaffold, no .fdsx.tmp.* directories remain."""
        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=discover_templates(),
            profile_assignments=self._make_profile_assignments(),
        )
        scaffold(tmp_path, config)

        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".fdsx.tmp.")]
        assert leftover == []

    def test_existing_fdsx_not_atomic(self, tmp_path: Path):
        """Scaffolding into an existing .fdsx/ writes files incrementally (non-atomic)."""
        (tmp_path / ".fdsx").mkdir()

        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=[],
            profile_assignments=self._make_profile_assignments(),
        )

        with patch(
            "fdsx.core.init.Path.rename",
            side_effect=AssertionError("rename should not be called"),
        ):
            result = scaffold(tmp_path, config)

        assert result.created
        assert (tmp_path / ".fdsx" / "config.yaml").exists()

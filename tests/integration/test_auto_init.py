from unittest.mock import patch

import pytest
import yaml

from fdsx.core.init import (
    check_conflicts,
    discover_templates,
    generate_config_yaml,
    needs_init,
    scaffold,
)
from fdsx.models.init import InitConfig, ProviderSelection


def _make_profile_assignments():
    return {
        name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
        for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
    }


class TestAutoInit:
    def test_needs_init_true_when_missing(self, tmp_path):
        result = needs_init(tmp_path)
        assert result is True

    def test_needs_init_false_when_exists(self, tmp_path):
        (tmp_path / ".fdsx").mkdir()
        result = needs_init(tmp_path)
        assert result is False

    def test_needs_init_false_when_partial(self, tmp_path):
        (tmp_path / ".fdsx").mkdir()
        result = needs_init(tmp_path)
        assert result is False

    def test_generate_config_yaml_valid(self):
        profile_assignments = _make_profile_assignments()
        providers = [ProviderSelection(provider="claude", model="claude-sonnet-4-7")]
        config_yaml = generate_config_yaml(profile_assignments, providers)
        parsed = yaml.safe_load(config_yaml)
        assert isinstance(parsed, dict)
        assert "profiles" in parsed
        assert "providers" in parsed

    def test_scaffold_creates_complete_structure(self, tmp_path):
        templates = discover_templates()
        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=templates,
            profile_assignments=_make_profile_assignments(),
        )
        scaffold(tmp_path, config)
        expected = [
            ".fdsx/config.yaml",
            ".fdsx/workflows/full-impl/workflow.yaml",
        ]
        for path in expected:
            assert (tmp_path / path).exists(), f"Missing: {path}"

    def test_scaffold_returns_sorted_file_list(self, tmp_path):
        templates = discover_templates()
        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=templates,
            profile_assignments=_make_profile_assignments(),
        )
        result = scaffold(tmp_path, config)
        assert result.created == sorted(result.created)
        expected = [
            ".fdsx/config.yaml",
            ".fdsx/workflows/full-impl/finalize.md",
            ".fdsx/workflows/full-impl/fix.md",
            ".fdsx/workflows/full-impl/implement.md",
            ".fdsx/workflows/full-impl/plan.md",
            ".fdsx/workflows/full-impl/replan.md",
            ".fdsx/workflows/full-impl/review-code-quality.md",
            ".fdsx/workflows/full-impl/review-security.md",
            ".fdsx/workflows/full-impl/workflow.yaml",
            ".fdsx/workflows/self-improve/README.md",
            ".fdsx/workflows/self-improve/analyze.md",
            ".fdsx/workflows/self-improve/collect_data.sh",
            ".fdsx/workflows/self-improve/research.md",
            ".fdsx/workflows/self-improve/workflow.yaml",
            ".fdsx/workflows/self-improve/write_lessons.md",
            ".fdsx/workflows/simple-impl/finalize.md",
            ".fdsx/workflows/simple-impl/fix.md",
            ".fdsx/workflows/simple-impl/implement.md",
            ".fdsx/workflows/simple-impl/plan.md",
            ".fdsx/workflows/simple-impl/replan.md",
            ".fdsx/workflows/simple-impl/review-general.md",
            ".fdsx/workflows/simple-impl/workflow.yaml",
        ]
        assert result.created == expected

    def test_scaffold_permission_error(self, tmp_path):
        templates = discover_templates()
        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=templates,
            profile_assignments=_make_profile_assignments(),
        )
        with (
            patch("os.rename", side_effect=PermissionError("mocked")),
            patch("tempfile.mkdtemp", side_effect=PermissionError("mocked")),
            pytest.raises(PermissionError),
        ):
            scaffold(tmp_path, config)

    def test_fresh_scaffold_cleans_up_temp_dir_on_failure(self, tmp_path):
        """If scaffold fails mid-creation, no partial temp dir remains."""
        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=discover_templates(),
            profile_assignments=_make_profile_assignments(),
        )
        with (
            patch(
                "fdsx.core.init.Path.rename",
                side_effect=OSError("mocked rename failure"),
            ),
            pytest.raises(OSError),
        ):
            scaffold(tmp_path, config)

        # No .fdsx or .fdsx.tmp.* directories should remain
        assert not (tmp_path / ".fdsx").exists()
        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".fdsx.tmp.")]
        assert leftover == []


class TestCheckConflicts:
    def test_no_conflicts_when_no_fdsx_dir(self, tmp_path):
        templates = discover_templates()
        conflicts = check_conflicts(tmp_path, templates)
        assert conflicts == []

    def test_no_conflicts_when_no_matching_workflows(self, tmp_path):
        (tmp_path / ".fdsx" / "workflows").mkdir(parents=True)
        templates = discover_templates()
        conflicts = check_conflicts(tmp_path, templates)
        assert conflicts == []

    def test_detects_existing_workflow_conflict(self, tmp_path):
        workflows_dir = tmp_path / ".fdsx" / "workflows" / "full-impl"
        workflows_dir.mkdir(parents=True)
        templates = discover_templates()
        conflicts = check_conflicts(tmp_path, templates)
        assert "full-impl" in conflicts


class TestScaffoldExistingProtection:
    def _make_config(self):
        return InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=discover_templates(),
            profile_assignments=_make_profile_assignments(),
        )

    def test_skips_config_yaml_when_present(self, tmp_path):
        """scaffold() into existing .fdsx/ skips config.yaml if already present."""
        fdsx_dir = tmp_path / ".fdsx"
        fdsx_dir.mkdir()
        config_path = fdsx_dir / "config.yaml"
        config_path.write_text("original: true\n")

        result = scaffold(tmp_path, self._make_config())

        assert result.skipped_config is True
        assert config_path.read_text() == "original: true\n"
        assert ".fdsx/config.yaml" not in result.created

    def test_creates_config_yaml_when_missing_in_existing(self, tmp_path):
        """scaffold() into existing .fdsx/ creates config.yaml if not present."""
        (tmp_path / ".fdsx").mkdir()

        result = scaffold(tmp_path, self._make_config())

        assert result.skipped_config is False
        assert (tmp_path / ".fdsx" / "config.yaml").exists()
        assert ".fdsx/config.yaml" in result.created

    def test_skipped_workflows_lists_conflicts(self, tmp_path):
        """scaffold() reports conflicting workflows in skipped_workflows."""
        fdsx_dir = tmp_path / ".fdsx"
        workflows_dir = fdsx_dir / "workflows" / "full-impl"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "workflow.yaml").write_text("existing: true\n")

        result = scaffold(tmp_path, self._make_config())

        assert "full-impl" in result.skipped_workflows
        # Original file preserved
        assert (workflows_dir / "workflow.yaml").read_text() == "existing: true\n"

    def test_allow_overwrite_replaces_approved_workflow(self, tmp_path):
        """scaffold() with allow_overwrite overwrites approved conflicting workflows."""
        fdsx_dir = tmp_path / ".fdsx"
        workflows_dir = fdsx_dir / "workflows" / "full-impl"
        workflows_dir.mkdir(parents=True)
        (workflows_dir / "workflow.yaml").write_text("old: true\n")

        result = scaffold(tmp_path, self._make_config(), allow_overwrite={"full-impl"})

        assert "full-impl" not in result.skipped_workflows
        content = (workflows_dir / "workflow.yaml").read_text()
        assert content != "old: true\n"  # overwritten with template content

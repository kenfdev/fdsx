"""Integration tests for builtin template discovery and scaffold with new templates.

Covers the three new production templates: full-impl, simple-impl, self-improve.
"""

from pathlib import Path

from fdsx.core.init import discover_templates, scaffold
from fdsx.models.init import InitConfig, ProviderSelection

_KNOWN_TEMPLATE_NAMES = {"full-impl", "simple-impl", "self-improve"}

_DEFAULT_PROFILE_ASSIGNMENTS = {
    name: ProviderSelection(provider="claude", model="claude-sonnet-4-7")
    for name in ["smarty", "doer", "specialist", "generalist", "behemoth"]
}


class TestDiscoverBuiltinTemplates:
    def test_returns_exactly_three_builtin_templates(self):
        """discover_templates() returns exactly 3 builtin templates."""
        templates = discover_templates()
        builtin_templates = [t for t in templates if t.source == "builtin"]
        assert len(builtin_templates) == 3

    def test_builtin_template_names_match_expected_set(self):
        """Builtin template names are exactly full-impl, simple-impl, self-improve."""
        templates = discover_templates()
        builtin_names = {t.name for t in templates if t.source == "builtin"}
        assert builtin_names == _KNOWN_TEMPLATE_NAMES

    def test_each_builtin_has_workflow_yaml(self):
        """Each builtin template has a workflow.yaml file."""
        templates = discover_templates()
        for t in templates:
            if t.source == "builtin":
                assert (t.path / "workflow.yaml").is_file(), (
                    f"Builtin template {t.name!r} is missing workflow.yaml"
                )


class TestFullImplTemplate:
    def test_has_expected_file_extensions(self, tmp_path: Path):
        """full-impl template contains .md and .yaml files."""
        templates = discover_templates()
        full_impl = next(t for t in templates if t.name == "full-impl")

        md_files = list(full_impl.path.glob("*.md"))
        yaml_files = list(full_impl.path.glob("*.yaml"))

        assert len(md_files) > 0, "full-impl must have at least one .md file"
        assert len(yaml_files) == 1, "full-impl must have exactly one .yaml file"
        assert (full_impl.path / "workflow.yaml").is_file()


class TestSimpleImplTemplate:
    def test_has_expected_file_extensions(self, tmp_path: Path):
        """simple-impl template contains .md and .yaml files."""
        templates = discover_templates()
        simple_impl = next(t for t in templates if t.name == "simple-impl")

        md_files = list(simple_impl.path.glob("*.md"))
        yaml_files = list(simple_impl.path.glob("*.yaml"))

        assert len(md_files) > 0, "simple-impl must have at least one .md file"
        assert len(yaml_files) == 1, "simple-impl must have exactly one .yaml file"
        assert (simple_impl.path / "workflow.yaml").is_file()


class TestSelfImproveTemplate:
    def test_has_sh_script_file(self, tmp_path: Path):
        """self-improve template contains .sh shell script file."""
        templates = discover_templates()
        self_improve = next(t for t in templates if t.name == "self-improve")

        sh_files = list(self_improve.path.glob("*.sh"))
        yaml_files = list(self_improve.path.glob("*.yaml"))
        md_files = list(self_improve.path.glob("*.md"))

        assert len(sh_files) > 0, "self-improve must have at least one .sh file"
        assert len(yaml_files) == 1, "self-improve must have exactly one .yaml file"
        assert len(md_files) > 0, "self-improve must have at least one .md file"
        assert (self_improve.path / "workflow.yaml").is_file()


class TestScaffoldWithNewTemplates:
    def test_scaffold_full_impl_creates_all_template_files(self, tmp_path: Path):
        """scaffold() with full-impl creates all .md and .yaml files."""
        templates = discover_templates()
        full_impl = [t for t in templates if t.name == "full-impl"]

        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=full_impl,
            profile_assignments=_DEFAULT_PROFILE_ASSIGNMENTS,
        )
        result = scaffold(tmp_path, config)

        assert result.created
        full_impl_files = [
            f for f in result.created if f.startswith(".fdsx/workflows/full-impl/")
        ]
        assert len(full_impl_files) > 1, "full-impl should create multiple files"

        assert (tmp_path / ".fdsx/workflows/full-impl/workflow.yaml").is_file()
        assert (tmp_path / ".fdsx/workflows/full-impl/plan.md").is_file()

    def test_scaffold_simple_impl_creates_all_template_files(self, tmp_path: Path):
        """scaffold() with simple-impl creates all .md and .yaml files."""
        templates = discover_templates()
        simple_impl = [t for t in templates if t.name == "simple-impl"]

        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=simple_impl,
            profile_assignments=_DEFAULT_PROFILE_ASSIGNMENTS,
        )
        result = scaffold(tmp_path, config)

        assert result.created
        simple_impl_files = [
            f for f in result.created if f.startswith(".fdsx/workflows/simple-impl/")
        ]
        assert len(simple_impl_files) > 1, "simple-impl should create multiple files"

        assert (tmp_path / ".fdsx/workflows/simple-impl/workflow.yaml").is_file()
        assert (tmp_path / ".fdsx/workflows/simple-impl/plan.md").is_file()

    def test_scaffold_self_improve_creates_sh_and_md_files(self, tmp_path: Path):
        """scaffold() with self-improve creates .sh, .md, and .yaml files."""
        templates = discover_templates()
        self_improve = [t for t in templates if t.name == "self-improve"]

        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=self_improve,
            profile_assignments=_DEFAULT_PROFILE_ASSIGNMENTS,
        )
        result = scaffold(tmp_path, config)

        assert result.created
        self_improve_files = [
            f for f in result.created if f.startswith(".fdsx/workflows/self-improve/")
        ]
        assert len(self_improve_files) > 1, "self-improve should create multiple files"

        assert (tmp_path / ".fdsx/workflows/self-improve/workflow.yaml").is_file()
        assert (tmp_path / ".fdsx/workflows/self-improve/collect_data.sh").is_file()

    def test_scaffold_all_templates_creates_all_files(self, tmp_path: Path):
        """scaffold() with all three templates creates files from all templates."""
        templates = discover_templates()

        config = InitConfig(
            providers=[ProviderSelection(provider="claude", model="claude-sonnet-4-7")],
            templates=templates,
            profile_assignments=_DEFAULT_PROFILE_ASSIGNMENTS,
        )
        result = scaffold(tmp_path, config)

        full_impl_files = [f for f in result.created if "full-impl" in f]
        simple_impl_files = [f for f in result.created if "simple-impl" in f]
        self_improve_files = [f for f in result.created if "self-improve" in f]

        assert len(full_impl_files) >= 8, "full-impl should have at least 8 files"
        assert len(simple_impl_files) >= 7, "simple-impl should have at least 7 files"
        assert len(self_improve_files) >= 6, "self-improve should have at least 6 files"

"""Integration tests for full init flow with profile wiring (T009)."""

from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from fdsx.cli import main
from fdsx.models.init import (
    ProviderSelection,
    ScaffoldResult,
    TemplateInfo,
)

runner = CliRunner()

FAKE_PROVIDERS = ["claude"]
FAKE_SELECTIONS = [ProviderSelection(provider="claude", model="sonnet")]
FAKE_ASSIGNMENTS = {
    "smarty": ProviderSelection(provider="claude", model="sonnet"),
    "doer": ProviderSelection(provider="claude", model="sonnet"),
    "specialist": ProviderSelection(provider="claude", model="sonnet"),
    "generalist": ProviderSelection(provider="claude", model="sonnet"),
    "behemoth": ProviderSelection(provider="claude", model="sonnet"),
}
FAKE_RESULT = ScaffoldResult(
    created=[".fdsx/config.yaml", ".fdsx/workflows/full-impl/workflow.yaml"],
    skipped_config=False,
    skipped_workflows=[],
)


class TestInitFullFlow:
    """Tests for full init flow wiring (providers -> models -> profiles -> templates -> scaffold)."""

    def test_assign_profiles_called_with_provider_selections(
        self, tmp_path, monkeypatch
    ):
        """Verify assign_profiles is called with select_models output, not hardcoded."""
        monkeypatch.chdir(tmp_path)

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=[]),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch(
                "fdsx.cli.main.assign_profiles", return_value=FAKE_ASSIGNMENTS
            ) as mock_assign,
            patch("fdsx.cli.main.select_templates", return_value=[]),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT),
            patch("fdsx.cli.main._prompt_and_install_skill"),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)

        assert result.exit_code == 0
        mock_assign.assert_called_once_with(FAKE_SELECTIONS)

    def test_scaffold_receives_profile_assignments_from_assign_profiles(
        self, tmp_path, monkeypatch
    ):
        """Verify scaffold receives profile_assignments dict from assign_profiles, not hardcoded defaults."""
        monkeypatch.chdir(tmp_path)

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=[]),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.assign_profiles", return_value=FAKE_ASSIGNMENTS),
            patch("fdsx.cli.main.select_templates", return_value=[]),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT) as mock_scaffold,
            patch("fdsx.cli.main._prompt_and_install_skill"),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)

        assert result.exit_code == 0
        call_args = mock_scaffold.call_args
        init_config = call_args[0][1]
        assert init_config.profile_assignments == FAKE_ASSIGNMENTS

    def test_next_steps_mentions_profile_names(self, tmp_path, monkeypatch):
        """Verify Next steps message mentions profile names for customization."""
        monkeypatch.chdir(tmp_path)

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=[]),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.assign_profiles", return_value=FAKE_ASSIGNMENTS),
            patch("fdsx.cli.main.select_templates", return_value=[]),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT),
            patch("fdsx.cli.main._prompt_and_install_skill"),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)

        assert result.exit_code == 0
        assert "smarty" in result.output
        assert "doer" in result.output
        assert "specialist" in result.output
        assert "generalist" in result.output
        assert "behemoth" in result.output
        assert "Customize model assignments per profile" in result.output

    def test_config_contains_five_named_profiles(self, tmp_path, monkeypatch):
        """Verify scaffold produces config.yaml with all 5 named profiles (real scaffold, no mock)."""
        monkeypatch.chdir(tmp_path)

        template_dir = tmp_path / "full-impl"
        template_dir.mkdir()
        workflow_file = template_dir / "workflow.yaml"
        workflow_file.write_text("states: []")

        real_template = TemplateInfo(
            name="full-impl",
            path=template_dir,
            source="builtin",
        )

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=[real_template]),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.assign_profiles", return_value=FAKE_ASSIGNMENTS),
            patch("fdsx.cli.main.select_templates", return_value=[real_template]),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main._prompt_and_install_skill"),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)

        assert result.exit_code == 0

        config_path = tmp_path / ".fdsx" / "config.yaml"
        assert config_path.exists(), "config.yaml should be created by scaffold"

        config_content = yaml.safe_load(config_path.read_text())
        assert "profiles" in config_content
        profiles = config_content["profiles"]
        assert set(profiles.keys()) == {
            "smarty",
            "doer",
            "specialist",
            "generalist",
            "behemoth",
        }

        for _profile_name, profile_config in profiles.items():
            assert "provider" in profile_config
            assert "model" in profile_config

    def test_config_yaml_structure_valid(self, tmp_path, monkeypatch):
        """Verify generated config.yaml is valid YAML with correct structure."""
        monkeypatch.chdir(tmp_path)

        template_dir = tmp_path / "linear-basic"
        template_dir.mkdir()
        workflow_file = template_dir / "workflow.yaml"
        workflow_file.write_text("states: []")

        real_template = TemplateInfo(
            name="linear-basic",
            path=template_dir,
            source="builtin",
        )

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=[real_template]),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.assign_profiles", return_value=FAKE_ASSIGNMENTS),
            patch("fdsx.cli.main.select_templates", return_value=[real_template]),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main._prompt_and_install_skill"),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)

        assert result.exit_code == 0

        config_path = tmp_path / ".fdsx" / "config.yaml"
        config_text = config_path.read_text()

        parsed = yaml.safe_load(config_text)
        assert isinstance(parsed, dict)
        assert "profiles" in parsed
        assert "workflow_selector" in parsed
        assert parsed["workflow_selector"]["profile"] == "generalist"

    def test_init_flow_order_profiles_before_templates(self, tmp_path, monkeypatch):
        """Verify assign_profiles is called before select_templates."""
        monkeypatch.chdir(tmp_path)
        call_order = []

        def track_assign(selections):
            call_order.append("assign_profiles")
            return FAKE_ASSIGNMENTS

        def track_templates(templates):
            call_order.append("select_templates")
            return []

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=[]),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.assign_profiles", side_effect=track_assign),
            patch("fdsx.cli.main.select_templates", side_effect=track_templates),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main.scaffold", return_value=FAKE_RESULT),
            patch("fdsx.cli.main._prompt_and_install_skill"),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)

        assert result.exit_code == 0
        assert call_order.index("assign_profiles") < call_order.index(
            "select_templates"
        )

    def test_init_flow_order_skill_install_before_scaffold(self, tmp_path, monkeypatch):
        """Verify _prompt_and_install_skill is called before scaffold."""
        monkeypatch.chdir(tmp_path)
        call_order = []

        def track_skill(cwd):
            call_order.append("skill_install")

        def track_scaffold(cwd, config, allow_overwrite):
            call_order.append("scaffold")
            return FAKE_RESULT

        with (
            patch("fdsx.cli.main.sys") as mock_sys,
            patch("fdsx.cli.main.discover_templates", return_value=[]),
            patch("fdsx.cli.main.needs_init", return_value=True),
            patch("fdsx.cli.main.select_providers", return_value=FAKE_PROVIDERS),
            patch("fdsx.cli.main.select_models", return_value=FAKE_SELECTIONS),
            patch("fdsx.cli.main.assign_profiles", return_value=FAKE_ASSIGNMENTS),
            patch("fdsx.cli.main.select_templates", return_value=[]),
            patch("fdsx.cli.main.check_conflicts", return_value=[]),
            patch("fdsx.cli.main.scaffold", side_effect=track_scaffold),
            patch(
                "fdsx.cli.main._prompt_and_install_skill",
                side_effect=track_skill,
            ),
        ):
            mock_sys.stdin.isatty.return_value = True
            result = runner.invoke(main.app, ["init"], catch_exceptions=False)

        assert result.exit_code == 0
        assert call_order.index("skill_install") < call_order.index("scaffold")

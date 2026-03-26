import yaml

from tests import FIXTURES_DIR


class TestProfileFixtures:
    """Validate profile workflow fixtures have correct structure for downstream phases."""

    def test_profile_flow_fixture_structure(self) -> None:
        """profile_flow.yaml: smart_guy profile defined, plan+review use it, implement uses system provider."""
        path = FIXTURES_DIR / "profile_flow.yaml"
        with open(path) as f:
            data = yaml.safe_load(f)

        assert "smart_guy" in data["profiles"]
        smart_guy = data["profiles"]["smart_guy"]
        assert smart_guy["provider"] == "claude"
        assert smart_guy["model"] == "claude-sonnet-4-6"

        states = data["states"]
        assert states["plan"]["profile"] == "smart_guy"
        assert "provider" not in states["plan"]

        assert states["implement"]["provider"] == "system"
        assert "profile" not in states["implement"]

        assert states["review"]["profile"] == "smart_guy"
        assert "provider" not in states["review"]

    def test_profile_parallel_flow_fixture_structure(self) -> None:
        """profile_parallel_flow.yaml: reviewer + security_reviewer profiles, 2 branches each using a distinct profile."""
        path = FIXTURES_DIR / "profile_parallel_flow.yaml"
        with open(path) as f:
            data = yaml.safe_load(f)

        assert "reviewer" in data["profiles"]
        reviewer = data["profiles"]["reviewer"]
        assert reviewer["provider"] == "claude"
        assert reviewer["model"] == "claude-sonnet-4-6"

        assert "security_reviewer" in data["profiles"]
        sec_reviewer = data["profiles"]["security_reviewer"]
        assert sec_reviewer["provider"] == "codex"
        assert sec_reviewer["model"] == "gpt-5.4"

        parallel_state = data["states"]["review_parallel"]
        assert parallel_state["type"] == "parallel"
        branches = parallel_state["branches"]
        assert len(branches) == 2

        assert branches[0]["profile"] == "reviewer"
        assert "provider" not in branches[0]

        assert branches[1]["profile"] == "security_reviewer"
        assert "provider" not in branches[1]

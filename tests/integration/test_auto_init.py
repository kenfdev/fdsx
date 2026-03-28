from unittest.mock import patch

import pytest
import yaml

from fdsx.core.init import CONFIG_TEMPLATE, needs_init, scaffold


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

    def test_config_template_valid_yaml(self):
        lines = CONFIG_TEMPLATE.splitlines()
        uncommented = []
        for line in lines:
            if line.startswith("# "):
                uncommented.append(line[2:])
            elif line == "#":
                uncommented.append("")
            else:
                uncommented.append(line)
        uncommented_yaml = "\n".join(uncommented)
        parsed = yaml.safe_load(uncommented_yaml)
        assert isinstance(parsed, dict)
        expected_keys = {
            "workflows_dir",
            "auto_workflow",
            "profiles",
            "providers",
            "task_splitter",
            "workflow_selector",
            "hooks",
        }
        assert expected_keys.issubset(parsed.keys())

    def test_scaffold_creates_complete_structure(self, tmp_path):
        scaffold(tmp_path)
        expected = [
            ".fdsx/config.yaml",
            ".fdsx/workflows/plan-implement-review/implement-prompt.txt",
            ".fdsx/workflows/plan-implement-review/plan-prompt.txt",
            ".fdsx/workflows/plan-implement-review/workflow.yaml",
        ]
        for path in expected:
            assert (tmp_path / path).exists(), f"Missing: {path}"

    def test_scaffold_returns_sorted_file_list(self, tmp_path):
        result = scaffold(tmp_path)
        assert result == sorted(result)
        expected = [
            ".fdsx/config.yaml",
            ".fdsx/workflows/plan-implement-review/implement-prompt.txt",
            ".fdsx/workflows/plan-implement-review/plan-prompt.txt",
            ".fdsx/workflows/plan-implement-review/workflow.yaml",
        ]
        assert result == expected

    def test_scaffold_permission_error(self, tmp_path):
        with (
            patch("os.makedirs", side_effect=PermissionError("mocked")),
            patch("pathlib.Path.mkdir", side_effect=PermissionError("mocked")),
            patch("builtins.open", side_effect=PermissionError("mocked")),
            patch("importlib.resources.files"),
            pytest.raises(PermissionError),
        ):
            scaffold(tmp_path)

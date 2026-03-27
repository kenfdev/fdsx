import yaml

from fdsx.core.init import CONFIG_TEMPLATE, needs_init


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

import tempfile
from pathlib import Path

from fdsx.core.loader import load_flow, validate_flow
from tests import FIXTURES_DIR


class TestLoadFlow:
    def test_load_valid_simple_flow(self):
        path = FIXTURES_DIR / "simple_flow.yaml"
        flow, errors = load_flow(path)
        assert flow is not None
        assert len(errors) == 0
        assert flow.name == "Simple Plan-Implement-Review Flow"

    def test_load_nonexistent_file(self):
        path = FIXTURES_DIR / "nonexistent.yaml"
        flow, errors = load_flow(path)
        assert flow is None
        assert len(errors) > 0
        assert "not found" in errors[0].lower()

    def test_load_invalid_yaml(self):
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("  invalid: yaml\n    content:\n      - broken")
            f.flush()
            path = Path(f.name)

        flow, errors = load_flow(path)
        assert flow is None
        assert len(errors) > 0

        path.unlink()

    def test_load_missing_start_at(self):
        path = FIXTURES_DIR / "invalid_flows" / "missing_start_at.yaml"
        flow, errors = load_flow(path)
        assert flow is None

    def test_load_bad_next_reference(self):
        path = FIXTURES_DIR / "invalid_flows" / "bad_next_ref.yaml"
        flow, errors = load_flow(path)
        assert flow is None

    def test_load_mutual_exclusive(self):
        path = FIXTURES_DIR / "invalid_flows" / "mutual_exclusive.yaml"
        flow, errors = load_flow(path)
        assert flow is None

    def test_load_yaml_list_root_returns_error(self):
        """Regression: non-dict YAML root (list) must not crash with TypeError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("- item1\n- item2\n")
            path = Path(f.name)

        try:
            flow, errors = load_flow(path)
            assert flow is None
            assert len(errors) > 0
            assert "mapping" in errors[0].lower()
        finally:
            path.unlink()

    def test_load_yaml_scalar_root_returns_error(self):
        """Regression: non-dict YAML root (scalar string) must not crash with TypeError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("just a string\n")
            path = Path(f.name)

        try:
            flow, errors = load_flow(path)
            assert flow is None
            assert len(errors) > 0
            assert "mapping" in errors[0].lower()
        finally:
            path.unlink()


class TestValidateFlow:
    def test_validate_valid_flow(self):
        path = FIXTURES_DIR / "simple_flow.yaml"
        is_valid, errors = validate_flow(path)
        assert is_valid
        assert len(errors) == 0

    def test_validate_invalid_flow(self):
        path = FIXTURES_DIR / "invalid_flows" / "missing_start_at.yaml"
        is_valid, errors = validate_flow(path)
        assert not is_valid
        assert len(errors) > 0

    def test_validate_nonexistent(self):
        path = FIXTURES_DIR / "nonexistent.yaml"
        is_valid, errors = validate_flow(path)
        assert not is_valid


class TestPromptFileResolution:
    def test_prompt_file_resolved_relative_to_yaml(self):
        path = FIXTURES_DIR / "prompt_file_test" / "flow.yaml"
        flow, errors = load_flow(path)
        assert flow is not None, f"Flow loading failed: {errors}"
        assert len(errors) == 0

        task1 = flow.states["task1"]
        assert task1.prompt_template is not None
        assert "test prompt template" in task1.prompt_template.lower()
        assert task1.prompt_file is None

    # F5 regression: path traversal rejection
    def test_prompt_file_path_traversal_rejected(self):
        """F5: prompt_file paths that escape the workflow directory must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            # provider=claude requires prompt_template or prompt_file (no command)
            flow_yaml.write_text(
                "name: test\n"
                "description: test description\n"
                "start_at: task1\n"
                "states:\n"
                "  task1:\n"
                "    type: task\n"
                "    provider: claude\n"
                "    model: opus\n"
                "    result_path: '$.result'\n"
                "    prompt_file: '../../../etc/passwd'\n"
                "    end: true\n"
            )
            flow, errors = load_flow(flow_yaml)
            assert flow is None
            assert any(
                "escapes" in e or "relative" in e.lower() or "path" in e.lower()
                for e in errors
            )

    def test_prompt_file_absolute_path_rejected(self):
        """F5: absolute prompt_file paths must be rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            flow_yaml.write_text(
                "name: test\n"
                "description: test description\n"
                "start_at: task1\n"
                "states:\n"
                "  task1:\n"
                "    type: task\n"
                "    provider: claude\n"
                "    model: opus\n"
                "    result_path: '$.result'\n"
                "    prompt_file: '/etc/passwd'\n"
                "    end: true\n"
            )
            flow, errors = load_flow(flow_yaml)
            assert flow is None
            assert any(
                "absolute" in e.lower() or "relative" in e.lower() for e in errors
            )

    def test_input_keys_prevent_false_positive_in_loader(self):
        """F2: load_flow must accept input_keys and suppress warnings for runtime-provided vars.

        Uses {cli_input} instead of {task} because {task} is now a
        recognised global variable (GLOBAL_TASK_VARS) and would never be
        flagged as undefined.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            flow_yaml.write_text(
                "name: test\n"
                "description: test description\n"
                "start_at: start\n"
                "states:\n"
                "  start:\n"
                "    type: task\n"
                "    provider: system\n"
                "    command: echo hello\n"
                "    result_path: '$.result'\n"
                "    next: middle\n"
                "  middle:\n"
                "    type: task\n"
                "    provider: system\n"
                "    command: echo {cli_input}\n"
                "    result_path: '$.other'\n"
                "    end: true\n"
            )
            # Without input_keys: flow is rejected (variable errors are blocking)
            flow_bad, errors = load_flow(flow_yaml)
            assert flow_bad is None
            assert any("cli_input" in e for e in errors)

            # With input_keys: no warnings
            flow_ok, no_warnings = load_flow(flow_yaml, input_keys={"cli_input"})
            assert flow_ok is not None, (
                f"Expected load to succeed but got: {no_warnings}"
            )
            assert len(no_warnings) == 0


class TestDescriptionValidation:
    """T10: Tests for description field pre-check in loader."""

    def test_load_flow_missing_description(self):
        """T10: Loader should provide actionable error for missing description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            flow_yaml.write_text(
                "name: test\n"
                "start_at: start\n"
                "states:\n"
                "  start:\n"
                "    type: task\n"
                "    provider: system\n"
                "    command: echo test\n"
                "    result_path: '$.result'\n"
                "    end: true\n"
            )
            flow, errors = load_flow(flow_yaml)
            assert flow is None
            assert len(errors) > 0
            assert "description" in errors[0].lower()

    def test_load_flow_empty_description(self):
        """T10: Loader should reject empty description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            flow_yaml.write_text(
                "name: test\n"
                "description: ''\n"
                "start_at: start\n"
                "states:\n"
                "  start:\n"
                "    type: task\n"
                "    provider: system\n"
                "    command: echo test\n"
                "    result_path: '$.result'\n"
                "    end: true\n"
            )
            flow, errors = load_flow(flow_yaml)
            assert flow is None
            assert len(errors) > 0
            assert "description" in errors[0].lower()

    def test_load_flow_with_valid_description(self):
        """T10: Loader should accept valid description."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            flow_yaml.write_text(
                "name: test\n"
                "description: A valid test flow\n"
                "start_at: start\n"
                "states:\n"
                "  start:\n"
                "    type: task\n"
                "    provider: system\n"
                "    command: echo test\n"
                "    result_path: '$.result'\n"
                "    end: true\n"
            )
            flow, errors = load_flow(flow_yaml)
            assert flow is not None
            assert len(errors) == 0
            assert flow.description == "A valid test flow"

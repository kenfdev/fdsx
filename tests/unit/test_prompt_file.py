import tempfile
from pathlib import Path

from fdsx.core.loader import load_flow


class TestPromptFileWithVariables:
    def test_prompt_file_variable_substitution(self):
        """prompt_file content should have variable references resolved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            prompt_file = Path(tmpdir) / "prompt.txt"

            prompt_file.write_text("Hello {name}, you are {age} years old.")
            flow_yaml.write_text(
                "name: test\n"
                "start_at: task1\n"
                "max_loop: 1\n"
                "states:\n"
                "  task1:\n"
                "    type: task\n"
                "    provider: claude\n"
                "    model: opus\n"
                "    prompt_file: prompt.txt\n"
                "    result_path: '$.result'\n"
                "    end: true\n"
            )

            flow, errors = load_flow(flow_yaml)
            assert flow is not None, f"Flow loading failed: {errors}"

            task = flow.states["task1"]
            assert task.prompt_template == "Hello {name}, you are {age} years old."
            assert task.prompt_file is None

    def test_prompt_file_not_found(self):
        """prompt_file that doesn't exist should produce an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            flow_yaml.write_text(
                "name: test\n"
                "start_at: task1\n"
                "max_loop: 1\n"
                "states:\n"
                "  task1:\n"
                "    type: task\n"
                "    provider: claude\n"
                "    model: opus\n"
                "    prompt_file: nonexistent.txt\n"
                "    result_path: '$.result'\n"
                "    end: true\n"
            )

            flow, errors = load_flow(flow_yaml)
            assert flow is None
            assert any("not found" in e for e in errors)


class TestPromptFileParallelBranch:
    def test_prompt_file_in_parallel_branch(self):
        """prompt_file in parallel branch should be loaded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            branch_prompt = Path(tmpdir) / "branch_prompt.txt"

            branch_prompt.write_text("Branch prompt content")
            flow_yaml.write_text(
                "name: test\n"
                "start_at: parallel1\n"
                "max_loop: 1\n"
                "states:\n"
                "  parallel1:\n"
                "    type: parallel\n"
                "    result_path: '$.results'\n"
                "    branches:\n"
                "      - name: branch1\n"
                "        provider: claude\n"
                "        model: opus\n"
                "        prompt_file: branch_prompt.txt\n"
                "    next: end\n"
                "  end:\n"
                "    type: pass\n"
                "    end: true\n"
            )

            flow, errors = load_flow(flow_yaml)
            assert flow is not None, f"Flow loading failed: {errors}"

            parallel_state = flow.states["parallel1"]
            branch = parallel_state.branches[0]
            assert branch.prompt_template == "Branch prompt content"


class TestPromptFileEdgeCases:
    def test_prompt_file_empty_content(self):
        """prompt_file with empty content should work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            empty_prompt = Path(tmpdir) / "empty.txt"

            empty_prompt.write_text("")
            flow_yaml.write_text(
                "name: test\n"
                "start_at: task1\n"
                "max_loop: 1\n"
                "states:\n"
                "  task1:\n"
                "    type: task\n"
                "    provider: claude\n"
                "    model: opus\n"
                "    prompt_file: empty.txt\n"
                "    result_path: '$.result'\n"
                "    end: true\n"
            )

            flow, errors = load_flow(flow_yaml)
            assert flow is not None, f"Flow loading failed: {errors}"

            task = flow.states["task1"]
            assert task.prompt_template == ""

    def test_prompt_file_with_special_characters(self):
        """prompt_file with special characters should be loaded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            flow_yaml = Path(tmpdir) / "flow.yaml"
            special_prompt = Path(tmpdir) / "special.txt"

            special_prompt.write_text("Line 1\nLine 2\nLine 3\nSpecial: ${{var}}")
            flow_yaml.write_text(
                "name: test\n"
                "start_at: task1\n"
                "max_loop: 1\n"
                "states:\n"
                "  task1:\n"
                "    type: task\n"
                "    provider: claude\n"
                "    model: opus\n"
                "    prompt_file: special.txt\n"
                "    result_path: '$.result'\n"
                "    end: true\n"
            )

            flow, errors = load_flow(flow_yaml)
            assert flow is not None, f"Flow loading failed: {errors}"

            task = flow.states["task1"]
            assert task.prompt_template == "Line 1\nLine 2\nLine 3\nSpecial: ${{var}}"

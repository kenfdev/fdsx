"""Unit tests for the workflow selector module."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from fdsx.core.config import WorkflowSelectorConfig
from fdsx.core.selector import (
    _build_workflow_selection_prompt,
    _parse_workflow_selection,
    confirm_workflow_selection,
    discover_workflows,
    pick_workflow_manually,
    resolve_workflow_for_task,
    select_workflow,
)


def _minimal_workflow(name: str, description: str) -> str:
    """Return minimal valid workflow YAML content."""
    return yaml.dump(
        {
            "name": name,
            "description": description,
            "start_at": "s",
            "states": {
                "s": {
                    "type": "task",
                    "provider": "system",
                    "command": f"echo {name}",
                    "result_path": "$.x",
                    "end": True,
                }
            },
        }
    )


class TestDiscoverWorkflows:
    def test_discovers_yaml_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            (workflows_dir / "b-workflow.yaml").write_text(
                yaml.dump(
                    {
                        "name": "B",
                        "description": "B workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo b",
                                "result_path": "$.x",
                                "end": True,
                            }
                        },
                    }
                )
            )
            (workflows_dir / "a-workflow.yaml").write_text(
                yaml.dump(
                    {
                        "name": "A",
                        "description": "A workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo a",
                                "result_path": "$.x",
                                "end": True,
                            }
                        },
                    }
                )
            )

            results = discover_workflows(workflows_dir)

            assert len(results) == 2
            # Sorted by display_name (flow.name)
            assert results[0][2] == "A"
            assert results[1][2] == "B"
            assert results[0][1] == "A workflow"
            assert results[1][1] == "B workflow"

    def test_returns_empty_for_nonexistent_dir(self):
        results = discover_workflows(Path("/nonexistent/path"))
        assert results == []

    def test_raises_on_symlinked_dir(self, tmp_path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        link_dir = tmp_path / "link"
        link_dir.symlink_to(real_dir)

        with pytest.raises(ValueError, match="must not be a symlink"):
            discover_workflows(link_dir)

    def test_skips_invalid_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            (workflows_dir / "valid.yaml").write_text(
                yaml.dump(
                    {
                        "name": "V",
                        "description": "Valid",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo v",
                                "result_path": "$.x",
                                "end": True,
                            }
                        },
                    }
                )
            )
            (workflows_dir / "invalid.yaml").write_text("not: [valid: yaml")

            with pytest.warns(RuntimeWarning, match="Skipping invalid workflow"):
                results = discover_workflows(workflows_dir)

            assert len(results) == 1
            assert results[0][0].name == "valid.yaml"

    def test_skips_symlinked_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            real_file = workflows_dir / "workflow.yaml"
            real_file.write_text(
                yaml.dump(
                    {
                        "name": "W",
                        "description": "Workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo w",
                                "result_path": "$.x",
                                "end": True,
                            }
                        },
                    }
                )
            )
            symlink_file = workflows_dir / "link.yaml"
            symlink_file.symlink_to(real_file)

            with pytest.warns(RuntimeWarning, match="Skipping symlinked workflow file"):
                results = discover_workflows(workflows_dir)

            assert len(results) == 1
            assert results[0][0].name == "workflow.yaml"

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            results = discover_workflows(workflows_dir)
            assert results == []

    def test_discovers_directory_workflows(self, tmp_path):
        """Subdirectories with workflow.yaml are discovered as directory workflows."""
        wf_dir = tmp_path / "review"
        wf_dir.mkdir()
        (wf_dir / "workflow.yaml").write_text(_minimal_workflow("R", "Review workflow"))

        results = discover_workflows(tmp_path)

        assert len(results) == 1
        assert results[0][0] == wf_dir / "workflow.yaml"
        assert results[0][1] == "Review workflow"
        assert results[0][2] == "R"

    def test_discovers_mixed_flat_and_directory(self, tmp_path):
        """Both flat files and directory workflows are discovered."""
        (tmp_path / "plan.yaml").write_text(_minimal_workflow("P", "Plan workflow"))
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        (review_dir / "workflow.yaml").write_text(
            _minimal_workflow("R", "Review workflow")
        )

        results = discover_workflows(tmp_path)

        assert len(results) == 2
        display_names = [r[2] for r in results]
        assert display_names == ["P", "R"]  # sorted by display_name (flow.name)

    def test_directory_shadows_flat_file(self, tmp_path):
        """A directory 'review' shadows a flat file 'review.yaml'."""
        (tmp_path / "review.yaml").write_text(_minimal_workflow("RF", "Review flat"))
        review_dir = tmp_path / "review"
        review_dir.mkdir()
        (review_dir / "workflow.yaml").write_text(_minimal_workflow("RD", "Review dir"))

        results = discover_workflows(tmp_path)

        assert len(results) == 1
        assert results[0][1] == "Review dir"
        assert results[0][2] == "RD"

    def test_yml_extension_flat_files(self, tmp_path):
        """Flat *.yml files are discovered."""
        (tmp_path / "plan.yml").write_text(_minimal_workflow("P", "Plan yml"))

        results = discover_workflows(tmp_path)

        assert len(results) == 1
        assert results[0][2] == "P"
        assert results[0][1] == "Plan yml"

    def test_yml_extension_directory(self, tmp_path):
        """Directory workflows with workflow.yml are discovered."""
        wf_dir = tmp_path / "review"
        wf_dir.mkdir()
        (wf_dir / "workflow.yml").write_text(_minimal_workflow("R", "Review yml"))

        results = discover_workflows(tmp_path)

        assert len(results) == 1
        assert results[0][2] == "R"

    def test_yaml_takes_precedence_over_yml_flat(self, tmp_path):
        """When both plan.yaml and plan.yml exist, .yaml takes precedence."""
        (tmp_path / "plan.yaml").write_text(_minimal_workflow("PY", "Plan yaml"))
        (tmp_path / "plan.yml").write_text(_minimal_workflow("PL", "Plan yml"))

        results = discover_workflows(tmp_path)

        assert len(results) == 1
        assert results[0][0].name == "plan.yaml"
        assert results[0][1] == "Plan yaml"

    def test_yaml_takes_precedence_over_yml_directory(self, tmp_path):
        """In a directory, workflow.yaml takes precedence over workflow.yml."""
        wf_dir = tmp_path / "review"
        wf_dir.mkdir()
        (wf_dir / "workflow.yaml").write_text(_minimal_workflow("RY", "Review yaml"))
        (wf_dir / "workflow.yml").write_text(_minimal_workflow("RL", "Review yml"))

        results = discover_workflows(tmp_path)

        assert len(results) == 1
        assert results[0][0].name == "workflow.yaml"
        assert results[0][1] == "Review yaml"

    def test_skips_symlinked_directory(self, tmp_path):
        """Symlinked subdirectories are skipped."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        (real_dir / "workflow.yaml").write_text(_minimal_workflow("R", "Real"))
        link_dir = tmp_path / "linked"
        link_dir.symlink_to(real_dir)

        with pytest.warns(
            RuntimeWarning, match="Skipping symlinked workflow directory"
        ):
            results = discover_workflows(tmp_path)

        # real_dir is also a subdirectory, so it should be discovered
        assert len(results) == 1
        assert results[0][2] == "R"

    def test_skips_directory_without_workflow_yaml(self, tmp_path):
        """Subdirectories without workflow.yaml/yml are ignored."""
        no_wf_dir = tmp_path / "empty"
        no_wf_dir.mkdir()
        (no_wf_dir / "other.yaml").write_text("key: value")

        results = discover_workflows(tmp_path)

        assert len(results) == 0

    def test_display_name_flat_is_stem(self, tmp_path):
        """Display name for flat files is the flow.name."""
        (tmp_path / "plan-implement.yaml").write_text(
            _minimal_workflow("PI", "Plan-implement")
        )

        results = discover_workflows(tmp_path)

        assert results[0][2] == "PI"

    def test_display_name_directory_is_dirname(self, tmp_path):
        """Display name for directory workflows is the flow.name."""
        wf_dir = tmp_path / "my-review"
        wf_dir.mkdir()
        (wf_dir / "workflow.yaml").write_text(_minimal_workflow("MR", "My review"))

        results = discover_workflows(tmp_path)

        assert results[0][2] == "MR"

    def test_sorted_by_display_name(self, tmp_path):
        """Results are sorted by display_name (flow.name), not filename or path."""
        (tmp_path / "z-workflow.yaml").write_text(_minimal_workflow("Z", "Zeta"))
        a_dir = tmp_path / "a-workflow"
        a_dir.mkdir()
        (a_dir / "workflow.yaml").write_text(_minimal_workflow("A", "Alpha"))
        (tmp_path / "m-workflow.yaml").write_text(_minimal_workflow("M", "Mike"))

        results = discover_workflows(tmp_path)

        display_names = [r[2] for r in results]
        assert display_names == ["A", "M", "Z"]

    def test_skips_symlinked_workflow_file_inside_directory(self, tmp_path):
        """Regression (F5): symlinked workflow.yaml inside a directory is rejected."""
        wf_dir = tmp_path / "review"
        wf_dir.mkdir()
        real_file = tmp_path / "real-workflow.yaml"
        real_file.write_text(_minimal_workflow("R", "Real"))
        (wf_dir / "workflow.yaml").symlink_to(real_file)

        with pytest.warns(
            RuntimeWarning, match="Skipping symlinked workflow file in directory"
        ):
            results = discover_workflows(tmp_path)

        # The flat file real-workflow.yaml should still be discovered
        flat_names = [r[2] for r in results]
        assert "review" not in flat_names


class TestBuildWorkflowSelectionPrompt:
    def test_includes_task_description(self):
        workflows = [(Path("plan.yaml"), "Plan workflow", "plan")]
        prompt = _build_workflow_selection_prompt("Implement a feature", workflows)
        assert "Implement a feature" in prompt

    def test_includes_workflow_descriptions(self):
        workflows = [
            (Path("plan.yaml"), "Planning phase", "plan"),
            (Path("implement.yaml"), "Implementation", "implement"),
        ]
        prompt = _build_workflow_selection_prompt("Build something", workflows)
        assert "plan" in prompt
        assert "Planning phase" in prompt
        assert "implement" in prompt
        assert "Implementation" in prompt

    def test_requests_workflow_name_only(self):
        workflows = [(Path("test.yaml"), "Test workflow", "test")]
        prompt = _build_workflow_selection_prompt("Test task", workflows)
        assert "workflow name" in prompt.lower()
        assert "Return ONLY" in prompt or "only" in prompt.lower()


class TestParseWorkflowSelection:
    def test_parses_plain_filename(self):
        result = _parse_workflow_selection("plan-implement.yaml")
        assert result == "plan-implement.yaml"

    def test_strips_markdown_code_block(self):
        result = _parse_workflow_selection("```yaml\nplan-implement.yaml\n```")
        assert result == "plan-implement.yaml"

    def test_strips_yaml_code_block_with_quotes(self):
        result = _parse_workflow_selection('```yaml\n"plan.yaml"\n```')
        assert result == "plan.yaml"

    def test_strips_quotes(self):
        assert _parse_workflow_selection('"plan.yaml"') == "plan.yaml"
        assert _parse_workflow_selection("'plan.yaml'") == "plan.yaml"

    def test_strips_whitespace(self):
        result = _parse_workflow_selection("  plan.yaml  \n")
        assert result == "plan.yaml"

    def test_missing_yaml_extension_parsed_for_matching(self):
        result = _parse_workflow_selection("plan-implement")
        assert result == "plan-implement"

    def test_raises_on_empty_response(self):
        with pytest.raises(ValueError, match="Empty workflow selection"):
            _parse_workflow_selection("")

    def test_raises_on_whitespace_only_response(self):
        with pytest.raises(ValueError, match="Empty workflow selection"):
            _parse_workflow_selection("   \n  ")


class TestSelectWorkflow:
    def test_single_workflow_shortcut(self):
        workflows = [(Path("only.yaml"), "Only workflow", "only")]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        result = select_workflow("Do something", workflows, config)

        assert result == Path("only.yaml")

    def test_no_workflows_raises(self):
        workflows: list[tuple[Path, str, str]] = []
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        with pytest.raises(ValueError, match="No workflows found"):
            select_workflow("Do something", workflows, config)

    def test_multiple_workflows_calls_llm(self):
        workflows = [
            (Path("plan.yaml"), "Planning workflow", "plan"),
            (Path("implement.yaml"), "Implementation workflow", "implement"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="plan",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            result = select_workflow("Build a feature", workflows, config)

        assert result == Path("plan.yaml")
        call_kwargs = mock_provider.execute.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6"

    def test_llm_response_with_code_block(self):
        workflows = [(Path("implement.yaml"), "Implementation", "implement")]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="```yaml\nimplement.yaml\n```",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            result = select_workflow("Implement feature", workflows, config)

        assert result == Path("implement.yaml")

    def test_llm_selects_nonexistent_workflow_raises(self):
        workflows = [
            (Path("plan.yaml"), "Planning", "plan"),
            (Path("implement.yaml"), "Implementation", "implement"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="nonexistent.yaml",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            with pytest.raises(ValueError, match="does not match any available"):
                select_workflow("Build something", workflows, config)

    def test_llm_response_without_yaml_extension_matches(self):
        workflows = [
            (Path("plan.yaml"), "Planning", "plan"),
            (Path("implement.yaml"), "Implementation", "implement"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="implement",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            result = select_workflow("Build something", workflows, config)

        assert result == Path("implement.yaml")

    def test_llm_response_with_prefix_matches(self):
        workflows = [
            (Path("plan.yaml"), "Planning", "plan"),
            (Path("implement.yaml"), "Implementation", "implement"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="I recommend implement.yaml",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            result = select_workflow("Build something", workflows, config)

        assert result == Path("implement.yaml")

    def test_stem_overlap_does_not_match_wrong_workflow(self):
        """Regression: Strategy 4 must not match 'code' when LLM says 'review-code'."""
        workflows = [
            (Path("code.yaml"), "Code workflow", "code"),
            (Path("review.yaml"), "Review workflow", "review"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="review-code",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            with pytest.raises(ValueError, match="does not match any available"):
                select_workflow("Review code", workflows, config)

    def test_ambiguous_filename_match_raises(self):
        """Regression: Strategy 4 must raise if multiple names appear in the response."""
        workflows = [
            (Path("plan.yaml"), "Planning", "plan"),
            (Path("implement.yaml"), "Implementation", "implement"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="either plan.yaml or implement.yaml would work",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            with pytest.raises(ValueError, match="does not match any available"):
                select_workflow("Ambiguous task", workflows, config)

    def test_stem_in_surrounding_text_matches(self):
        """Regression: display_name matching should still work when LLM wraps the
        name in surrounding text like 'I recommend implement'.
        """
        workflows = [
            (Path("plan.yaml"), "Planning", "plan"),
            (Path("implement.yaml"), "Implementation", "implement"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="I recommend implement",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            result = select_workflow("Build something", workflows, config)

        assert result == Path("implement.yaml")

    def test_provider_failure_raises(self):
        workflows = [(Path("a.yaml"), "A", "a"), (Path("b.yaml"), "B", "b")]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=1,
            stdout="",
            stderr="Provider error",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            with pytest.raises(RuntimeError, match="Workflow selector failed"):
                select_workflow("Build something", workflows, config)


class TestConfirmWorkflowSelection:
    def test_approve_returns_true(self):
        with patch("builtins.input", return_value="y"):
            result = confirm_workflow_selection(
                Path("plan.yaml"), "Implement a feature"
            )
        assert result is True

    def test_reject_returns_false(self):
        with patch("builtins.input", return_value="n"):
            result = confirm_workflow_selection(
                Path("plan.yaml"), "Implement a feature"
            )
        assert result is False

    def test_list_returns_false(self):
        with patch("builtins.input", return_value="l"):
            result = confirm_workflow_selection(
                Path("plan.yaml"), "Implement a feature"
            )
        assert result is False

    def test_invalid_then_approve(self):
        inputs = iter(["invalid", "y"])
        with patch("builtins.input", lambda x: next(inputs)):
            result = confirm_workflow_selection(
                Path("plan.yaml"), "Implement a feature"
            )
        assert result is True


class TestPickWorkflowManually:
    def test_pick_valid_number(self):
        workflows = [
            (Path("plan.yaml"), "Planning", "plan"),
            (Path("implement.yaml"), "Implementation", "implement"),
        ]

        with patch("builtins.input", return_value="1"):
            result = pick_workflow_manually(workflows)

        assert result == Path("plan.yaml")

    def test_cancel_returns_none(self):
        workflows = [(Path("plan.yaml"), "Planning", "plan")]

        with patch("builtins.input", return_value="c"):
            result = pick_workflow_manually(workflows)

        assert result is None

    def test_invalid_number_prompts_again(self):
        workflows = [(Path("plan.yaml"), "Planning", "plan")]

        inputs = iter(["0", "1"])
        with patch("builtins.input", lambda x: next(inputs)):
            result = pick_workflow_manually(workflows)

        assert result == Path("plan.yaml")


class TestResolveWorkflowForTask:
    def test_no_workflows_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            config = WorkflowSelectorConfig(
                provider="claude", model="claude-sonnet-4-6"
            )

            with pytest.raises(ValueError, match="No workflows found"):
                resolve_workflow_for_task(
                    task_description="Do something",
                    workflows_dir=workflows_dir,
                    selector_config=config,
                    auto_workflow=True,
                )

    def test_single_workflow_auto_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            (workflows_dir / "only.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Only",
                        "description": "Only workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo o",
                                "result_path": "$.x",
                                "end": True,
                            }
                        },
                    }
                )
            )
            config = WorkflowSelectorConfig(
                provider="claude", model="claude-sonnet-4-6"
            )

            result = resolve_workflow_for_task(
                task_description="Do something",
                workflows_dir=workflows_dir,
                selector_config=config,
                auto_workflow=True,
            )

            assert result == Path(tmpdir) / "only.yaml"

    def test_calls_llm_when_multiple_and_auto(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            (workflows_dir / "plan.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Plan",
                        "description": "Plan workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo p",
                                "result_path": "$.x",
                                "end": True,
                            }
                        },
                    }
                )
            )
            (workflows_dir / "implement.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Impl",
                        "description": "Impl workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo i",
                                "result_path": "$.x",
                                "end": True,
                            }
                        },
                    }
                )
            )
            config = WorkflowSelectorConfig(
                provider="claude", model="claude-sonnet-4-6"
            )

            mock_provider = MagicMock()
            mock_provider.execute.return_value = MagicMock(
                exit_code=0,
                stdout="implement.yaml",
                stderr="",
            )

            with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
                result = resolve_workflow_for_task(
                    task_description="Build something",
                    workflows_dir=workflows_dir,
                    selector_config=config,
                    auto_workflow=True,
                )

            assert result == Path(tmpdir) / "implement.yaml"

    def test_confirm_prompt_when_not_auto(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            (workflows_dir / "only.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Only",
                        "description": "Only workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo o",
                                "result_path": "$.x",
                                "end": True,
                            }
                        },
                    }
                )
            )
            config = WorkflowSelectorConfig(
                provider="claude", model="claude-sonnet-4-6"
            )

            with patch("builtins.input", return_value="y"):
                result = resolve_workflow_for_task(
                    task_description="Do something",
                    workflows_dir=workflows_dir,
                    selector_config=config,
                    auto_workflow=False,
                )

            assert result == Path(tmpdir) / "only.yaml"

    def test_auto_workflow_true_never_calls_input(self):
        """Regression (FR-6.3): auto_workflow=True must never call input() during
        pre-computation. The engine always passes auto_workflow=True to this function
        so the batch confirmation is the sole confirm gate.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            (workflows_dir / "only.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Only",
                        "description": "Only workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo o",
                                "result_path": "$.x",
                                "end": True,
                            }
                        },
                    }
                )
            )
            config = WorkflowSelectorConfig(
                provider="claude", model="claude-sonnet-4-6"
            )

            # input() must NOT be called — if it is, the MagicMock raises on iteration
            with patch(
                "builtins.input",
                side_effect=AssertionError("input() must not be called in auto mode"),
            ):
                result = resolve_workflow_for_task(
                    task_description="Do something",
                    workflows_dir=workflows_dir,
                    selector_config=config,
                    auto_workflow=True,
                )

            assert result == Path(tmpdir) / "only.yaml"


class TestFuzzyMatchSubstringCollision:
    """Regression tests for F3: fuzzy match substring collision (plan vs planning)."""

    def test_planning_matches_over_plan(self):
        """'planning' in response should match 'planning', not 'plan'."""
        workflows = [
            (Path("plan.yaml"), "Plan workflow", "plan"),
            (Path("planning.yaml"), "Planning workflow", "planning"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="I recommend planning",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            result = select_workflow("Schedule tasks", workflows, config)

        assert result == Path("planning.yaml")

    def test_plan_matches_when_standalone(self):
        """'plan' as standalone word should match 'plan'."""
        workflows = [
            (Path("plan.yaml"), "Plan workflow", "plan"),
            (Path("planning.yaml"), "Planning workflow", "planning"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="use the plan workflow",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            result = select_workflow("Make a plan", workflows, config)

        assert result == Path("plan.yaml")


class TestConfirmWorkflowSelectionDisplayName:
    """Regression tests for F2: confirm shows display_name instead of workflow.yaml."""

    def test_directory_workflow_shows_display_name(self, capsys):
        """confirm_workflow_selection should show display_name, not 'workflow.yaml'."""
        with patch("builtins.input", return_value="y"):
            confirm_workflow_selection(
                Path("review/workflow.yaml"),
                "Review the code",
                display_name="review",
            )

        captured = capsys.readouterr()
        assert "review" in captured.err
        assert "workflow.yaml" not in captured.err

    def test_flat_workflow_falls_back_to_filename(self, capsys):
        """Without display_name, confirm_workflow_selection shows filename."""
        with patch("builtins.input", return_value="y"):
            confirm_workflow_selection(
                Path("plan.yaml"),
                "Plan the work",
            )

        captured = capsys.readouterr()
        assert "plan.yaml" in captured.err

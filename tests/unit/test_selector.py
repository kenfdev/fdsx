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
            names = [p.name for p, _ in results]
            assert names == ["a-workflow.yaml", "b-workflow.yaml"]
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


class TestBuildWorkflowSelectionPrompt:
    def test_includes_task_description(self):
        workflows = [(Path("plan.yaml"), "Plan workflow")]
        prompt = _build_workflow_selection_prompt("Implement a feature", workflows)
        assert "Implement a feature" in prompt

    def test_includes_workflow_descriptions(self):
        workflows = [
            (Path("plan.yaml"), "Planning phase"),
            (Path("implement.yaml"), "Implementation"),
        ]
        prompt = _build_workflow_selection_prompt("Build something", workflows)
        assert "plan.yaml" in prompt
        assert "Planning phase" in prompt
        assert "implement.yaml" in prompt
        assert "Implementation" in prompt

    def test_requests_filename_only(self):
        workflows = [(Path("test.yaml"), "Test workflow")]
        prompt = _build_workflow_selection_prompt("Test task", workflows)
        assert "filename" in prompt.lower()
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
        workflows = [(Path("only.yaml"), "Only workflow")]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        result = select_workflow("Do something", workflows, config)

        assert result == Path("only.yaml")

    def test_no_workflows_raises(self):
        workflows: list[tuple[Path, str]] = []
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        with pytest.raises(ValueError, match="No workflows found"):
            select_workflow("Do something", workflows, config)

    def test_multiple_workflows_calls_llm(self):
        workflows = [
            (Path("plan.yaml"), "Planning workflow"),
            (Path("implement.yaml"), "Implementation workflow"),
        ]
        config = WorkflowSelectorConfig(provider="claude", model="claude-sonnet-4-6")

        mock_provider = MagicMock()
        mock_provider.execute.return_value = MagicMock(
            exit_code=0,
            stdout="plan.yaml",
            stderr="",
        )

        with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
            result = select_workflow("Build a feature", workflows, config)

        assert result == Path("plan.yaml")
        call_kwargs = mock_provider.execute.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6"

    def test_llm_response_with_code_block(self):
        workflows = [(Path("implement.yaml"), "Implementation")]
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
            (Path("plan.yaml"), "Planning"),
            (Path("implement.yaml"), "Implementation"),
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
            (Path("plan.yaml"), "Planning"),
            (Path("implement.yaml"), "Implementation"),
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
            (Path("plan.yaml"), "Planning"),
            (Path("implement.yaml"), "Implementation"),
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
        """Regression: Strategy 3 must not match 'code.yaml' when LLM says 'review-code'.
        Previously, `wf_path.stem in selected_name` would match 'code' in 'review-code'.
        """
        workflows = [
            (Path("code.yaml"), "Code workflow"),
            (Path("review.yaml"), "Review workflow"),
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
        """Regression: Strategy 3 must raise if multiple filenames appear in the response."""
        workflows = [
            (Path("plan.yaml"), "Planning"),
            (Path("implement.yaml"), "Implementation"),
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
        """Regression (Finding 2): stem matching should still work when LLM wraps the
        stem in surrounding text like 'I recommend implement' (no .yaml extension).
        """
        workflows = [
            (Path("plan.yaml"), "Planning"),
            (Path("implement.yaml"), "Implementation"),
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
        workflows = [(Path("a.yaml"), "A"), (Path("b.yaml"), "B")]
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
            (Path("plan.yaml"), "Planning"),
            (Path("implement.yaml"), "Implementation"),
        ]

        with patch("builtins.input", return_value="1"):
            result = pick_workflow_manually(workflows)

        assert result == Path("plan.yaml")

    def test_cancel_returns_none(self):
        workflows = [(Path("plan.yaml"), "Planning")]

        with patch("builtins.input", return_value="c"):
            result = pick_workflow_manually(workflows)

        assert result is None

    def test_invalid_number_prompts_again(self):
        workflows = [(Path("plan.yaml"), "Planning")]

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
            with patch("builtins.input", side_effect=AssertionError("input() must not be called in auto mode")):
                result = resolve_workflow_for_task(
                    task_description="Do something",
                    workflows_dir=workflows_dir,
                    selector_config=config,
                    auto_workflow=True,
                )

            assert result == Path(tmpdir) / "only.yaml"

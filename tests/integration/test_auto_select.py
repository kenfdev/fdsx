"""Integration tests for workflow auto-selection end-to-end."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from fdsx.core.config import WorkflowSelectorConfig
from fdsx.core.selector import (
    discover_workflows,
    resolve_workflow_for_task,
    select_workflow,
)


class TestDiscoverWorkflowsIntegration:
    def test_end_to_end_with_real_workflows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)

            (workflows_dir / "plan.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Plan",
                        "description": "Planning workflow for creating project plans",
                        "start_at": "plan",
                        "states": {
                            "plan": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo plan",
                                "result_path": "$.result",
                                "end": True,
                            }
                        },
                    }
                )
            )
            (workflows_dir / "implement.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Implement",
                        "description": "Implementation workflow for coding features",
                        "start_at": "implement",
                        "states": {
                            "implement": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo implement",
                                "result_path": "$.result",
                                "end": True,
                            }
                        },
                    }
                )
            )

            results = discover_workflows(workflows_dir)

            assert len(results) == 2
            assert all(isinstance(p, Path) for p, _, _ in results)
            assert all(isinstance(d, str) for _, d, _ in results)


class TestSelectWorkflowIntegration:
    def test_single_workflow_auto_selects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            (workflows_dir / "only.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Only",
                        "description": "The only workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo only",
                                "result_path": "$.result",
                                "end": True,
                            }
                        },
                    }
                )
            )

            workflows = discover_workflows(workflows_dir)
            config = WorkflowSelectorConfig(
                provider="claude", model="claude-sonnet-4-6"
            )

            selected = select_workflow("Build a feature", workflows, config)

            assert selected.name == "only.yaml"

    def test_multiple_workflows_llm_selection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            (workflows_dir / "plan.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Plan",
                        "description": "Planning workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo plan",
                                "result_path": "$.result",
                                "end": True,
                            }
                        },
                    }
                )
            )
            (workflows_dir / "implement.yaml").write_text(
                yaml.dump(
                    {
                        "name": "Implement",
                        "description": "Implementation workflow",
                        "start_at": "s",
                        "states": {
                            "s": {
                                "type": "task",
                                "provider": "system",
                                "command": "echo implement",
                                "result_path": "$.result",
                                "end": True,
                            }
                        },
                    }
                )
            )

            mock_provider = MagicMock()
            mock_provider.execute.return_value = MagicMock(
                exit_code=0,
                stdout="implement.yaml",
                stderr="",
            )

            workflows = discover_workflows(workflows_dir)
            config = WorkflowSelectorConfig(
                provider="claude", model="claude-sonnet-4-6"
            )

            with patch("fdsx.core.selector.get_provider", return_value=mock_provider):
                selected = select_workflow(
                    "Implement a new API endpoint for user authentication",
                    workflows,
                    config,
                )

            assert selected.name == "implement.yaml"

    def test_empty_workflows_dir_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            workflows = discover_workflows(workflows_dir)
            config = WorkflowSelectorConfig(
                provider="claude", model="claude-sonnet-4-6"
            )

            with pytest.raises(ValueError, match="No workflows found"):
                select_workflow("Do something", workflows, config)


class TestResolveWorkflowForTaskIntegration:
    def test_auto_workflow_returns_workflow(self):
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
                                "command": "echo only",
                                "result_path": "$.result",
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
                task_description="Build a feature",
                workflows_dir=workflows_dir,
                selector_config=config,
                auto_workflow=True,
            )

            assert result is not None
            assert result.name == "only.yaml"

    def test_empty_workflows_dir_raises_on_resolve(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workflows_dir = Path(tmpdir)
            config = WorkflowSelectorConfig(
                provider="claude", model="claude-sonnet-4-6"
            )

            with pytest.raises(ValueError, match="No workflows found"):
                resolve_workflow_for_task(
                    task_description="Build a feature",
                    workflows_dir=workflows_dir,
                    selector_config=config,
                    auto_workflow=True,
                )

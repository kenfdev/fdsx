"""End-to-end coverage for the ``fdsx resolve`` command."""

from pathlib import Path

import pytest
import yaml

from tests.e2e.cli_test_utils import run_fdsx


def test_resolve_prints_workflow_yaml_without_adding_defaults(tmp_path: Path) -> None:
    """A valid workflow is printed as YAML without model defaults."""
    (tmp_path / ".fdsx").mkdir()
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: inspect
description: Inspect a workflow
start_at: finish
states:
  finish:
    type: task
    provider: system
    command: echo done
    end: true
""".lstrip(),
        encoding="utf-8",
    )

    result = run_fdsx(["resolve", str(workflow)], cwd=tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""
    assert yaml.safe_load(result.stdout) == {
        "name": "inspect",
        "description": "Inspect a workflow",
        "start_at": "finish",
        "states": {
            "finish": {
                "type": "task",
                "provider": "system",
                "command": "echo done",
                "end": True,
            }
        },
    }


def test_resolve_expands_prompt_file_and_preserves_its_source(
    tmp_path: Path,
) -> None:
    """Prompt file contents are shown inline with the declared path as a comment."""
    (tmp_path / ".fdsx").mkdir()
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "review.md").write_text(
        "Review the code.\nFocus on safety.\n",
        encoding="utf-8",
    )
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: review
description: Review code
start_at: review
states:
  review:
    type: task
    provider: claude
    model: sonnet
    prompt_file: prompts/review.md
    end: true
""".lstrip(),
        encoding="utf-8",
    )

    result = run_fdsx(["resolve", str(workflow)], cwd=tmp_path)

    assert result.returncode == 0
    assert result.stderr == ""
    assert "    # from prompt_file: prompts/review.md\n" in result.stdout
    assert "    prompt_template: |\n" in result.stdout
    resolved = yaml.safe_load(result.stdout)
    assert resolved["states"]["review"]["prompt_template"] == (
        "Review the code.\nFocus on safety.\n"
    )
    assert "prompt_file" not in resolved["states"]["review"]


def test_resolve_expands_prompt_file_in_parallel_branch(tmp_path: Path) -> None:
    """Prompt files nested in parallel branches are shown inline."""
    (tmp_path / ".fdsx").mkdir()
    (tmp_path / "branch.md").write_text("Review in parallel.\n", encoding="utf-8")
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: parallel-review
description: Review in parallel
start_at: review
states:
  review:
    type: parallel
    branches:
      - provider: claude
        model: sonnet
        prompt_file: branch.md
    result_path: $.reviews
    end: true
""".lstrip(),
        encoding="utf-8",
    )

    result = run_fdsx(["resolve", str(workflow)], cwd=tmp_path)

    assert result.returncode == 0
    assert "      # from prompt_file: branch.md\n" in result.stdout
    branch = yaml.safe_load(result.stdout)["states"]["review"]["branches"][0]
    assert branch["prompt_template"] == "Review in parallel.\n"
    assert "prompt_file" not in branch


def test_resolve_expands_prompt_file_in_map_iterator(tmp_path: Path) -> None:
    """Prompt files nested in map iterator tasks are shown inline."""
    (tmp_path / ".fdsx").mkdir()
    (tmp_path / "item.md").write_text("Review {item}.\n", encoding="utf-8")
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: map-review
description: Review each item
start_at: setup
states:
  setup:
    type: pass
    parameters:
      $.items:
        - one
    next: review
  review:
    type: map
    items_path: $.items
    iterator:
      states:
        - name: inspect
          type: task
          provider: claude
          model: sonnet
          prompt_file: item.md
          result_path: $.inspection
    result_path: $.reviews
    end: true
""".lstrip(),
        encoding="utf-8",
    )

    result = run_fdsx(["resolve", str(workflow)], cwd=tmp_path)

    assert result.returncode == 0
    assert "        # from prompt_file: item.md\n" in result.stdout
    iterator_state = yaml.safe_load(result.stdout)["states"]["review"]["iterator"][
        "states"
    ][0]
    assert iterator_state["prompt_template"] == "Review {item}.\n"
    assert "prompt_file" not in iterator_state


def test_resolve_collects_referenced_external_profiles_with_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Referenced config profiles are shown without expanding state references."""
    project_config = tmp_path / ".fdsx" / "config.yaml"
    project_config.parent.mkdir()
    project_config.write_text(
        """
profiles:
  project_reviewer:
    provider: claude
    model: sonnet
""".lstrip(),
        encoding="utf-8",
    )
    global_config = tmp_path / "xdg" / "fdsx" / "config.yaml"
    global_config.parent.mkdir(parents=True)
    global_config.write_text(
        """
profiles:
  unused:
    provider: codex
    model: unused-model
  global_writer:
    provider: codex
    model: gpt-5
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: profiles
description: Inspect profiles
start_at: review
profiles:
  local:
    provider: claude
    model: opus
states:
  review:
    type: task
    profile: project_reviewer
    prompt_template: Review
    next: write
  write:
    type: task
    profile: global_writer
    prompt_template: Write
    next: finish
  finish:
    type: task
    profile: local
    prompt_template: Finish
    end: true
""".lstrip(),
        encoding="utf-8",
    )

    result = run_fdsx(["resolve", str(workflow)], cwd=tmp_path)

    assert result.returncode == 0
    assert (
        f"  # from project config: {project_config.resolve()}\n"
        "  project_reviewer:\n" in result.stdout
    )
    assert (
        f"  # from global config: {global_config.resolve()}\n"
        "  global_writer:\n" in result.stdout
    )
    resolved = yaml.safe_load(result.stdout)
    assert list(resolved["profiles"]) == ["local", "project_reviewer", "global_writer"]
    assert "unused" not in resolved["profiles"]
    assert resolved["states"]["review"]["profile"] == "project_reviewer"
    assert "provider" not in resolved["states"]["review"]


def test_resolve_inserts_external_profiles_before_states(tmp_path: Path) -> None:
    """A generated top-level profiles section precedes its state references."""
    config = tmp_path / ".fdsx" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        """
profiles:
  reviewer:
    provider: claude
    model: sonnet
""".lstrip(),
        encoding="utf-8",
    )
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: review
description: Review code
start_at: review
states:
  review:
    type: task
    profile: reviewer
    prompt_template: Review
    end: true
""".lstrip(),
        encoding="utf-8",
    )

    result = run_fdsx(["resolve", str(workflow)], cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout.index("\nprofiles:\n") < result.stdout.index("\nstates:\n")


def test_resolve_collects_profiles_from_nested_execution_steps(tmp_path: Path) -> None:
    """Profile references in branches, fallbacks, and iterators are collected."""
    config = tmp_path / ".fdsx" / "config.yaml"
    config.parent.mkdir()
    config.write_text(
        """
profiles:
  branch_reviewer:
    provider: claude
    model: sonnet
  classifier:
    provider: codex
    model: gpt-5
  item_reviewer:
    provider: claude
    model: opus
""".lstrip(),
        encoding="utf-8",
    )
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: nested-profiles
description: Inspect nested profiles
start_at: setup
states:
  setup:
    type: pass
    parameters:
      $.items:
        - one
    next: parallel_review
  parallel_review:
    type: parallel
    branches:
      - profile: branch_reviewer
        prompt_template: Review
        extract:
          strategy: [keyword]
          pattern: APPROVED
          result_path: $.decision
          fallback:
            type: llm_classify
            profile: classifier
            prompt: Classify
    result_path: $.parallel
    next: map_review
  map_review:
    type: map
    items_path: $.items
    iterator:
      states:
        - name: inspect
          type: task
          profile: item_reviewer
          prompt_template: Review {item}
          result_path: $.inspection
    result_path: $.mapped
    end: true
""".lstrip(),
        encoding="utf-8",
    )

    result = run_fdsx(["resolve", str(workflow)], cwd=tmp_path)

    assert result.returncode == 0
    assert list(yaml.safe_load(result.stdout)["profiles"]) == [
        "branch_reviewer",
        "classifier",
        "item_reviewer",
    ]


def test_resolve_reports_invalid_config_without_printing_yaml(tmp_path: Path) -> None:
    """Config parse failures are reported as validation errors on stderr."""
    config = tmp_path / ".fdsx" / "config.yaml"
    config.parent.mkdir()
    config.write_text("profiles: [broken\n", encoding="utf-8")
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: review
description: Review code
start_at: finish
states:
  finish:
    type: task
    provider: system
    command: echo done
    end: true
""".lstrip(),
        encoding="utf-8",
    )

    result = run_fdsx(["resolve", str(workflow)], cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "Error: Invalid YAML in config file" in result.stderr
    assert "Traceback" not in result.stderr


def test_resolve_expands_yaml_aliases(tmp_path: Path) -> None:
    """YAML anchors and aliases are rendered as independent values."""
    (tmp_path / ".fdsx").mkdir()
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        """
name: aliases
description: Expand YAML aliases
start_at: setup
states:
  setup:
    type: pass
    parameters:
      $.first: &shared
        value: one
      $.second: *shared
    end: true
""".lstrip(),
        encoding="utf-8",
    )

    result = run_fdsx(["resolve", str(workflow)], cwd=tmp_path)

    assert result.returncode == 0
    assert "&id" not in result.stdout
    assert "*id" not in result.stdout
    parameters = yaml.safe_load(result.stdout)["states"]["setup"]["parameters"]
    assert parameters == {
        "$.first": {"value": "one"},
        "$.second": {"value": "one"},
    }

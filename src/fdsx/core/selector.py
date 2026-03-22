"""Workflow auto-selection system.

Discovers workflow files from a directory, uses an LLM to select the most
appropriate workflow based on task description, and supports both auto and
confirm modes.
"""

from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

from fdsx.core.config import WorkflowSelectorConfig
from fdsx.core.loader import load_flow
from fdsx.providers.base import get_provider


def discover_workflows(workflows_dir: Path) -> list[tuple[Path, str]]:
    """Discover all workflow YAML files in the given directory.

    Loads each discovered YAML file, extracts the description field, and returns
    a list of (path, description) tuples. Files with invalid YAML are skipped
    with a warning.

    Args:
        workflows_dir: Directory containing workflow YAML files.

    Returns:
        List of (workflow_path, description) tuples sorted by filename.
        Returns an empty list if the directory does not exist.

    Raises:
        ValueError: If the workflows directory is a symlink.
    """
    if not workflows_dir.exists():
        return []
    if workflows_dir.is_symlink():
        raise ValueError(f"Workflows directory must not be a symlink: {workflows_dir}")

    yaml_files = sorted(workflows_dir.glob("*.yaml"))
    results: list[tuple[Path, str]] = []

    for fp in yaml_files:
        if fp.is_symlink():
            warnings.warn(
                f"Skipping symlinked workflow file: {fp}",
                RuntimeWarning,
            )
            continue
        if not fp.is_file():
            warnings.warn(
                f"Skipping non-regular workflow file: {fp}",
                RuntimeWarning,
            )
            continue
        try:
            flow, errors = load_flow(fp)
            if flow is None:
                warnings.warn(
                    f"Skipping invalid workflow file {fp}: {', '.join(errors)}",
                    RuntimeWarning,
                )
                continue
            results.append((fp, flow.description))
        except Exception as e:
            warnings.warn(
                f"Skipping unparseable workflow file {fp}: {e}",
                RuntimeWarning,
            )

    return results


def _build_workflow_selection_prompt(
    task_description: str, workflows: list[tuple[Path, str]]
) -> str:
    """Build the LLM prompt for workflow selection.

    Args:
        task_description: The task/goal description to match against workflows.
        workflows: List of (path, description) tuples for discovered workflows.

    Returns:
        The formatted prompt string.
    """
    workflow_list = "\n".join(
        f"- **{fp.name}**: {description}" for fp, description in workflows
    )

    return f"""You are a workflow selector. Given a task description, select the most appropriate workflow from the available options.

TASK:
{task_description}

AVAILABLE WORKFLOWS:
{workflow_list}

INSTRUCTIONS:
1. Analyze the task description above
2. Consider which workflow best matches the task's goal and requirements
3. Return ONLY the filename of the selected workflow (e.g., "plan-implement-review.yaml")
4. Do not include any explanations, markdown, or additional text — just the filename

OUTPUT FORMAT:
Return the exact filename string only."""


def _parse_workflow_selection(response: str) -> str:
    """Parse the LLM response to extract the selected workflow filename.

    Handles markdown code blocks, quotes, and whitespace. Returns the raw
    cleaned string without strict validation — the calling code handles
    matching against known workflows.

    Args:
        response: The raw LLM response text.

    Returns:
        The extracted workflow filename (may not have .yaml extension).

    Raises:
        ValueError: If no content can be extracted.
    """
    cleaned = response.strip()

    code_block_patterns = [
        r"```(?:yaml)?\s*(.*?)\s*```",
        r"```\s*(.*?)\s*```",
    ]
    for pattern in code_block_patterns:
        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()

    cleaned = cleaned.strip("\"'")
    cleaned = cleaned.strip()

    if not cleaned:
        raise ValueError(f"Empty workflow selection from response: {response}")

    return cleaned


def select_workflow(
    task_description: str,
    workflows: list[tuple[Path, str]],
    selector_config: WorkflowSelectorConfig,
) -> Path:
    """Select the most appropriate workflow for the given task description.

    Selection rules:
    - If exactly one workflow is available, it is returned directly (FR-5.7).
    - If no workflows are available, raises ValueError (FR-5.8).
    - If multiple workflows are available, calls the LLM to select one.

    Args:
        task_description: The task/goal description to match against workflows.
        workflows: List of (workflow_path, description) tuples.
        selector_config: Configuration for the workflow selector LLM.

    Returns:
        Path to the selected workflow file.

    Raises:
        ValueError: If no workflows are available.
    """
    if len(workflows) == 0:
        raise ValueError(
            "No workflows found in the workflows directory. "
            "Please add workflow YAML files or check your workflows_dir configuration."
        )

    if len(workflows) == 1:
        return workflows[0][0]

    provider = get_provider(selector_config.provider)

    prompt = _build_workflow_selection_prompt(task_description, workflows)

    result = provider.execute(
        prompt=prompt,
        model=selector_config.model,
    )

    if result.exit_code != 0:
        raise RuntimeError(f"Workflow selector failed: {result.stderr}")

    selected_name = _parse_workflow_selection(result.stdout)

    # Strategy 1: exact filename match
    for wf_path, _ in workflows:
        if wf_path.name == selected_name:
            return wf_path

    # Strategy 2: append .yaml if missing
    if not selected_name.endswith(".yaml"):
        candidate = selected_name + ".yaml"
        for wf_path, _ in workflows:
            if wf_path.name == candidate:
                return wf_path

    # Strategy 3: check if exactly one known filename or stem appears in the response
    matches: list[Path] = []
    for wf_path, _ in workflows:
        if wf_path.name in selected_name:
            if wf_path not in matches:
                matches.append(wf_path)
    # Only try stem matching if filename matching found nothing
    if not matches:
        response_words = selected_name.split()
        for wf_path, _ in workflows:
            if wf_path.stem in response_words:
                if wf_path not in matches:
                    matches.append(wf_path)
    if len(matches) == 1:
        return matches[0]

    raise ValueError(
        f"LLM selected workflow '{selected_name}' which does not match any available workflow. "
        f"Available: {[wf_path.name for wf_path, _ in workflows]}"
    )


def confirm_workflow_selection(
    workflow_path: Path,
    task_description: str,
) -> bool:
    """Prompt the user to confirm a workflow selection.

    Args:
        workflow_path: Path to the selected workflow.
        task_description: The task description that was used for selection.

    Returns:
        True if the user approves, False if they reject.
    """
    print(f"\nSelected workflow: {workflow_path.name}", file=sys.stderr)
    print(
        f"  Task: {task_description[:60]}{'...' if len(task_description) > 60 else ''}",
        file=sys.stderr,
    )
    print(
        "  (y) Accept and run  (n) Reject and choose manually  (l) List all workflows",
        file=sys.stderr,
    )

    while True:
        response = input("Choice: ").strip().lower()
        if response == "y":
            return True
        elif response == "n":
            return False
        elif response == "l":
            return False
        else:
            print("Invalid choice. Enter 'y', 'n', or 'l'.", file=sys.stderr)


def pick_workflow_manually(
    workflows: list[tuple[Path, str]],
) -> Path | None:
    """Present a numbered list of workflows and let the user pick one.

    Args:
        workflows: List of (workflow_path, description) tuples.

    Returns:
        The selected workflow path, or None if the user cancels.
    """
    print("\nAvailable workflows:", file=sys.stderr)
    print("-" * 60, file=sys.stderr)
    for i, (wf_path, description) in enumerate(workflows, 1):
        desc_preview = (
            description[:50] + "..." if len(description) > 50 else description
        )
        print(f"  {i}. {wf_path.name}", file=sys.stderr)
        print(f"     {desc_preview}", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    while True:
        response = input("Enter number (or 'c' to cancel): ").strip()
        if response.lower() == "c":
            return None
        try:
            idx = int(response) - 1
            if 0 <= idx < len(workflows):
                return workflows[idx][0]
            else:
                print(f"Invalid number. Enter 1-{len(workflows)}.", file=sys.stderr)
        except ValueError:
            print("Invalid input. Enter a number or 'c'.", file=sys.stderr)


def resolve_workflow_for_task(
    task_description: str,
    workflows_dir: Path,
    selector_config: WorkflowSelectorConfig,
    auto_workflow: bool,
) -> Path | None:
    """Resolve which workflow to use for a task via auto-discovery and LLM selection.

    Args:
        task_description: Description of the task.
        workflows_dir: Directory to discover workflows from.
        selector_config: Configuration for the workflow selector LLM.
        auto_workflow: If True, skip confirmation prompt.

    Returns:
        Path to the selected workflow, or None if the user cancels manual pick.
    """
    discovered = discover_workflows(workflows_dir)

    if len(discovered) == 0:
        raise ValueError(
            f"No workflows found in {workflows_dir}. "
            "Add workflow YAML files or configure workflows_dir in your config."
        )

    selected = select_workflow(task_description, discovered, selector_config)

    if auto_workflow:
        return selected

    approved = confirm_workflow_selection(selected, task_description)
    if approved:
        return selected

    picked = pick_workflow_manually(discovered)
    return picked

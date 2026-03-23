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


def discover_workflows(workflows_dir: Path) -> list[tuple[Path, str, str]]:
    """Discover all workflow files in the given directory.

    Scans for:
    - Flat workflow files: ``*.yaml`` and ``*.yml`` (yaml takes precedence over yml)
    - Directory workflows: subdirectories containing ``workflow.yaml`` or ``workflow.yml``

    Directory names shadow flat files with the same stem.

    Args:
        workflows_dir: Directory containing workflow files and subdirectories.

    Returns:
        List of ``(workflow_path, description, display_name)`` tuples sorted by
        *display_name*.  Returns an empty list if the directory does not exist.

    Raises:
        ValueError: If the workflows directory is a symlink.
    """
    if not workflows_dir.exists():
        return []
    if workflows_dir.is_symlink():
        raise ValueError(f"Workflows directory must not be a symlink: {workflows_dir}")

    results: list[tuple[Path, str, str]] = []
    dir_names: set[str] = set()

    # --- Phase 1: directory workflows ---
    for entry in sorted(workflows_dir.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            if entry.is_dir() and entry.is_symlink():
                warnings.warn(
                    f"Skipping symlinked workflow directory: {entry}",
                    RuntimeWarning,
                )
            continue

        wf_yaml = entry / "workflow.yaml"
        wf_yml = entry / "workflow.yml"

        wf_file: Path | None = None
        if wf_yaml.exists() and not wf_yaml.is_symlink():
            wf_file = wf_yaml
        elif wf_yml.exists() and not wf_yml.is_symlink():
            wf_file = wf_yml
        else:
            if (wf_yaml.exists() and wf_yaml.is_symlink()) or (
                wf_yml.exists() and wf_yml.is_symlink()
            ):
                warnings.warn(
                    f"Skipping symlinked workflow file in directory: {entry}",
                    RuntimeWarning,
                )
            continue

        display_name = entry.name
        try:
            flow, errors = load_flow(wf_file)
            if flow is None:
                warnings.warn(
                    f"Skipping invalid workflow file {wf_file}: {', '.join(errors)}",
                    RuntimeWarning,
                )
                continue
            results.append((wf_file, flow.description, display_name))
            dir_names.add(display_name)
        except Exception as e:
            warnings.warn(
                f"Skipping unparseable workflow file {wf_file}: {e}",
                RuntimeWarning,
            )

    # --- Phase 2: flat files (*.yaml then *.yml, yaml takes precedence) ---
    seen_stems: set[str] = set()
    for ext in ("*.yaml", "*.yml"):
        for fp in sorted(workflows_dir.glob(ext)):
            if fp.stem in dir_names:
                continue  # directory shadows flat file
            if fp.stem in seen_stems:
                continue  # .yaml already found, skip .yml
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
                results.append((fp, flow.description, fp.stem))
                seen_stems.add(fp.stem)
            except Exception as e:
                warnings.warn(
                    f"Skipping unparseable workflow file {fp}: {e}",
                    RuntimeWarning,
                )

    results.sort(key=lambda t: t[2])
    return results


def _build_workflow_selection_prompt(
    task_description: str, workflows: list[tuple[Path, str, str]]
) -> str:
    """Build the LLM prompt for workflow selection.

    Args:
        task_description: The task/goal description to match against workflows.
        workflows: List of (path, description, display_name) tuples.

    Returns:
        The formatted prompt string.
    """
    workflow_list = "\n".join(
        f"- **{display_name}**: {description}"
        for _, description, display_name in workflows
    )

    return f"""You are a workflow selector. Given a task description, select the most appropriate workflow from the available options.

TASK:
{task_description}

AVAILABLE WORKFLOWS:
{workflow_list}

INSTRUCTIONS:
1. Analyze the task description above
2. Consider which workflow best matches the task's goal and requirements
3. Return ONLY the name of the selected workflow (e.g., "plan-implement-review")
4. Do not include any explanations, markdown, or additional text — just the workflow name

OUTPUT FORMAT:
Return the exact workflow name string only."""


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
    workflows: list[tuple[Path, str, str]],
    selector_config: WorkflowSelectorConfig,
) -> Path:
    """Select the most appropriate workflow for the given task description.

    Selection rules:
    - If exactly one workflow is available, it is returned directly (FR-5.7).
    - If no workflows are available, raises ValueError (FR-5.8).
    - If multiple workflows are available, calls the LLM to select one.

    Args:
        task_description: The task/goal description to match against workflows.
        workflows: List of (workflow_path, description, display_name) tuples.
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

    # Strategy 1: exact display_name match
    for wf_path, _, display_name in workflows:
        if display_name == selected_name:
            return wf_path

    # Strategy 2: exact filename match (backward compat)
    for wf_path, _, _ in workflows:
        if wf_path.name == selected_name:
            return wf_path

    # Strategy 3: append .yaml if missing
    if not selected_name.endswith((".yaml", ".yml")):
        for ext in (".yaml", ".yml"):
            candidate = selected_name + ext
            for wf_path, _, _ in workflows:
                if wf_path.name == candidate:
                    return wf_path

    # Strategy 4: whitespace-boundary matching to avoid substring collisions
    # (e.g. "plan" should not match inside "planning"; "review" should not
    # match inside "review-code").
    matches: list[Path] = []
    for wf_path, _, display_name in workflows:
        pattern = r'(?:^|\s)' + re.escape(display_name) + r'(?:$|\s)'
        if re.search(pattern, selected_name):
            if wf_path not in matches:
                matches.append(wf_path)
    if not matches:
        for wf_path, _, display_name in workflows:
            pattern = r'(?:^|\s)' + re.escape(wf_path.name) + r'(?:$|\s)'
            if re.search(pattern, selected_name):
                if wf_path not in matches:
                    matches.append(wf_path)
    if not matches:
        # Fall back to word-split matching
        response_words = selected_name.split()
        for wf_path, _, display_name in workflows:
            if display_name in response_words:
                if wf_path not in matches:
                    matches.append(wf_path)
    if len(matches) == 1:
        return matches[0]

    raise ValueError(
        f"LLM selected workflow '{selected_name}' which does not match any available workflow. "
        f"Available: {[dn for _, _, dn in workflows]}"
    )


def confirm_workflow_selection(
    workflow_path: Path,
    task_description: str,
    display_name: str | None = None,
) -> bool:
    """Prompt the user to confirm a workflow selection.

    Args:
        workflow_path: Path to the selected workflow.
        task_description: The task description that was used for selection.
        display_name: Human-readable name for the workflow. Falls back to
            ``workflow_path.name`` when not provided.

    Returns:
        True if the user approves, False if they reject.
    """
    name = display_name or workflow_path.name
    print(f"\nSelected workflow: {name}", file=sys.stderr)
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
    workflows: list[tuple[Path, str, str]],
) -> Path | None:
    """Present a numbered list of workflows and let the user pick one.

    Args:
        workflows: List of (workflow_path, description, display_name) tuples.

    Returns:
        The selected workflow path, or None if the user cancels.
    """
    print("\nAvailable workflows:", file=sys.stderr)
    print("-" * 60, file=sys.stderr)
    for i, (wf_path, description, display_name) in enumerate(workflows, 1):
        desc_preview = (
            description[:50] + "..." if len(description) > 50 else description
        )
        print(f"  {i}. {display_name}", file=sys.stderr)
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

    # Look up display_name for the selected workflow
    selected_display_name: str | None = None
    for wf_path, _, dn in discovered:
        if wf_path == selected:
            selected_display_name = dn
            break

    approved = confirm_workflow_selection(selected, task_description, display_name=selected_display_name)
    if approved:
        return selected

    picked = pick_workflow_manually(discovered)
    return picked

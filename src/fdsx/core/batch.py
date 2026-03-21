import json
import re as _re
import shutil
import sys
from pathlib import Path
from typing import Any

from fdsx.core.config import TaskSplitterConfig
from fdsx.display.terminal import _sanitize_output
from fdsx.models.flow import Flow
from fdsx.models.task import TaskEntry, TaskFile, save_task_file
from fdsx.providers.base import get_provider


TASKS_DIR = ".fdsx/tasks"
COMPLETED_SUBDIR = "completed"


def _slugify(text: str, max_length: int = 40) -> str:
    """Convert text to a URL-safe slug for use in filenames."""
    slug = text.lower()
    slug = _re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = _re.sub(r"[\s_]+", "-", slug).strip("-")
    slug = _re.sub(r"-+", "-", slug)
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "task"


def split_tasks(
    task_content: str, flow: Flow, task_splitter: TaskSplitterConfig
) -> list[str]:
    """Invoke the task_splitter LLM to split the task file content into individual tasks.

    Args:
        task_content: The content of the task file
        flow: The flow definition
        task_splitter: The task splitter configuration

    Returns:
        List of task description strings
    """
    provider = get_provider(task_splitter.provider)

    state_names = list(flow.states.keys())
    input_vars = _extract_input_variables(flow)

    prompt = _build_task_split_prompt(task_content, state_names, input_vars)

    result = provider.execute(
        prompt=prompt,
        model=task_splitter.model,
    )

    if result.exit_code != 0:
        raise RuntimeError(f"Task splitter failed: {result.stderr}")

    try:
        groups = _parse_structured_tasks(result.stdout)
        flattened = [entry.description for group in groups for entry in group]
        return flattened
    except ValueError:
        return _parse_task_list(result.stdout)


def split_tasks_to_groups(
    task_content: str,
    task_splitter: TaskSplitterConfig,
    state_names: list[str] | None = None,
    input_vars: set[str] | None = None,
) -> list[list[TaskEntry]]:
    """Invoke the task_splitter LLM to split task content into file groups.

    This is the standalone version used by the split CLI command that doesn't
    require a flow object.

    Args:
        task_content: The content of the task file
        task_splitter: The task splitter configuration
        state_names: Optional list of state names for context
        input_vars: Optional set of input variables for context

    Returns:
        List of file groups. Each group contains sequentially-dependent
        TaskEntry objects that belong in the same file.

    Raises:
        RuntimeError: If the LLM call fails
        ValueError: If the response cannot be parsed
    """
    provider = get_provider(task_splitter.provider)

    prompt = _build_task_split_prompt(task_content, state_names, input_vars)

    result = provider.execute(
        prompt=prompt,
        model=task_splitter.model,
    )

    if result.exit_code != 0:
        raise RuntimeError(f"Task splitter failed: {result.stderr}")

    return _parse_structured_tasks(result.stdout)


def _build_task_split_prompt(
    task_content: str, state_names: list[str] | None, input_vars: set[str] | None
) -> str:
    """Build the prompt for the task splitter LLM.

    Args:
        task_content: The content of the task file
        state_names: List of state names in the workflow (optional for standalone split)
        input_vars: Set of input variable names (optional for standalone split)
    """
    states_desc = ", ".join(state_names) if state_names else "any workflow"
    input_vars_desc = ", ".join(input_vars) if input_vars else "task"

    prompt = f"""You are a task splitter. Given a batch of work, split it into individual executable tasks and organize them into file groups.

The workflow has these states: {states_desc}
The workflow accepts these input variables: {input_vars_desc}

TASK CONTENT:
{task_content}

INSTRUCTIONS:
1. Analyze the task content above
2. Split it into individual, self-contained task descriptions
3. Group tasks that DEPEND on each other sequentially into the same group (they will be executed in order within one file)
4. Place independent tasks (no dependencies between them) in SEPARATE groups (each becomes its own file)
5. Within each group, order tasks by their sequential dependency (first task executed first)
6. Output ONLY valid JSON in the format described below

OUTPUT FORMAT:
Return a JSON array of file groups. Each group is an array of task objects that belong in the same file.
```json
[
  [
    {{"description": "Independent task A"}}
  ],
  [
    {{"description": "Step 1 of related work"}},
    {{"description": "Step 2 that depends on step 1"}}
  ],
  [
    {{"description": "Independent task B"}}
  ]
]
```

IMPORTANT: Output ONLY the JSON array, no additional text, explanations, or markdown formatting."""

    return prompt


def _extract_input_variables(flow: Flow) -> set[str]:
    """Extract expected input variables from the flow.

    Scans prompt_template fields for {variable} references to identify actual inputs.
    Also includes 'task' as the standard batch input variable.
    """
    import re

    from fdsx.models.flow import ParallelState, TaskState

    input_vars: set[str] = {"task"}
    # Matches {var}, {var.field}, {var[0]} etc.
    var_pattern = r"\{(\w+(?:\.\w+)*(?:\[\d+\])?)\}"

    for state_name, state in flow.states.items():
        if isinstance(state, TaskState) and state.prompt_template:
            for match in re.findall(var_pattern, state.prompt_template):
                root = match.split(".")[0].split("[")[0]
                input_vars.add(root)
        elif isinstance(state, ParallelState):
            for branch in state.branches:
                if branch.prompt_template:
                    for match in re.findall(var_pattern, branch.prompt_template):
                        root = match.split(".")[0].split("[")[0]
                        input_vars.add(root)

    return input_vars


def _parse_task_list(response: str) -> list[str]:
    """Parse the LLM response into a list of task strings."""
    tasks: list[str] = []
    lines = response.strip().split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line[0].isdigit() and ". " in line:
            task = line.split(". ", 1)[1].strip()
            tasks.append(task)
        else:
            tasks.append(line)

    return tasks


def _parse_structured_tasks(response: str) -> list[list[TaskEntry]]:
    """Parse the LLM JSON response into a list of file groups.

    Each group is a list of TaskEntry objects that belong in the same file.
    Tasks within a group execute sequentially; separate groups become
    separate files.

    Args:
        response: The LLM response containing JSON

    Returns:
        List of file groups, each containing TaskEntry objects

    Raises:
        ValueError: If JSON parsing fails or format is invalid
    """
    cleaned = response.strip()

    code_block_patterns = [
        r"```json\s*(.*?)\s*```",
        r"```\s*(.*?)\s*```",
    ]
    for pattern in code_block_patterns:
        import re

        match = re.search(pattern, cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
            break

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON from LLM response: {e}") from e

    if not isinstance(data, list):
        raise ValueError(
            f"Expected JSON array of file groups, got {type(data).__name__}"
        )

    groups: list[list[TaskEntry]] = []
    for i, group in enumerate(data):
        if not isinstance(group, list):
            raise ValueError(
                f"Group {i} must be an array of tasks, got {type(group).__name__}"
            )

        entries: list[TaskEntry] = []
        for j, task in enumerate(group):
            if not isinstance(task, dict):
                raise ValueError(
                    f"Task {j} in group {i} must be an object, got {type(task).__name__}"
                )
            if "description" not in task:
                raise ValueError(
                    f"Task {j} in group {i} missing required 'description' field"
                )
            entries.append(TaskEntry(description=task["description"]))
        groups.append(entries)

    return groups


def _scan_max_task_index(tasks_dir: Path) -> int:
    """Scan tasks_dir and tasks_dir/completed/ for existing numeric file indices.

    Looks for files whose names start with one or more digits followed by a hyphen
    (e.g. ``001-some-task.yaml``).  Returns the highest index found, or 0 if no
    such files exist.

    Args:
        tasks_dir: The active tasks directory to scan (completed/ is derived from it).

    Returns:
        Maximum index found across both directories, or 0 if none found.
    """
    max_idx = 0
    dirs_to_scan = [tasks_dir, tasks_dir / COMPLETED_SUBDIR]
    for scan_dir in dirs_to_scan:
        if not scan_dir.exists() or not scan_dir.is_dir():
            continue
        for f in scan_dir.glob("*.yaml"):
            m = _re.match(r"^(\d+)-", f.name)
            if m:
                idx = int(m.group(1))
                if idx > max_idx:
                    max_idx = idx
    return max_idx


def write_task_files(groups: list[list[TaskEntry]], tasks_dir: Path) -> list[Path]:
    """Write task groups to numbered YAML files in the tasks directory.

    Creates files in the format: tasks_dir/NNN-<slug>.yaml where NNN continues
    from the highest existing index found in both tasks_dir and
    tasks_dir/completed/.  Each file contains sequentially-dependent tasks from
    one file group.

    Args:
        groups: List of file groups, each containing TaskEntry objects
        tasks_dir: Directory to write task files to

    Returns:
        List of created file paths

    Raises:
        ValueError: If tasks_dir is a symlink or other security checks fail
    """
    current = tasks_dir
    while current != current.parent:
        if current.exists() and current.is_symlink():
            raise ValueError(f"Refusing to write: ancestor is a symlink: {current}")
        current = current.parent

    tasks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    base_index = _scan_max_task_index(tasks_dir)

    created_files: list[Path] = []
    for i, group in enumerate(groups):
        if not group:
            continue

        task_file = TaskFile(entries=group)
        slug = _slugify(group[0].description)
        file_path = tasks_dir / f"{base_index + i + 1:03d}-{slug}.yaml"
        save_task_file(file_path, task_file)
        created_files.append(file_path)

    return created_files


def move_task_to_completed(file_path: Path) -> None:
    """Move a task file to the ``completed/`` subdirectory alongside it.

    The ``completed/`` directory is created automatically if it does not exist.
    The original filename is preserved.

    Args:
        file_path: Absolute (or relative) path to the task YAML file to move.

    Raises:
        ValueError: If a symlink is detected anywhere in the ancestor path of
            the destination directory.
        FileExistsError: If a file with the same name already exists inside
            ``completed/``.
    """
    completed_dir = file_path.parent / COMPLETED_SUBDIR

    # Security check: no symlinks in the ancestor path
    current = completed_dir
    while current != current.parent:
        if current.exists() and current.is_symlink():
            raise ValueError(f"Refusing to write: ancestor is a symlink: {current}")
        current = current.parent

    completed_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    dest = completed_dir / file_path.name
    if dest.exists():
        raise FileExistsError(
            f"Destination already exists in completed/: {dest}"
        )

    shutil.move(str(file_path), str(dest))


def display_task_list(tasks: list[str]) -> bool:
    """Display the split tasks in numbered format and prompt for confirmation.

    Args:
        tasks: List of task description strings

    Returns:
        True if user approves, False if rejected
    """
    print("The following tasks will be executed:", file=sys.stderr)
    print("-" * 60, file=sys.stderr)

    for i, task in enumerate(tasks, 1):
        task_preview = task[:70] + "..." if len(task) > 70 else task
        print(f"  {i}. {_sanitize_output(task_preview)}", file=sys.stderr)

    print("-" * 60, file=sys.stderr)

    while True:
        response = input("Approve task list? (y/n): ").strip().lower()
        if response == "y":
            return True
        elif response == "n":
            return False


def display_batch_summary(results: list[dict[str, Any]]) -> None:
    """Display a summary table of all task results.

    Args:
        results: List of result dicts with task_index, task_description, thread_id, status, error
    """
    print("\n" + "=" * 80, file=sys.stderr)
    print("BATCH EXECUTION SUMMARY", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(
        f"{'#':<4} {'STATUS':<12} {'THREAD_ID':<36} {'TASK':<25}",
        file=sys.stderr,
    )
    print("-" * 80, file=sys.stderr)

    for result in results:
        task_idx = result.get("task_index", 0) + 1
        status = result.get("status", "unknown")
        thread_id = result.get("thread_id", "")[:36]
        task_desc = result.get("task_description", "")[:25]

        status_symbol = "✓" if status == "completed" else "✗"

        print(
            f"{task_idx:<4} {status_symbol} {status:<10} {_sanitize_output(thread_id):<36} {_sanitize_output(task_desc):<25}",
            file=sys.stderr,
        )

        if result.get("error"):
            error_preview = result["error"][:60]
            print(f"       Error: {_sanitize_output(error_preview)}", file=sys.stderr)

    print("-" * 80, file=sys.stderr)

    total = len(results)
    succeeded = sum(1 for r in results if r.get("status") == "completed")
    failed = total - succeeded

    print(
        f"Total: {total} | Succeeded: {succeeded} | Failed: {failed}", file=sys.stderr
    )
    print("=" * 80, file=sys.stderr)


def display_tasks_dir_summary(results: list[dict[str, Any]]) -> None:
    """Display a summary of tasks-dir execution results.

    Shows skipped, retried, new, completed, and failed entries with symbols.

    Args:
        results: List of result dicts with file_index, file_name, entry_index,
                 entry_description, thread_id, status, error, category.
    """
    print("\n" + "=" * 80, file=sys.stderr)
    print("TASKS-DIR EXECUTION SUMMARY", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(
        f"{'FILE':<30} {'ENTRY':<6} {'CAT':<8} {'STATUS':<12} {'THREAD_ID':<36} {'TASK':<20}",
        file=sys.stderr,
    )
    print("-" * 80, file=sys.stderr)

    for result in results:
        file_name = result.get("file_name", "")[:30]
        entry_idx = result.get("entry_index", -1)
        category = result.get("category", "new")
        status = result.get("status", "unknown")
        thread_id = result.get("thread_id", "")[:36]
        entry_desc = result.get("entry_description", "")[:20]

        symbol_map = {
            "skipped": "⊘",
            "retried": "↻",
            "new": "○",
            "completed": "✓",
        }
        status_symbol = symbol_map.get(category, "?") if status == "completed" else "✗"

        entry_display = str(entry_idx + 1) if entry_idx >= 0 else "-"
        print(
            f"{_sanitize_output(file_name):<30} {entry_display:<6} {category:<8} "
            f"{status_symbol} {status:<10} {_sanitize_output(thread_id):<36} "
            f"{_sanitize_output(entry_desc):<20}",
            file=sys.stderr,
        )

        if result.get("error"):
            error_preview = result["error"][:70]
            print(f"       Error: {_sanitize_output(error_preview)}", file=sys.stderr)

    print("-" * 80, file=sys.stderr)

    skipped = sum(1 for r in results if r.get("category") == "skipped")
    retried = sum(1 for r in results if r.get("category") == "retried")
    new_total = sum(1 for r in results if r.get("category") == "new")
    failed = sum(1 for r in results if r.get("status") == "failed")
    total = len(results)

    print(
        f"Total: {total} | Skipped: {skipped} | Retried: {retried} | New: {new_total} | "
        f"Failed: {failed}",
        file=sys.stderr,
    )
    print("=" * 80, file=sys.stderr)

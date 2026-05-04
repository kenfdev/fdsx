import json
import re as _re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from fdsx.core.config import TaskSplitterConfig
from fdsx.display.terminal import _sanitize_output
from fdsx.models.task import (
    TaskEntry,
    TaskFile,
    _ensure_no_symlink_ancestors,
    save_task_file,
)
from fdsx.providers.base import ProviderBase, get_provider

logger = structlog.get_logger(__name__)

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


def _invoke_splitter_and_parse(
    provider: "ProviderBase", prompt: str, model: str | None
) -> tuple[list[list[TaskEntry]], str]:
    """Invoke the task splitter and parse its JSON response.

    Args:
        provider: The provider instance to use for execution
        prompt: The prompt to send to the provider
        model: Optional model override

    Returns:
        Tuple of (parsed groups, raw stdout) for logging purposes

    Raises:
        RuntimeError: If the provider call fails
        ValueError: If the JSON parsing fails (raised with response_preview attached)
    """
    result = provider.execute(prompt=prompt, model=model)
    if result.exit_code != 0:
        raise RuntimeError(f"Task splitter failed: {result.stderr}")
    try:
        groups = _parse_structured_tasks(result.stdout)
        return groups, result.stdout
    except ValueError as e:
        e.response_preview = result.stdout[:500]  # type: ignore[attr-defined]
        raise


def _build_corrective_suffix(first_err: ValueError, response_preview: str) -> str:
    """Build a corrective prompt suffix for retry on parse failure."""
    return f"""---
CORRECTION REQUIRED
Your previous response could not be parsed as valid JSON.
Parse error: {first_err}
Previous response preview: {response_preview}
Please output a PROPOSED PARTITION: preamble followed by a valid JSON array
inside a ```json fenced block, with no other text.
"""


def split_tasks_to_groups(
    task_content: str,
    task_splitter: TaskSplitterConfig,
    state_names: list[str] | None = None,
    input_vars: set[str] | None = None,
    single_task: bool = False,
    progress: Callable[[str], None] | None = None,
) -> list[list[TaskEntry]]:
    """Invoke the task_splitter LLM to split task content into file groups.

    This is the standalone version used by the split CLI command that doesn't
    require a flow object.

    Args:
        task_content: The content of the task file
        task_splitter: The task splitter configuration
        state_names: Optional list of state names for context
        input_vars: Optional set of input variables for context
        single_task: If True, the LLM is directed to return exactly one group
            containing exactly one task. If the result exceeds this, it is coalesced
            into a single entry with a warning logged.
        progress: Optional callback for progress messages

    Returns:
        List of file groups. Each group contains sequentially-dependent
        TaskEntry objects that belong in the same file.

    Raises:
        RuntimeError: If the LLM call fails
        ValueError: If the response cannot be parsed
    """
    provider = get_provider(task_splitter.provider)

    prompt = _build_task_split_prompt(
        task_content,
        state_names,
        input_vars,
        extra_instructions=task_splitter.extra_instructions,
        single_task=single_task,
    )

    if progress:
        progress("Calling task splitter (this may take a while)...")

    try:
        groups, _raw_stdout = _invoke_splitter_and_parse(
            provider, prompt, task_splitter.model
        )
    except ValueError as first_err:
        response_preview = getattr(first_err, "response_preview", "")
        logger.warning(
            "task_splitter_invalid_json",
            error=str(first_err),
            response_preview=response_preview,
        )
        if progress:
            progress(
                "Splitter returned invalid JSON, retrying with corrective prompt..."
            )

        corrective_prompt = (
            prompt + "\n\n" + _build_corrective_suffix(first_err, response_preview)
        )
        try:
            groups, _raw_stdout = _invoke_splitter_and_parse(
                provider, corrective_prompt, task_splitter.model
            )
        except ValueError as second_err:
            raise ValueError(
                f"Attempt 1: {first_err} | Attempt 2: {second_err}"
            ) from second_err

    if progress:
        progress("Parsing splitter response...")

    if single_task:
        total_entries = sum(len(group) for group in groups)
        if len(groups) > 1 or total_entries > 1:
            logger.warning(
                "splitter_over_split",
                groups=len(groups),
                total_entries=total_entries,
            )
            all_descriptions = [
                entry.description for group in groups for entry in group
            ]
            coalesced_description = "\n\n".join(all_descriptions)
            return [[TaskEntry(description=coalesced_description)]]

    if progress:
        progress(f"Splitter produced {len(groups)} task group(s)")

    return groups


def _build_task_split_prompt(
    task_content: str,
    state_names: list[str] | None,
    input_vars: set[str] | None,
    extra_instructions: str | None = None,
    single_task: bool = False,
) -> str:
    """Build the splitter prompt.

    When ``single_task=False``, the prompt biases toward single-file output and
    instructs the LLM to emit a human-readable ``PROPOSED PARTITION:`` preamble
    before the JSON.  Assumes task content is a structured spec with a
    ``User Stories`` / ``Functional Requirements`` section; collapses to one file
    when that structure is absent.  Note: ``extra_instructions`` is an explicit
    override and may contradict the default rules; the user is responsible for
    consistency.
    """
    states_desc = ", ".join(state_names) if state_names else "any workflow"
    input_vars_desc = ", ".join(input_vars) if input_vars else "task"
    extra_section = (
        f"ADDITIONAL INSTRUCTIONS:\n{extra_instructions}\n\n"
        if extra_instructions
        else ""
    )

    single_task_directive = (
        "CRITICAL: You MUST return exactly ONE group containing exactly ONE task object.\n"
        "Combine all work into a single comprehensive task description with numbered sub-steps.\n"
        "Do NOT split into multiple groups or multiple tasks.\n\n"
        if single_task
        else ""
    )

    if not single_task:
        rules_block = """RULES (in priority order):
1. PR-sized: Each task file covers one PR-sized unit of work.
2. Default 1:1: one task per user story — do not merge unrelated stories into one task.
3. Sizing bias: if sizing is borderline, prefer one larger task over two smaller ones.
4. Single-file default: by default, emit exactly one file group containing all tasks.
5. Multi-file gate: emit multiple file groups only when ALL of the following hold:
   - tasks are non-overlapping (no shared files, types, or infrastructure)
   - free of cross-file dependencies (no file group depends on output of another)
   - free of shared cross-cutting concerns (auth, logging, config, DB schema)
6. Same-file rule: any cross-cutting concern MUST live in the same file group as its consumers.
7. No cross-file ordering: file groups are unordered and run in parallel; never assume sequencing.
8. No story splitting: a single user story is never split across file groups.
9. Spec-only judgement: independence is judged from the spec alone — from the input alone,
   never from an assumed codebase.
10. Auto-collapse: if any rule 5-9 cannot be satisfied, collapse to one file.

INPUT SHAPE:
This prompt expects the task content to contain a User Stories / Functional Requirements section.
Absent that structure, collapse to one file.

"""
        output_format = """OUTPUT FORMAT:
Output a PROPOSED PARTITION: preamble (plain text, no backtick literals):
  PROPOSED PARTITION:
  - file_count: <N>
  - per_file_summary: <one line per file group>
  - independence_rationale: <only when N > 1; why the groups are provably independent>
  - collapse_reason: <only when N == 1; why all stories collapsed to one file>

Then output the JSON inside a ```json ... ``` fenced block.

IMPORTANT: Output ONLY the PROPOSED PARTITION preamble then the JSON fence.
No other text or commentary."""
        examples_block = """EXAMPLES:

Example A — interdependent user stories → single file (collapse_reason populated):
PROPOSED PARTITION:
- file_count: 1
- per_file_summary: All user stories collapsed into one task file
- collapse_reason: Stories share the same data model and UI layer; splitting would
  create a cross-file dependency on the schema migration in story 1.

```json
[
  [
    {{"description": "Implement user authentication\\n1. Add User model\\n2. Add login endpoint\\n3. Add session handling"}},
    {{"description": "Implement user profile page\\n1. Read user from session\\n2. Render profile template"}}
  ]
]
```

Example B — independent feature areas → multi-file (independence_rationale populated):
PROPOSED PARTITION:
- file_count: 2
- per_file_summary:
  - File 1: Billing dashboard (read-only analytics, no shared models)
  - File 2: Email notification service (background worker, separate DB table)
- independence_rationale: Billing and notifications touch separate DB tables, separate
  UI surfaces, and have no shared business logic. Neither depends on the other's output.

```json
[
  [
    {{"description": "Build billing dashboard\\n1. Add BillingRecord model\\n2. Render monthly chart"}}
  ],
  [
    {{"description": "Build email notification service\\n1. Add Notification model\\n2. Add worker task"}}
  ]
]
```

"""
    else:
        rules_block = ""
        examples_block = ""
        output_format = """OUTPUT FORMAT:
Return a JSON array of file groups. Each group is an array of task objects that belong in the same file.
```json
[
  [
    {{"description": "Independent feature A\\n1. Step one\\n2. Step two"}}
  ],
  [
    {{"description": "Step 1 of related work"}},
    {{"description": "Step 2 that depends on step 1"}}
  ]
]
```

IMPORTANT: Output ONLY the JSON array, no additional text, explanations, or markdown formatting."""

    prompt = f"""You are a task partitioner that converts a finalized feature spec into
PR-sized task files for parallel execution. You judge independence
from the spec alone — never from any assumed codebase.

{single_task_directive}The workflow has these states: {states_desc}
The workflow accepts these input variables: {input_vars_desc}

TASK CONTENT:
{task_content}

{rules_block}JSON HYGIENE:
- Escape special characters: use \\" for quotes, \\\\ for backslashes, \\n for newlines
- Keep code snippets short and single-line; avoid multi-line blocks that can break JSON
- Prefer concise prose + step bullets over reproducing code verbatim
- Always output valid JSON (arrays and objects only, no trailing commas)

{extra_section}{examples_block}{output_format}"""

    return prompt


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


def write_task_files(
    groups: list[list[TaskEntry]], tasks_dir: Path, *, source: str | None = None
) -> list[Path]:
    """Write task groups to numbered YAML files in the tasks directory.

    Creates files in the format: tasks_dir/NNN-<slug>.yaml where NNN continues
    from the highest existing index found in both tasks_dir and
    tasks_dir/completed/.  Each file contains sequentially-dependent tasks from
    one file group.

    Args:
        groups: List of file groups, each containing TaskEntry objects
        tasks_dir: Directory to write task files to
        source: Optional source/origin path to record in each task file

    Returns:
        List of created file paths

    Raises:
        ValueError: If tasks_dir is a symlink or other security checks fail
    """
    # Reject user-controlled symlink ancestors before creating the tasks dir,
    # while allowing known platform temp aliases like /var and /tmp on macOS.
    _ensure_no_symlink_ancestors(tasks_dir, include_self=True)

    tasks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    base_index = _scan_max_task_index(tasks_dir)

    created_files: list[Path] = []
    for i, group in enumerate(groups):
        if not group:
            continue

        task_file = TaskFile(entries=group, source=source)
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

    # Reject user-controlled symlink ancestors in the destination path while
    # allowing known platform temp aliases like /var and /tmp on macOS.
    _ensure_no_symlink_ancestors(completed_dir, include_self=True)

    completed_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    dest = completed_dir / file_path.name
    if dest.exists():
        raise FileExistsError(f"Destination already exists in completed/: {dest}")

    shutil.move(str(file_path), str(dest))


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

import sys
from typing import Any

from fdsx.core.config import TaskSplitterConfig
from fdsx.display.terminal import _sanitize_output
from fdsx.models.flow import Flow
from fdsx.providers.base import get_provider


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

    tasks = _parse_task_list(result.stdout)

    return tasks


def _build_task_split_prompt(
    task_content: str, state_names: list[str], input_vars: set[str]
) -> str:
    """Build the prompt for the task splitter LLM."""
    states_desc = ", ".join(state_names)
    input_vars_desc = ", ".join(input_vars) if input_vars else "none"

    prompt = f"""You are a task splitter. Given a batch of work, split it into individual executable tasks.

The workflow has these states: {states_desc}
The workflow accepts these input variables: {input_vars_desc}

TASK CONTENT:
{task_content}

INSTRUCTIONS:
1. Analyze the task content above
2. Split it into individual, self-contained task descriptions
3. Each task should be executable by the workflow and should set a 'task' variable
4. Output ONLY a numbered list (1., 2., 3., etc.) with one task per line
5. Keep tasks concise but clear
6. Do NOT include any additional text, explanations, or formatting

OUTPUT:"""

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

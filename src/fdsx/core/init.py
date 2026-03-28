import importlib.resources
from pathlib import Path

CONFIG_TEMPLATE = """\
# workflows_dir: .fdsx/workflows  # Directory containing workflow definitions
# auto_workflow: false  # Automatically select workflow when only one exists
# profiles:
#   smarty:  # Profile for planning and analysis tasks
#     provider: claude
#     model: claude-sonnet-4-7
#   specialist:  # Profile for implementation tasks
#     provider: claude
#     model: claude-sonnet-4-7
#   doer:  # Profile for quick execution tasks
#     provider: opencode
#     model: opencode
# providers:  # Provider binary overrides (null uses defaults)
#   claude: null
#   codex: null
#   opencode: null
# task_splitter: null  # Task splitting configuration
# workflow_selector:  # Workflow auto-selection settings
#   provider: claude
#   model: claude-sonnet-4-7
# hooks: null  # Lifecycle hooks configuration
"""


def needs_init(cwd: Path) -> bool:
    return not (cwd / ".fdsx").is_dir()


def scaffold(cwd: Path) -> list[str]:
    cwd = Path(cwd)
    fdsx_dir = cwd / ".fdsx"
    workflows_dir = fdsx_dir / "workflows"

    workflows_dir.mkdir(parents=True, exist_ok=True)

    config_path = fdsx_dir / "config.yaml"
    config_path.write_text(CONFIG_TEMPLATE)

    examples_pkg = importlib.resources.files("fdsx.examples.workflows")
    created_paths: list[str] = []

    for resource in examples_pkg.iterdir():
        if resource.name in ("__init__.py", "__pycache__"):
            continue
        if not resource.is_dir():
            continue

        workflow_name = resource.name
        dest_workflow_dir = workflows_dir / workflow_name
        dest_workflow_dir.mkdir(parents=True, exist_ok=True)

        for file_resource in resource.iterdir():
            if file_resource.name == "__init__.py":
                continue
            if not file_resource.is_file():
                continue
            content = file_resource.read_text()
            dest_file = dest_workflow_dir / file_resource.name
            dest_file.write_text(content)
            created_paths.append(str(dest_file.relative_to(cwd)))

    created_paths.append(str(config_path.relative_to(cwd)))
    return sorted(created_paths)

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

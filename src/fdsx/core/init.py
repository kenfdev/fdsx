import importlib.resources
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fdsx.models.init import InitConfig, ProviderSelection, ScaffoldResult, TemplateInfo

SKILL_PACKAGE = "fdsx.data.skills.fdsx"

GITIGNORE_TEMPLATE = """\
# fdsx runtime directories
runs/
tasks/
checkpoints/
locks/
"""

CONFIG_TEMPLATE = """\
# --- Commented Reference ---
# profiles:
#   # Deep reasoning and analysis
#   smarty:
#     provider: claude
#     model: claude-opus-4-6
#   # Fast execution
#   doer:
#     provider: claude
#     model: claude-sonnet-4-6
#   # Domain-focused tasks
#   specialist:
#     provider: claude
#     model: claude-sonnet-4-6
#   # Broad capability tasks
#   generalist:
#     provider: claude
#     model: claude-sonnet-4-6
#   # Heavy/large-scale tasks
#   behemoth:
#     provider: claude
#     model: claude-opus-4-6
# workflow_selector:
#   profile: generalist
#
# default_tasks_dir: .fdsx/tasks/
#
# extra_instructions:
#   workflow_selector:
#     - "Place shared utilities in the shared/ directory"
"""


def needs_init(cwd: Path) -> bool:
    return not (cwd / ".fdsx").is_dir()


def ensure_gitignore(cwd: Path) -> None:
    """Create .fdsx/.gitignore if it doesn't exist."""
    gitignore_path = Path(cwd) / ".fdsx" / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(GITIGNORE_TEMPLATE)


def check_conflicts(cwd: Path, templates: list[TemplateInfo]) -> list[str]:
    """Return workflow names that already exist in .fdsx/workflows/."""
    cwd = Path(cwd)
    workflows_dir = cwd / ".fdsx" / "workflows"
    if not workflows_dir.is_dir():
        return []
    existing_workflows = {d.name for d in workflows_dir.iterdir() if d.is_dir()}
    return [t.name for t in templates if t.name in existing_workflows]


def scaffold(
    cwd: Path,
    config: InitConfig,
    allow_overwrite: set[str] | None = None,
) -> ScaffoldResult:
    """Scaffold .fdsx/ directory with selected templates and config.

    Args:
        cwd: Working directory to scaffold into.
        config: InitConfig containing selected providers and templates.
        allow_overwrite: Set of workflow names to overwrite if they already exist.

    Returns:
        ScaffoldResult with created files and skipped items info.
    """
    cwd = Path(cwd)
    fdsx_dir = cwd / ".fdsx"
    allow_overwrite = allow_overwrite or set()
    created: list[str] = []
    skipped_workflows: list[str] = []

    existing_fdsx = fdsx_dir.is_dir()
    if existing_fdsx:
        return _scaffold_existing(cwd, config, allow_overwrite)
    else:
        return _scaffold_fresh(cwd, config, allow_overwrite, created, skipped_workflows)


def _scaffold_fresh(
    cwd: Path,
    config: InitConfig,
    allow_overwrite: set[str],
    created: list[str],
    skipped_workflows: list[str],
) -> ScaffoldResult:
    """Scaffold fresh .fdsx/ directory using atomic temp dir + rename."""
    fdsx_dir = cwd / ".fdsx"
    tmp_dir: Path | None = None
    try:
        tmp_dir = Path(tempfile.mkdtemp(dir=str(cwd), prefix=".fdsx.tmp."))
        tmp_workflows_dir = tmp_dir / "workflows"
        tmp_workflows_dir.mkdir(parents=True)

        config_yaml = generate_config_yaml(config.profile_assignments, config.providers)
        config_path = tmp_dir / "config.yaml"
        config_path.write_text(config_yaml)

        for template in config.templates:
            workflow_dir = tmp_workflows_dir / template.name
            workflow_dir.mkdir(parents=True, exist_ok=True)
            for file_resource in template.path.iterdir():
                if file_resource.name == "__init__.py":
                    continue
                if not file_resource.is_file():
                    continue
                content = file_resource.read_text()
                dest_file = workflow_dir / file_resource.name
                dest_file.write_text(content)
                created.append(f".fdsx/workflows/{template.name}/{file_resource.name}")

        gitignore_path = tmp_dir / ".gitignore"
        gitignore_path.write_text(GITIGNORE_TEMPLATE)

        Path.rename(tmp_dir, fdsx_dir)
        tmp_dir = None

        created.append(".fdsx/config.yaml")
        return ScaffoldResult(
            created=sorted(created),
            skipped_config=False,
            skipped_workflows=skipped_workflows,
        )
    finally:
        if tmp_dir is not None and Path(tmp_dir).exists():
            shutil.rmtree(tmp_dir)


def _scaffold_existing(
    cwd: Path,
    config: InitConfig,
    allow_overwrite: set[str],
) -> ScaffoldResult:
    """Scaffold into existing .fdsx/ directory (protected, non-atomic)."""
    fdsx_dir = cwd / ".fdsx"
    workflows_dir = fdsx_dir / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    skipped_config = False
    skipped_workflows: list[str] = []

    config_path = fdsx_dir / "config.yaml"
    if config_path.exists():
        skipped_config = True
    else:
        config_yaml = generate_config_yaml(config.profile_assignments, config.providers)
        config_path.write_text(config_yaml)
        created.append(str(config_path.relative_to(cwd)))

    gitignore_path = fdsx_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(GITIGNORE_TEMPLATE)

    for template in config.templates:
        dest_workflow_dir = workflows_dir / template.name
        if dest_workflow_dir.exists():
            if template.name in allow_overwrite:
                shutil.rmtree(dest_workflow_dir)
                _copy_template(template, dest_workflow_dir, cwd, created)
            else:
                skipped_workflows.append(template.name)
        else:
            _copy_template(template, dest_workflow_dir, cwd, created)

    return ScaffoldResult(
        created=sorted(created),
        skipped_config=skipped_config,
        skipped_workflows=skipped_workflows,
    )


def _copy_template(
    template: TemplateInfo,
    dest_workflow_dir: Path,
    cwd: Path,
    created: list[str],
) -> None:
    """Copy template files to destination workflow directory."""
    dest_workflow_dir.mkdir(parents=True, exist_ok=True)
    for file_resource in template.path.iterdir():
        if file_resource.name == "__init__.py":
            continue
        if not file_resource.is_file():
            continue
        content = file_resource.read_text()
        dest_file = dest_workflow_dir / file_resource.name
        dest_file.write_text(content)
        created.append(str(dest_file.relative_to(cwd)))


def _resolve_xdg_templates_dir() -> Path:
    """Resolve the XDG config templates directory for user templates.

    Returns the path even if it does not exist, since discover_templates
    handles non-existence gracefully.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_dir = Path(xdg) if xdg else Path.home() / ".config"
    return config_dir / "fdsx" / "templates" / "workflows"


def discover_templates() -> list[TemplateInfo]:
    """Discover available workflow templates.

    Returns builtin templates first (from the fdsx.examples.workflows package),
    followed by user templates from the XDG config directory.
    """
    templates: list[TemplateInfo] = []

    examples_pkg = importlib.resources.files("fdsx.examples.workflows")
    builtin_dirs: list[tuple[str, Any]] = []
    for resource in examples_pkg.iterdir():
        if resource.name in ("__init__.py", "__pycache__"):
            continue
        if not resource.is_dir():
            continue
        if (resource / "workflow.yaml").is_file():
            builtin_dirs.append((resource.name, resource))

    for name, resource in builtin_dirs:
        with importlib.resources.as_file(resource) as template_dir:
            templates.append(
                TemplateInfo(
                    name=name,
                    path=template_dir,
                    source="builtin",
                )
            )

    user_templates_dir = _resolve_xdg_templates_dir()
    if user_templates_dir.exists():
        for subdir in user_templates_dir.iterdir():
            if not subdir.is_dir():
                continue
            if (subdir / "workflow.yaml").exists():
                templates.append(
                    TemplateInfo(
                        name=subdir.name,
                        path=subdir,
                        source="user",
                    )
                )

    return templates


@contextmanager
def get_bundled_skill_path() -> Iterator[Path]:
    """Yield path to bundled skill files using importlib.resources.

    This is a context manager that yields a real filesystem path.
    For directory resources, as_file() cannot be used directly, so we
    resolve each child individually when needed.

    Raises:
        FileNotFoundError: If the bundled skill path does not exist.

    Yields:
        Path to the bundled skill directory.
    """
    pkg = importlib.resources.files(SKILL_PACKAGE)
    # as_file() only works on single-file resources, not directories.
    # Resolve the directory path via a known sentinel file.
    sentinel = pkg.joinpath("SKILL.md")
    with importlib.resources.as_file(sentinel) as sentinel_path:
        path = sentinel_path.parent
        if not path.exists():
            raise FileNotFoundError(f"Bundled skill path not found: {path}")
        yield path


def install_skill(target_dir: Path, overwrite: bool = False) -> list[str]:
    """Copy bundled skill files to target_dir/fdsx/.

    Args:
        target_dir: Parent directory for skill installation (skill goes into target_dir/fdsx/).
        overwrite: If True, remove existing target_dir/fdsx/ before copying.

    Returns:
        List of relative paths of created files.

    Raises:
        FileExistsError: If target_dir/fdsx/ exists and overwrite is False.
        ValueError: If the resolved destination is outside target_dir.
    """
    target_dir = Path(target_dir).resolve()
    skill_dest = target_dir / "fdsx"
    resolved_dest = skill_dest.resolve()

    if not resolved_dest.is_relative_to(target_dir):
        raise ValueError(
            f"Destination path {resolved_dest} is outside the target directory {target_dir}"
        )

    if skill_dest.exists():
        if not overwrite:
            raise FileExistsError(f"Skill already exists at {skill_dest}")
        shutil.rmtree(skill_dest)

    skill_dest.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    with get_bundled_skill_path() as skill_src_path:
        for item in skill_src_path.rglob("*.md"):
            rel_path = item.relative_to(skill_src_path)
            dest_file = skill_dest / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_file)
            created.append(str(dest_file.relative_to(target_dir)))

    return created


_MAX_PERMISSION_OPTIONS: dict[str, dict[str, Any]] = {
    "claude": {"dangerously_skip_permissions": True},
    "codex": {"dangerously_bypass_approvals_and_sandbox": True},
    "gemini": {"yolo": True},
    "opencode": {"permission": "auto-edit"},
    "grok": {"permission_mode": "bypassPermissions"},
}

_PROFILE_ROLE_COMMENTS: dict[str, str] = {
    "smarty": "# Deep reasoning and analysis",
    "doer": "# Fast execution",
    "specialist": "# Domain-focused tasks",
    "generalist": "# Broad capability tasks",
    "behemoth": "# Heavy/large-scale tasks",
}

_PROFILE_ORDER = ["smarty", "doer", "specialist", "generalist", "behemoth"]


def generate_config_yaml(
    profile_assignments: dict[str, ProviderSelection],
    providers: list[ProviderSelection],
) -> str:
    """Generate a config.yaml string with profiles and max-permission provider options.

    Args:
        profile_assignments: Mapping of profile names to provider selections.
        providers: List of provider selections (provider + model).

    Returns:
        YAML string suitable for writing to .fdsx/config.yaml.
    """
    lines: list[str] = []

    lines.append("profiles:")
    for profile_name in _PROFILE_ORDER:
        if profile_name in profile_assignments:
            selection = profile_assignments[profile_name]
            comment = _PROFILE_ROLE_COMMENTS.get(profile_name, "")
            if comment:
                lines.append(f"  {comment}")
            lines.append(f"  {profile_name}:")
            lines.append(f"    provider: {selection.provider}")
            lines.append(f"    model: {selection.model}")

    provider_configs: dict[str, dict[str, Any]] = {}
    for selection in providers:
        if selection.provider in _MAX_PERMISSION_OPTIONS:
            provider_configs[selection.provider] = _MAX_PERMISSION_OPTIONS[
                selection.provider
            ]

    if provider_configs:
        lines.append("providers:")
        for provider_name, provider_config in provider_configs.items():
            lines.append(f"  {provider_name}:")
            for key, value in provider_config.items():
                lines.append(f"    {key}: {value}")

    lines.append("workflow_selector:")
    lines.append("  profile: generalist")

    lines.append("")
    lines.append(CONFIG_TEMPLATE)

    return "\n".join(lines)

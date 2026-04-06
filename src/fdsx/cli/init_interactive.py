"""Interactive UI functions for fdsx init using Rich/Typer patterns."""

from pathlib import Path

from rich.console import Console
from rich.table import Table

from fdsx.models.init import ProviderSelection, TemplateInfo
from fdsx.models.validators import VALID_PROVIDERS

MODEL_PRESETS: dict[str, list[str]] = {
    "claude": ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5"],
    "codex": ["o4-mini", "o3", "codex-mini"],
    "gemini": ["gemini-2.5-pro", "gemini-2.5-flash"],
}

PROVIDER_DOCS: dict[str, str] = {
    "claude": "https://docs.anthropic.com/en/docs/about-claude/models",
    "codex": "https://platform.openai.com/docs/models",
    "gemini": "https://ai.google.dev/gemini-api/docs/models",
    "opencode": "https://opencode.ai/docs",
}

PROFILE_ROLES: dict[str, str] = {
    "smarty": "Deep reasoning and analysis",
    "doer": "Fast execution",
    "specialist": "Domain-focused tasks",
    "generalist": "Broad capability tasks",
    "behemoth": "Heavy/large-scale tasks",
}

_PROFILE_ORDER: list[str] = [
    "smarty",
    "doer",
    "specialist",
    "generalist",
    "behemoth",
]

_console = Console(stderr=True)


def _input(prompt: str = "") -> str:
    """Thin wrapper around input() for test mocking.

    Args:
        prompt: Optional prompt string (if empty, prompt should already be displayed).
    """
    return input(prompt)


def select_providers() -> list[str]:
    """Display provider selection table and return chosen providers."""
    table = Table(title="Select Providers", show_header=True, header_style="bold")
    table.add_column("#", style="dim", width=4)
    table.add_column("Provider")

    providers = sorted(VALID_PROVIDERS)
    for i, provider in enumerate(providers, start=1):
        table.add_row(str(i), provider)

    _console.print(table)
    _console.print("Enter numbers separated by commas (e.g. 1,3): ", end="")

    while True:
        user_input = _input("").strip()
        if not user_input:
            _console.print("Please enter at least one provider.", style="red")
            _console.print("Enter numbers separated by commas (e.g. 1,3): ", end="")
            continue
        try:
            indices = [int(x.strip()) for x in user_input.split(",")]
        except ValueError:
            _console.print("Invalid input. Enter numbers only.", style="red")
            _console.print("Enter numbers separated by commas (e.g. 1,3): ", end="")
            continue
        if not all(1 <= idx <= len(providers) for idx in indices):
            _console.print(
                f"Invalid selection. Enter numbers between 1 and {len(providers)}.",
                style="red",
            )
            _console.print("Enter numbers separated by commas (e.g. 1,3): ", end="")
            continue
        if len(set(indices)) != len(indices):
            _console.print("Duplicate selections are not allowed.", style="red")
            _console.print("Enter numbers separated by commas (e.g. 1,3): ", end="")
            continue
        selected = [providers[idx - 1] for idx in indices]
        for provider in selected:
            docs_url = PROVIDER_DOCS.get(provider)
            if docs_url:
                _console.print(f"  {provider}: {docs_url}")
        return selected


def select_models(providers: list[str]) -> list[ProviderSelection]:
    """Display model selection for each provider and return selections."""
    results: list[ProviderSelection] = []

    for provider in providers:
        presets = MODEL_PRESETS.get(provider, [])

        if not presets:
            _console.print(f"\n[bold]Provider:[/bold] {provider}")
            _console.print("Enter model name: ", end="")
            while True:
                model = _input("").strip()
                if not model:
                    _console.print("Model name cannot be empty.", style="red")
                    _console.print("Enter model name: ", end="")
                    continue
                results.append(ProviderSelection(provider=provider, model=model))
                break
            continue

        table = Table(
            title=f"Select Model for {provider}",
            show_header=True,
            header_style="bold",
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Model")

        for i, model in enumerate(presets, start=1):
            table.add_row(str(i), model)

        _console.print(f"\n[bold]Provider:[/bold] {provider}")
        _console.print(table)
        _console.print("Enter number (or type a custom model name): ", end="")

        while True:
            user_input = _input("").strip()
            if not user_input:
                _console.print("Please enter a selection.", style="red")
                _console.print("Enter number (or type a custom model name): ", end="")
                continue
            try:
                idx = int(user_input)
                if 1 <= idx <= len(presets):
                    results.append(
                        ProviderSelection(provider=provider, model=presets[idx - 1])
                    )
                    break
                _console.print(
                    f"Invalid number. Enter between 1 and {len(presets)}.",
                    style="red",
                )
                _console.print("Enter number (or type a custom model name): ", end="")
            except ValueError:
                results.append(ProviderSelection(provider=provider, model=user_input))
                break
    return results


def assign_profiles(
    selections: list[ProviderSelection],
) -> dict[str, ProviderSelection]:
    """Assign provider+model selections to named profiles.

    If a single provider is selected, auto-fill all profiles with that selection
    (no prompts). If multiple providers are selected, prompt for each profile.

    Args:
        selections: List of ProviderSelection from select_models().

    Returns:
        Dict mapping profile name -> ProviderSelection.
    """
    if len(selections) == 1:
        single = selections[0]
        return {profile: single for profile in _PROFILE_ORDER}

    result: dict[str, ProviderSelection] = {}
    for profile in _PROFILE_ORDER:
        role_desc = PROFILE_ROLES.get(profile, "")
        _console.print(f"\n[bold]{profile}[/bold]: {role_desc}")

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", style="dim", width=4)
        table.add_column("Provider")
        table.add_column("Model")

        for i, sel in enumerate(selections, start=1):
            table.add_row(str(i), sel.provider, sel.model)

        _console.print(table)
        _console.print("Enter number: ", end="")

        while True:
            user_input = _input("").strip()
            if not user_input:
                _console.print("Please enter a selection.", style="red")
                _console.print("Enter number: ", end="")
                continue
            try:
                idx = int(user_input)
                if 1 <= idx <= len(selections):
                    result[profile] = selections[idx - 1]
                    break
                _console.print(
                    f"Invalid number. Enter between 1 and {len(selections)}.",
                    style="red",
                )
                _console.print("Enter number: ", end="")
            except ValueError:
                _console.print("Invalid input. Enter a number.", style="red")
                _console.print("Enter number: ", end="")

    return result


def select_templates(available: list[TemplateInfo]) -> list[TemplateInfo]:
    """Display template selection table and return chosen templates."""
    if not available:
        return []

    table = Table(
        title="Select Templates",
        show_header=True,
        header_style="bold",
    )
    table.add_column("#", style="dim", width=4)
    table.add_column("Template")
    table.add_column("Source", style="dim")

    for i, template in enumerate(available, start=1):
        table.add_row(str(i), template.name, template.source)

    _console.print(table)
    _console.print(
        "Enter numbers separated by commas (or press Enter for none): ", end=""
    )

    while True:
        user_input = _input("").strip()
        if not user_input:
            return []
        try:
            indices = [int(x.strip()) for x in user_input.split(",")]
        except ValueError:
            _console.print("Invalid input. Enter numbers only.", style="red")
            _console.print(
                "Enter numbers separated by commas (or press Enter for none): ",
                end="",
            )
            continue
        if not all(1 <= idx <= len(available) for idx in indices):
            _console.print(
                f"Invalid selection. Enter numbers between 1 and {len(available)}.",
                style="red",
            )
            _console.print(
                "Enter numbers separated by commas (or press Enter for none): ",
                end="",
            )
            continue
        if len(set(indices)) != len(indices):
            _console.print("Duplicate selections are not allowed.", style="red")
            _console.print(
                "Enter numbers separated by commas (or press Enter for none): ",
                end="",
            )
            continue
        selected = [available[idx - 1] for idx in indices]
        return selected


def confirm_existing_project() -> bool:
    """Ask user whether to proceed with an existing project directory."""
    _console.print("Project directory already exists. Continue? (y/n): ", end="")
    while True:
        user_input = _input("").strip().lower()
        if user_input in ("y", "yes"):
            return True
        if user_input in ("n", "no"):
            return False
        _console.print("Please enter 'y' or 'n'.", style="red")
        _console.print("Continue? (y/n): ", end="")


def confirm_overwrite(workflow_name: str) -> bool:
    """Ask user whether to overwrite an existing workflow file."""
    _console.print(
        f"Workflow '{workflow_name}' already exists. Overwrite? (y/n): ", end=""
    )
    while True:
        user_input = _input("").strip().lower()
        if user_input in ("y", "yes"):
            return True
        if user_input in ("n", "no"):
            return False
        _console.print("Please enter 'y' or 'n'.", style="red")
        _console.print(f"Overwrite '{workflow_name}'? (y/n): ", end="")


def prompt_skill_install() -> Path | None:
    """Ask user whether to install skill; if yes, ask for location.

    Returns:
        Chosen target Path for skill installation, or None if declined.
    """
    _console.print("Install the /fdsx Claude Code skill? (Y/n): ", end="")
    while True:
        user_input = _input("").strip().lower()
        if user_input in ("n", "no"):
            return None
        if user_input in ("y", "yes", ""):
            break
        _console.print("Please enter Y or n: ", end="")

    _console.print("\nSelect skill installation location:")
    _console.print("  1. ~/.agents/skills (default)")
    _console.print("  2. .agents/skills (project-local)")
    _console.print("  3. Custom path")
    _console.print("Enter number (or press Enter for default): ", end="")

    while True:
        user_input = _input("").strip()
        if not user_input or user_input == "1":
            return Path("~/.agents/skills").expanduser()
        if user_input == "2":
            return Path(".agents/skills")
        if user_input == "3":
            _console.print("Enter custom path: ", end="")
            custom = _input("").strip()
            if custom:
                return Path(custom)
        _console.print("Invalid selection. Enter 1, 2, or 3: ", end="")


def confirm_skill_overwrite(path: Path) -> bool:
    """Ask user to confirm overwriting existing skill files."""
    _console.print(f"Skill already exists at {path}/fdsx/. Overwrite? (y/n): ", end="")
    while True:
        user_input = _input("").strip().lower()
        if user_input in ("y", "yes"):
            return True
        if user_input in ("n", "no"):
            return False
        _console.print("Please enter 'y' or 'n'.", style="red")
        _console.print(f"Overwrite {path}/fdsx/? (y/n): ", end="")

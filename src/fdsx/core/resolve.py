"""Render workflow YAML for inspection."""

import os
from pathlib import Path
from typing import Any

import yaml

_COMMENT_PREFIX = "__fdsx_resolve_comment_"


class _ResolveDumper(yaml.SafeDumper):
    """YAML dumper that keeps expanded prompt contents readable."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _represent_string(
    dumper: yaml.SafeDumper,
    value: str,
) -> yaml.nodes.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_ResolveDumper.add_representer(str, _represent_string)


def _expand_prompt_file(
    item: dict[str, Any],
    *,
    workflow_dir: Path,
    comments: dict[str, str],
) -> dict[str, Any]:
    expanded: dict[str, Any] = {}
    for key, value in item.items():
        if key != "prompt_file":
            expanded[key] = value
            continue

        marker = f"{_COMMENT_PREFIX}{len(comments)}__"
        comments[marker] = f"from prompt_file: {value}"
        expanded[marker] = None
        expanded["prompt_template"] = (workflow_dir / value).read_text(encoding="utf-8")
    return expanded


def _expand_state_prompt_files(
    state: dict[str, Any],
    *,
    workflow_dir: Path,
    comments: dict[str, str],
) -> dict[str, Any]:
    if state.get("type") == "task":
        return _expand_prompt_file(
            state,
            workflow_dir=workflow_dir,
            comments=comments,
        )
    if state.get("type") == "parallel":
        expanded = dict(state)
        expanded["branches"] = [
            _expand_prompt_file(
                branch,
                workflow_dir=workflow_dir,
                comments=comments,
            )
            for branch in state.get("branches", [])
        ]
        return expanded
    if state.get("type") == "map":
        expanded = dict(state)
        iterator = dict(state.get("iterator", {}))
        iterator["states"] = [
            _expand_prompt_file(
                iterator_state,
                workflow_dir=workflow_dir,
                comments=comments,
            )
            for iterator_state in iterator.get("states", [])
        ]
        expanded["iterator"] = iterator
        return expanded
    return state


def _render_comments(data: dict[str, Any], comments: dict[str, str]) -> str:
    rendered = yaml.dump(
        data,
        Dumper=_ResolveDumper,
        sort_keys=False,
        allow_unicode=True,
    )
    for marker, comment in comments.items():
        rendered = rendered.replace(f"{marker}: null", f"# {comment}")
    return rendered


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _external_profile_sources(
    project_dir: Path,
) -> dict[str, tuple[dict[str, Any], str]]:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    xdg_dir = Path(xdg) if xdg else Path.home() / ".config"
    config_sources = (
        ("global", xdg_dir / "fdsx" / "config.yaml"),
        ("project", project_dir / ".fdsx" / "config.yaml"),
    )
    profiles: dict[str, tuple[dict[str, Any], str]] = {}
    for scope, config_path in config_sources:
        config_profiles = _read_yaml_mapping(config_path).get("profiles", {})
        if not isinstance(config_profiles, dict):
            continue
        for name, profile in config_profiles.items():
            if isinstance(profile, dict):
                profiles[name] = (
                    profile,
                    f"from {scope} config: {config_path.resolve()}",
                )
    return profiles


def _referenced_profiles(data: dict[str, Any]) -> list[str]:
    referenced: list[str] = []

    def add_from(item: Any) -> None:
        if not isinstance(item, dict):
            return
        profile = item.get("profile")
        if isinstance(profile, str) and profile not in referenced:
            referenced.append(profile)

    def add_fallback_from(item: Any) -> None:
        if not isinstance(item, dict):
            return
        extract = item.get("extract")
        if isinstance(extract, dict):
            add_from(extract.get("fallback"))

    add_from(data.get("extraction_fallback"))
    for state in data.get("states", {}).values():
        if not isinstance(state, dict):
            continue
        state_type = state.get("type")
        if state_type == "task":
            add_from(state)
            add_fallback_from(state)
        elif state_type == "parallel":
            for branch in state.get("branches", []):
                add_from(branch)
                add_fallback_from(branch)
        elif state_type == "map":
            iterator = state.get("iterator", {})
            if isinstance(iterator, dict):
                for iterator_state in iterator.get("states", []):
                    add_from(iterator_state)
                    add_fallback_from(iterator_state)
    return referenced


def _add_external_profiles(
    data: dict[str, Any],
    *,
    project_dir: Path,
    comments: dict[str, str],
) -> None:
    workflow_profiles = data.get("profiles")
    profiles = dict(workflow_profiles) if isinstance(workflow_profiles, dict) else {}
    external_profiles = _external_profile_sources(project_dir)
    for name in _referenced_profiles(data):
        if name in profiles or name not in external_profiles:
            continue
        profile, source = external_profiles[name]
        marker = f"{_COMMENT_PREFIX}{len(comments)}__"
        comments[marker] = source
        profiles[marker] = None
        profiles[name] = profile
    if profiles:
        if "profiles" in data:
            data["profiles"] = profiles
            return
        reordered: dict[str, Any] = {}
        for key, value in data.items():
            if key == "states":
                reordered["profiles"] = profiles
            reordered[key] = value
        data.clear()
        data.update(reordered)


def resolve_workflow_yaml(path: Path, *, project_dir: Path | None = None) -> str:
    """Return a workflow as normalized YAML with static file references expanded."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    comments: dict[str, str] = {}
    _add_external_profiles(
        data,
        project_dir=project_dir or Path.cwd(),
        comments=comments,
    )
    states = data.get("states", {})
    data["states"] = {
        name: _expand_state_prompt_files(
            state,
            workflow_dir=path.parent,
            comments=comments,
        )
        for name, state in states.items()
    }
    return _render_comments(data, comments)

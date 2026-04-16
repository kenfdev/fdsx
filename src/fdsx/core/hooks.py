"""Hook executor, data writer, and collector for fdsx lifecycle hooks (FR-5).

Provides three public functions:
- execute_hooks()  — run a flat list of HookEntry commands (T019)
- write_hook_data() — write per-state JSON data to .fdsx/runs/.../hooks/ (T020)
- collect_hooks()   — merge hooks from global → project → flow → state (T021)
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Literal

from fdsx.logging.recorder import FDSX_DIR_NAME, RUNS_DIR_NAME
from fdsx.models.flow import HookConfig, HookEntry

logger = logging.getLogger(__name__)

# --- Directory / file name constants ----------------------------------------

# FDSX_DIR_NAME and RUNS_DIR_NAME are imported from fdsx.logging.recorder
HOOKS_DIR_NAME = "hooks"
INPUT_FILENAME = "input.json"
OUTPUT_FILENAME = "output.json"

# --- Environment variable name constants -------------------------------------

ENV_STATE_NAME = "FDSX_STATE_NAME"
ENV_STATUS = "FDSX_STATUS"
ENV_DATA_PATH = "FDSX_DATA_PATH"
ENV_THREAD_ID = "FDSX_THREAD_ID"
ENV_FLOW_NAME = "FDSX_FLOW_NAME"
ENV_HOOKS = "FDSX_HOOKS"


class HookAbortError(Exception):
    """Raised when a hook with on_failure='abort' exits with a non-zero code."""

    def __init__(self, command: str, return_code: int) -> None:
        self.command = command
        self.return_code = return_code
        super().__init__(
            f"Hook command exited with code {return_code} (abort policy): {command!r}"
        )


def execute_hooks(
    hooks: list[HookEntry],
    *,
    state_name: str,
    status: str,
    data_path: Path,
    thread_id: str,
    flow_name: str,
    event: Literal["on_state_start", "on_state_end"],
) -> None:
    """Execute a flat list of hook commands in order.

    Each command is run with ``shell=True`` and receives:
    - Positional args: $1=state_name, $2=status, $3=data_path
    - Environment variables: FDSX_STATE_NAME, FDSX_STATUS, FDSX_DATA_PATH,
      FDSX_THREAD_ID, FDSX_FLOW_NAME, FDSX_HOOKS

    Args:
        hooks: Ordered list of HookEntry objects to execute.
        state_name: Name of the current state.
        status: Lifecycle status ("starting", "completed", or "failed").
        data_path: Path to the state data JSON file passed as $3 / FDSX_DATA_PATH.
        thread_id: Current run thread ID.
        flow_name: Name of the flow.
        event: Lifecycle event that triggered this hook invocation ("on_state_start" or
            "on_state_end"). Exported as FDSX_HOOKS; overrides any inherited value.

    Raises:
        HookAbortError: When a hook exits non-zero and its on_failure is "abort".
    """
    env = {
        **os.environ,
        ENV_STATE_NAME: state_name,
        ENV_STATUS: status,
        ENV_DATA_PATH: str(data_path),
        ENV_THREAD_ID: thread_id,
        ENV_FLOW_NAME: flow_name,
        ENV_HOOKS: event,
    }

    # Build positional arg suffix (safely quoted)
    positional_args = (
        f" {shlex.quote(state_name)}"
        f" {shlex.quote(status)}"
        f" {shlex.quote(str(data_path))}"
    )

    for hook in hooks:
        full_command = hook.command + positional_args
        result = subprocess.run(full_command, shell=True, env=env)

        if result.returncode != 0:
            if hook.on_failure == "abort":
                raise HookAbortError(hook.command, result.returncode)
            else:
                # on_failure == "warn"
                logger.warning(
                    "Hook command exited with code %d (warn policy, continuing): %r",
                    result.returncode,
                    hook.command,
                )


def write_hook_data(
    data: dict[str, Any],
    *,
    state_name: str,
    filename: str,
    thread_id: str,
    base_dir: Path | None = None,
) -> Path:
    """Write per-state hook data JSON to the hooks directory.

    Writes to:
        <base_dir>/runs/<thread_id>/hooks/<state_name>/<filename>

    When base_dir is None, defaults to <CWD>/.fdsx.

    Directories are created with mode 0o700; the file is written with mode 0o600.

    Args:
        data: JSON-serialisable dict to write.
        state_name: Name of the state (used as subdirectory name).
        filename: File name — typically INPUT_FILENAME or OUTPUT_FILENAME.
        thread_id: Current run thread ID.
        base_dir: Override for the .fdsx root. Defaults to CWD/.fdsx.

    Returns:
        Path to the written JSON file.
    """
    fdsx_dir = base_dir if base_dir is not None else Path.cwd() / FDSX_DIR_NAME

    base_runs_dir = (fdsx_dir / RUNS_DIR_NAME).resolve()
    expected_hooks_base = (base_runs_dir / thread_id / HOOKS_DIR_NAME).resolve()
    hooks_dir = (expected_hooks_base / state_name).resolve()
    if (
        not str(hooks_dir).startswith(str(expected_hooks_base) + os.sep)
        and hooks_dir != expected_hooks_base
    ):
        raise ValueError(
            "Invalid thread_id or state_name: path resolved outside runs directory"
        )
    if not str(expected_hooks_base).startswith(str(base_runs_dir)):
        raise ValueError(
            "Invalid thread_id or state_name: path resolved outside runs directory"
        )

    hooks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    hooks_dir.chmod(0o700)

    file_path = hooks_dir / filename
    json_bytes = json.dumps(data, indent=2).encode("utf-8")

    fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, json_bytes)
    finally:
        os.close(fd)

    return file_path


def collect_workflow_hooks(
    event: Literal["on_workflow_start", "on_workflow_end"],
    *,
    global_hooks: HookConfig | None,
    project_hooks: HookConfig | None,
    flow_hooks: HookConfig | None,
) -> list[HookEntry]:
    """Collect and merge workflow-scope hooks from global, project, and flow configs.

    Merges in order: global → project → flow (no state level).
    Returns a single flat list of HookEntry objects.

    Args:
        event: "on_workflow_start" or "on_workflow_end".
        global_hooks: Hook config from global ~/.config/fdsx/config.yaml.
        project_hooks: Hook config from project .fdsx/config.yaml.
        flow_hooks: Hook config from the flow definition.

    Returns:
        Concatenated list of HookEntry objects in global → project → flow order.
    """
    result: list[HookEntry] = []
    for hook_config in (global_hooks, project_hooks, flow_hooks):
        if hook_config is not None:
            result.extend(getattr(hook_config, event))
    return result


def execute_workflow_hooks(
    hooks: list[HookEntry],
    *,
    status: str,
    thread_id: str,
    flow_name: str,
    event: Literal["on_workflow_start", "on_workflow_end"],
    timeout_seconds: float = 30.0,
) -> None:
    """Execute workflow-scope hook commands in order.

    Each command is run with ``shell=True`` and a timeout. Receives:
    - Environment variables: FDSX_HOOKS, FDSX_STATUS, FDSX_FLOW_NAME, FDSX_THREAD_ID
    - FDSX_STATE_NAME and FDSX_DATA_PATH are explicitly omitted.

    Non-zero exit codes and timeouts log a warning and continue — never raises.

    Args:
        hooks: Ordered list of HookEntry objects to execute.
        status: Lifecycle status ("starting", "completed", "failed", "aborted").
        thread_id: Current run thread ID.
        flow_name: Name of the flow.
        event: Lifecycle event ("on_workflow_start" or "on_workflow_end").
        timeout_seconds: Per-hook subprocess timeout in seconds. Defaults to 30.0.
    """
    env = {
        k: v for k, v in os.environ.items() if k not in (ENV_STATE_NAME, ENV_DATA_PATH)
    }
    env[ENV_HOOKS] = event
    env[ENV_STATUS] = status
    env[ENV_FLOW_NAME] = flow_name
    env[ENV_THREAD_ID] = thread_id

    for hook in hooks:
        try:
            result = subprocess.run(
                hook.command, shell=True, env=env, timeout=timeout_seconds
            )
            if result.returncode != 0:
                logger.warning(
                    "Workflow hook exited with code %d (warn-only): %r",
                    result.returncode,
                    hook.command,
                )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Workflow hook timed out after %s s (killed): %r",
                timeout_seconds,
                hook.command,
            )


def collect_hooks(
    event: Literal["on_state_start", "on_state_end"],
    *,
    global_hooks: HookConfig | None,
    project_hooks: HookConfig | None,
    flow_hooks: HookConfig | None,
    state_hooks: HookConfig | None,
) -> list[HookEntry]:
    """Collect and merge hooks from all configuration levels for a given event.

    Merges in order: global → project → flow → state.
    Returns a single flat list of HookEntry objects.

    Args:
        event: "on_state_start" or "on_state_end".
        global_hooks: Hook config from global ~/.config/fdsx/config.yaml.
        project_hooks: Hook config from project .fdsx/config.yaml.
        flow_hooks: Hook config from the flow definition.
        state_hooks: Hook config from the individual state definition.

    Returns:
        Concatenated list of HookEntry objects in global → project → flow → state order.
    """
    result: list[HookEntry] = []
    for hook_config in (global_hooks, project_hooks, flow_hooks, state_hooks):
        if hook_config is not None:
            result.extend(getattr(hook_config, event))
    return result

"""Integration tests for list_threads using get_state() public API (T032)."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.checkpoint.manager import CheckpointManager
from fdsx.core import engine
from tests import FIXTURES_DIR


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def simple_flow_path():
    return FIXTURES_DIR / "simple_flow.yaml"


@pytest.fixture
def wait_resume_flow_path():
    return FIXTURES_DIR / "wait_resume_flow.yaml"


class TestListThreadsPublicAPI:
    def test_completed_thread_status(self, temp_dir, simple_flow_path):
        """T032: list_threads returns 'completed' for a fully executed flow."""
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-api-completed"

        engine.run_flow(
            simple_flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
        )

        manager = CheckpointManager(base_dir=base_dir)
        threads = manager.list_threads()

        assert len(threads) >= 1
        thread_info = next(t for t in threads if t["thread_id"] == thread_id)
        assert thread_info["status"] == "completed"
        assert thread_info["flow_name"] != thread_id
        assert thread_info["current_state"] != ""
        assert thread_info["started_at"] != ""

    def test_waiting_thread_status(self, temp_dir, wait_resume_flow_path):
        """T032: list_threads returns 'waiting' for a flow paused at a Wait state."""
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-api-waiting"

        # Run until Wait state, then simulate crash at the prompt so the interrupt
        # checkpoint is persisted but execution never resumes.
        with (
            pytest.raises(RuntimeError, match="Flow execution failed"),
            patch(
                "fdsx.core.engine.interrupts.display_wait_prompt",
                side_effect=Exception("simulated crash"),
            ),
        ):
            engine.run_flow(
                wait_resume_flow_path,
                thread_id=thread_id,
                base_dir=base_dir,
            )

        manager = CheckpointManager(base_dir=base_dir)
        threads = manager.list_threads()

        assert len(threads) >= 1
        thread_info = next(t for t in threads if t["thread_id"] == thread_id)
        assert thread_info["status"] == "waiting"

    def test_stopped_thread_status(self, temp_dir):
        """T032: list_threads returns 'stopped' for a flow that ended with an error."""
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-api-stopped"

        # Inline flow: first state succeeds, second fails via exit 1
        flow_yaml = """\
name: Stopped Flow Test
description: Flow that fails at the second state for testing
start_at: ok_state
states:
  ok_state:
    type: task
    provider: system
    command: "echo ok"
    result_path: $.ok_output
    next: fail_state
  fail_state:
    type: task
    provider: system
    command: "exit 1"
    result_path: $.fail_output
    end: true
"""
        flow_file = temp_dir / "stopped_flow.yaml"
        flow_file.write_text(flow_yaml)

        with pytest.raises(RuntimeError):
            engine.run_flow(
                flow_file,
                thread_id=thread_id,
                base_dir=base_dir,
            )

        manager = CheckpointManager(base_dir=base_dir)
        threads = manager.list_threads()

        assert len(threads) >= 1
        thread_info = next(t for t in threads if t["thread_id"] == thread_id)
        assert thread_info["status"] == "stopped"

    def test_waiting_thread_with_profile_flow(self, temp_dir):
        """list_threads resolves 'waiting' for a thread whose flow uses a config profile."""
        import yaml

        from fdsx.providers.base import ProviderResult

        base_dir = temp_dir / ".fdsx"
        thread_id = "test-api-waiting-profile"

        # Config must go to <project_dir>/.fdsx/config.yaml
        # load_config(project_dir=temp_dir) → _resolve_project_config_dir(temp_dir)
        # → looks for temp_dir/.fdsx/ directory → reads temp_dir/.fdsx/config.yaml
        # ProfileConfig requires a valid LLM provider and model (not "system").
        base_dir.mkdir(parents=True, exist_ok=True)
        config_path = base_dir / "config.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "profiles": {
                        "test_profile": {
                            "provider": "claude",
                            "model": "claude-haiku-4-5",
                        }
                    }
                }
            )
        )

        # Flow uses `profile: test_profile` with NO `provider:` key.
        # If config_profiles is None, load_flow fails to resolve the profile
        # (provider is a required field), so get_state() is never reached
        # and status stays "stopped" instead of "waiting".
        flow_yaml = """\
name: Profile Wait Flow
description: Flow with a wait state using a config profile
start_at: before_wait
states:
  before_wait:
    type: task
    profile: test_profile
    prompt_template: "say ready"
    result_path: $.ready
    next: wait_here
  wait_here:
    type: wait
    message: "Continue?"
    choices:
      - "yes"
      - "no"
    result_path: $.choice
    end: true
"""
        flow_file = temp_dir / "profile_wait_flow.yaml"
        flow_file.write_text(flow_yaml)

        fake = ProviderResult(exit_code=0, stdout="ready", stderr="")
        with (
            pytest.raises(RuntimeError, match="Flow execution failed"),
            patch("fdsx.providers.claude._run_subprocess", return_value=fake),
            patch(
                "fdsx.core.engine.interrupts.display_wait_prompt",
                side_effect=Exception("simulated crash"),
            ),
        ):
            engine.run_flow(
                flow_file,
                thread_id=thread_id,
                base_dir=base_dir,
            )

        manager = CheckpointManager(base_dir=base_dir)
        threads = manager.list_threads()

        thread_info = next(t for t in threads if t["thread_id"] == thread_id)
        assert thread_info["status"] == "waiting"

    def test_legacy_thread_without_flow_path_in_run_json(
        self, temp_dir, simple_flow_path
    ):
        """list_threads returns correct status for threads whose run.json lacks flow_path."""
        base_dir = temp_dir / ".fdsx"
        thread_id = "test-legacy-no-flow-path"

        engine.run_flow(simple_flow_path, thread_id=thread_id, base_dir=base_dir)

        # Simulate legacy run.json by removing the flow_path field
        run_json_path = base_dir / "runs" / thread_id / "run.json"
        with run_json_path.open() as f:
            run_log = json.load(f)
        run_log.pop("flow_path", None)
        with run_json_path.open("w") as f:
            json.dump(run_log, f)

        manager = CheckpointManager(base_dir=base_dir)
        threads = manager.list_threads()
        thread_info = next(t for t in threads if t["thread_id"] == thread_id)
        assert thread_info["status"] == "completed"

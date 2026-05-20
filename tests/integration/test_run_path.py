"""Integration tests for {run_path} global template variable."""

import yaml
from pathlib import Path
from unittest.mock import patch

import pytest

from fdsx.core.engine import run_flow
from fdsx.logging.recorder import RUNS_DIR_NAME
from fdsx.providers.base import ProviderResult
from tests import FIXTURES_DIR


def _single_state_flow(command: str, result_key: str = "out") -> dict:
    """Helper: one-state flow using the system provider."""
    return {
        "name": "Run Path Single State Flow",
        "description": "Test flow for run_path resolution",
        "start_at": "only",
        "states": {
            "only": {
                "type": "task",
                "provider": "system",
                "command": command,
                "result_path": f"$.{result_key}",
                "end": True,
            }
        },
    }


class TestRunPathPromptResolution:
    def test_run_path_in_command_resolves_to_absolute_path(self, tmp_path):
        """B1+B2: {run_path} in a task command resolves to the run's absolute directory."""
        path = tmp_path / "echo_run_path.yaml"
        path.write_text(yaml.dump(_single_state_flow("echo {run_path}")))

        result = run_flow(path, base_dir=tmp_path)

        captured = result.results["out"].strip()
        assert Path(captured).is_absolute(), (
            f"{captured!r} is not an absolute path — {'{run_path}'} was not resolved"
        )


class TestRunPathCommandResolution:
    def test_run_path_in_command_resolves_to_expected_run_dir(self, tmp_path):
        """B2: {run_path} resolves to <base_dir>/runs/<thread_id>."""
        thread_id = "test-cmd-resolution"
        path = tmp_path / "cmd_res.yaml"
        path.write_text(yaml.dump(_single_state_flow("echo {run_path}")))

        result = run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        captured = result.results["out"].strip()
        expected = str(tmp_path / RUNS_DIR_NAME / thread_id)
        assert captured == expected, (
            f"Expected run_path == {expected!r}, got {captured!r}"
        )


class TestRunPathResumeStability:
    def test_run_path_matches_deterministic_run_dir(self, tmp_path):
        """B3: run_path is stable — equals <base_dir>/runs/<thread_id> regardless of resume.

        The engine persists _meta.run_dir in the LangGraph checkpoint, so the same
        thread_id always maps to the same path across initial run and resume.
        """
        thread_id = "stable-thread-abc"
        path = tmp_path / "stable.yaml"
        path.write_text(yaml.dump(_single_state_flow("echo {run_path}")))

        result = run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        captured = result.results["out"].strip()
        expected = str(tmp_path / RUNS_DIR_NAME / thread_id)
        assert captured == expected, (
            f"run_path {captured!r} does not match expected run dir {expected!r}. "
            "After resume, _meta.run_dir is re-derived from thread_id so it must be stable."
        )


class TestRunPathUserInputOverrideBlocked:
    def test_user_input_run_path_is_ignored(self, tmp_path):
        """B4: inputs={"run_path": "/evil"} must not override {run_path} resolution."""
        thread_id = "test-no-user-override"
        path = tmp_path / "override_user.yaml"
        path.write_text(yaml.dump(_single_state_flow("echo {run_path}")))

        result = run_flow(
            path, inputs={"run_path": "/evil"}, thread_id=thread_id, base_dir=tmp_path
        )

        captured = result.results["out"].strip()
        expected = str(tmp_path / RUNS_DIR_NAME / thread_id)
        assert captured != "/evil", (
            "User-supplied run_path=/evil was not blocked — inject_builtin_vars must override it"
        )
        assert captured == expected, (
            f"Expected real run dir {expected!r}, got {captured!r}"
        )


class TestRunPathStateResultOverrideBlocked:
    def test_state_writing_run_path_result_cannot_override_downstream_resolution(
        self, tmp_path
    ):
        """B5: a state with result_path=$.run_path cannot poison downstream {run_path}."""
        thread_id = "test-no-state-override"
        flow_yaml = {
            "name": "State Override Test",
            "description": "Test that state result cannot override run_path",
            "start_at": "poisoner",
            "states": {
                "poisoner": {
                    "type": "task",
                    "provider": "system",
                    "command": "echo not-a-real-path",
                    "result_path": "$.run_path",
                    "next": "consumer",
                },
                "consumer": {
                    "type": "task",
                    "provider": "system",
                    "command": "echo {run_path}",
                    "result_path": "$.final_path",
                    "end": True,
                },
            },
        }
        path = tmp_path / "state_override.yaml"
        path.write_text(yaml.dump(flow_yaml))

        result = run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        final = result.results["final_path"].strip()
        expected = str(tmp_path / RUNS_DIR_NAME / thread_id)
        assert final != "not-a-real-path", (
            "State-written run_path value leaked to downstream — inject_builtin_vars must override it"
        )
        assert final == expected, f"Expected real run dir {expected!r}, got {final!r}"


class TestRunPathEndToEndFileHandoff:
    def test_file_written_in_state1_readable_in_state2(self, tmp_path):
        """B6: state1 writes {run_path}/artifact.txt; state2 reads it back via {run_path}."""
        path = FIXTURES_DIR / "run_path_flow.yaml"

        result = run_flow(path, base_dir=tmp_path)

        assert "content" in result.results, "state2 should have captured $.content"
        assert result.results["content"].strip() == "hello", (
            f"Expected 'hello', got {result.results['content']!r}"
        )

        write_path = result.results.get("write_path", "").strip()
        assert Path(write_path).is_absolute(), (
            f"write_path {write_path!r} is not absolute — {'{run_path}'} was not resolved in state1"
        )


class TestRunPathMetaInputProtection:
    def test_user_input_meta_override_is_ignored(self, tmp_path):
        """inputs={"_meta": {"run_dir": "/evil"}} must not replace the real _meta."""
        thread_id = "test-meta-input-guard"
        path = tmp_path / "meta_input.yaml"
        path.write_text(yaml.dump(_single_state_flow("echo {run_path}")))

        result = run_flow(
            path,
            inputs={"_meta": {"run_dir": "/evil"}},
            thread_id=thread_id,
            base_dir=tmp_path,
        )

        captured = result.results["out"].strip()
        expected = str(tmp_path / RUNS_DIR_NAME / thread_id)
        assert captured != "/evil", (
            "_meta override via inputs was not blocked — run.py must skip _meta key"
        )
        assert captured == expected, (
            f"Expected real run dir {expected!r}, got {captured!r}"
        )


class TestRunPathMetaResultProtection:
    def test_state_result_meta_run_dir_override_is_ignored(self, tmp_path):
        """result_path '$._meta.run_dir' must not poison downstream {run_path} resolution."""
        thread_id = "test-meta-result-guard"
        flow_yaml = {
            "name": "Meta Result Path Test",
            "description": "Test that result_path $._meta.run_dir cannot override run_path",
            "start_at": "poisoner",
            "states": {
                "poisoner": {
                    "type": "task",
                    "provider": "system",
                    "command": "echo /evil",
                    "result_path": "$._meta.run_dir",
                    "next": "consumer",
                },
                "consumer": {
                    "type": "task",
                    "provider": "system",
                    "command": "echo {run_path}",
                    "result_path": "$.final_path",
                    "end": True,
                },
            },
        }
        path = tmp_path / "meta_result.yaml"
        path.write_text(yaml.dump(flow_yaml))

        result = run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        final = result.results["final_path"].strip()
        expected = str(tmp_path / RUNS_DIR_NAME / thread_id)
        assert final != "/evil", (
            "result_path $._meta.run_dir was not stripped — _strip_reserved_keys must block this"
        )
        assert final == expected, f"Expected real run dir {expected!r}, got {final!r}"


class TestRunPathPromptTemplateResolution:
    def test_run_path_in_prompt_template_resolves_to_run_dir(self, tmp_path):
        """B1: {run_path} in prompt_template resolves correctly via mocked claude provider."""
        thread_id = "test-prompt-tmpl"
        captured_prompts: list[str] = []

        def fake_subprocess(args: list[str], **kwargs: object) -> ProviderResult:
            if "-p" in args:
                idx = args.index("-p")
                captured_prompts.append(args[idx + 1])
            return ProviderResult(exit_code=0, stdout="ok", stderr="")

        flow_yaml = {
            "name": "Prompt Template Run Path Test",
            "description": "Test prompt_template resolution of run_path",
            "start_at": "only",
            "states": {
                "only": {
                    "type": "task",
                    "provider": "claude",
                    "model": "sonnet",
                    "prompt_template": "Write to {run_path}/out.txt",
                    "result_path": "$.out",
                    "end": True,
                }
            },
        }
        path = tmp_path / "prompt_tmpl.yaml"
        path.write_text(yaml.dump(flow_yaml))

        with patch(
            "fdsx.providers.claude._run_subprocess", side_effect=fake_subprocess
        ):
            run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        assert captured_prompts, "No prompt was captured"
        expected_dir = str(tmp_path / RUNS_DIR_NAME / thread_id)
        assert expected_dir in captured_prompts[0], (
            f"Expected run_dir {expected_dir!r} in prompt, got {captured_prompts[0]!r}"
        )

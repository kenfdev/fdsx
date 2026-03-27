"""Unit tests for fdsx.core.hooks — T019, T020, T021."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fdsx.core.hooks import (
    ENV_DATA_PATH,
    ENV_FLOW_NAME,
    ENV_STATE_NAME,
    ENV_STATUS,
    ENV_THREAD_ID,
    HOOKS_DIR_NAME,
    INPUT_FILENAME,
    OUTPUT_FILENAME,
    RUNS_DIR_NAME,
    HookAbortError,
    collect_hooks,
    execute_hooks,
    write_hook_data,
)
from fdsx.models.flow import HookConfig, HookEntry

# ---------------------------------------------------------------------------
# T019: execute_hooks
# ---------------------------------------------------------------------------


class TestExecuteHooks:
    """Tests for execute_hooks()."""

    def _make_hook(self, command: str, on_failure: str = "warn") -> HookEntry:
        return HookEntry(command=command, on_failure=on_failure)  # type: ignore[arg-type]

    def test_empty_list_does_nothing(self, tmp_path: Path) -> None:
        """No subprocess calls when hook list is empty."""
        data_path = tmp_path / "data.json"
        with patch("subprocess.run") as mock_run:
            execute_hooks(
                [],
                state_name="MyState",
                status="starting",
                data_path=data_path,
                thread_id="tid-001",
                flow_name="MyFlow",
            )
        mock_run.assert_not_called()

    def test_single_hook_called_with_shell_true(self, tmp_path: Path) -> None:
        """subprocess.run is called with shell=True."""
        hook = self._make_hook("echo hello")
        data_path = tmp_path / "data.json"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_hooks(
                [hook],
                state_name="S1",
                status="starting",
                data_path=data_path,
                thread_id="t1",
                flow_name="F1",
            )

        assert mock_run.call_count == 1
        _, kwargs = mock_run.call_args
        assert kwargs.get("shell") is True

    def test_positional_args_appended_to_command(self, tmp_path: Path) -> None:
        """$1, $2, $3 are appended as quoted positional args."""
        hook = self._make_hook("myscript.sh")
        data_path = tmp_path / "state_data.json"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_hooks(
                [hook],
                state_name="MyState",
                status="completed",
                data_path=data_path,
                thread_id="t1",
                flow_name="F1",
            )

        full_cmd: str = mock_run.call_args[0][0]
        assert "MyState" in full_cmd
        assert "completed" in full_cmd
        assert str(data_path) in full_cmd

    def test_env_vars_passed_correctly(self, tmp_path: Path) -> None:
        """All five FDSX_ environment variables are present in env."""
        hook = self._make_hook("true")
        data_path = tmp_path / "data.json"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_hooks(
                [hook],
                state_name="StateX",
                status="failed",
                data_path=data_path,
                thread_id="tid-42",
                flow_name="FlowY",
            )

        env = mock_run.call_args[1]["env"]
        assert env[ENV_STATE_NAME] == "StateX"
        assert env[ENV_STATUS] == "failed"
        assert env[ENV_DATA_PATH] == str(data_path)
        assert env[ENV_THREAD_ID] == "tid-42"
        assert env[ENV_FLOW_NAME] == "FlowY"

    def test_warn_on_failure_does_not_raise(self, tmp_path: Path) -> None:
        """on_failure='warn' logs a warning and continues without raising."""
        hook = self._make_hook("false", on_failure="warn")
        data_path = tmp_path / "data.json"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            # Must not raise
            execute_hooks(
                [hook],
                state_name="S",
                status="starting",
                data_path=data_path,
                thread_id="t",
                flow_name="F",
            )

    def test_abort_on_failure_raises_hook_abort_error(self, tmp_path: Path) -> None:
        """on_failure='abort' raises HookAbortError on non-zero exit."""
        hook = self._make_hook("false", on_failure="abort")
        data_path = tmp_path / "data.json"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=2)
            with pytest.raises(HookAbortError) as exc_info:
                execute_hooks(
                    [hook],
                    state_name="S",
                    status="starting",
                    data_path=data_path,
                    thread_id="t",
                    flow_name="F",
                )
        assert exc_info.value.return_code == 2
        assert exc_info.value.command == "false"

    def test_abort_stops_subsequent_hooks(self, tmp_path: Path) -> None:
        """After abort, remaining hooks are not executed."""
        hooks = [
            self._make_hook("cmd1", on_failure="abort"),
            self._make_hook("cmd2", on_failure="warn"),
        ]
        data_path = tmp_path / "data.json"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            with pytest.raises(HookAbortError):
                execute_hooks(
                    hooks,
                    state_name="S",
                    status="starting",
                    data_path=data_path,
                    thread_id="t",
                    flow_name="F",
                )
        # Only cmd1 ran; cmd2 was skipped
        assert mock_run.call_count == 1

    def test_warn_continues_to_next_hook(self, tmp_path: Path) -> None:
        """After warn failure, next hooks still execute."""
        hooks = [
            self._make_hook("cmd1", on_failure="warn"),
            self._make_hook("cmd2", on_failure="warn"),
        ]
        data_path = tmp_path / "data.json"
        return_codes = [1, 0]

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [MagicMock(returncode=rc) for rc in return_codes]
            execute_hooks(
                hooks,
                state_name="S",
                status="starting",
                data_path=data_path,
                thread_id="t",
                flow_name="F",
            )
        assert mock_run.call_count == 2

    def test_hook_abort_error_message_contains_command(self, tmp_path: Path) -> None:
        """HookAbortError message includes the command string."""
        hook = self._make_hook("my-script.sh", on_failure="abort")
        data_path = tmp_path / "data.json"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=5)
            with pytest.raises(HookAbortError) as exc_info:
                execute_hooks(
                    [hook],
                    state_name="S",
                    status="starting",
                    data_path=data_path,
                    thread_id="t",
                    flow_name="F",
                )
        assert "my-script.sh" in str(exc_info.value)

    def test_multiple_hooks_run_in_order(self, tmp_path: Path) -> None:
        """Multiple hooks run in list order."""
        hooks = [
            self._make_hook("hook-a"),
            self._make_hook("hook-b"),
            self._make_hook("hook-c"),
        ]
        data_path = tmp_path / "data.json"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_hooks(
                hooks,
                state_name="S",
                status="starting",
                data_path=data_path,
                thread_id="t",
                flow_name="F",
            )

        assert mock_run.call_count == 3
        cmd_a: str = mock_run.call_args_list[0][0][0]
        cmd_b: str = mock_run.call_args_list[1][0][0]
        cmd_c: str = mock_run.call_args_list[2][0][0]
        assert cmd_a.startswith("hook-a ")
        assert cmd_b.startswith("hook-b ")
        assert cmd_c.startswith("hook-c ")

    def test_special_characters_in_state_name_are_quoted(self, tmp_path: Path) -> None:
        """State names with spaces/special chars are shell-quoted as positional args."""
        hook = self._make_hook("myscript.sh")
        data_path = tmp_path / "data.json"

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_hooks(
                [hook],
                state_name="my state; rm -rf /",
                status="starting",
                data_path=data_path,
                thread_id="t",
                flow_name="F",
            )

        full_cmd: str = mock_run.call_args[0][0]
        # Verify the dangerous string is properly quoted (not expanded)
        assert "rm -rf" not in full_cmd.replace("'my state; rm -rf /'", "")


# ---------------------------------------------------------------------------
# T020: write_hook_data
# ---------------------------------------------------------------------------


class TestWriteHookData:
    """Tests for write_hook_data()."""

    def test_writes_input_json_to_correct_path(self, tmp_path: Path) -> None:
        """input.json is written under hooks/<state_name>/input.json."""
        data = {"key": "value", "count": 42}
        file_path = write_hook_data(
            data,
            state_name="Planner",
            filename=INPUT_FILENAME,
            thread_id="thread-001",
            base_dir=tmp_path,
        )

        expected = (
            tmp_path
            / RUNS_DIR_NAME
            / "thread-001"
            / HOOKS_DIR_NAME
            / "Planner"
            / INPUT_FILENAME
        )
        assert file_path == expected
        assert file_path.exists()

    def test_writes_output_json_to_correct_path(self, tmp_path: Path) -> None:
        """output.json is written under hooks/<state_name>/output.json."""
        data = {"result": "done"}
        file_path = write_hook_data(
            data,
            state_name="Executor",
            filename=OUTPUT_FILENAME,
            thread_id="thread-002",
            base_dir=tmp_path,
        )

        expected = (
            tmp_path
            / RUNS_DIR_NAME
            / "thread-002"
            / HOOKS_DIR_NAME
            / "Executor"
            / OUTPUT_FILENAME
        )
        assert file_path == expected

    def test_written_content_is_valid_json(self, tmp_path: Path) -> None:
        """File content deserialises back to the original data dict."""
        data = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
        file_path = write_hook_data(
            data,
            state_name="S",
            filename=INPUT_FILENAME,
            thread_id="t",
            base_dir=tmp_path,
        )

        with open(file_path) as f:
            loaded = json.load(f)
        assert loaded == data

    def test_file_permissions_are_0o600(self, tmp_path: Path) -> None:
        """File is created with mode 0o600."""
        file_path = write_hook_data(
            {},
            state_name="S",
            filename=INPUT_FILENAME,
            thread_id="t",
            base_dir=tmp_path,
        )

        file_mode = stat.S_IMODE(os.stat(file_path).st_mode)
        assert file_mode == 0o600

    def test_directory_permissions_are_0o700(self, tmp_path: Path) -> None:
        """State hooks directory is created with mode 0o700."""
        write_hook_data(
            {},
            state_name="MyState",
            filename=INPUT_FILENAME,
            thread_id="t",
            base_dir=tmp_path,
        )

        hooks_state_dir = tmp_path / RUNS_DIR_NAME / "t" / HOOKS_DIR_NAME / "MyState"
        dir_mode = stat.S_IMODE(os.stat(hooks_state_dir).st_mode)
        assert dir_mode == 0o700

    def test_creates_intermediate_directories(self, tmp_path: Path) -> None:
        """Intermediate directories are created automatically."""
        file_path = write_hook_data(
            {"x": 1},
            state_name="DeepState",
            filename=OUTPUT_FILENAME,
            thread_id="new-thread",
            base_dir=tmp_path,
        )

        assert file_path.parent.is_dir()
        assert file_path.exists()

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        """A second write to the same path overwrites the first."""
        first_data = {"version": 1}
        second_data = {"version": 2}

        write_hook_data(
            first_data,
            state_name="S",
            filename=INPUT_FILENAME,
            thread_id="t",
            base_dir=tmp_path,
        )
        file_path = write_hook_data(
            second_data,
            state_name="S",
            filename=INPUT_FILENAME,
            thread_id="t",
            base_dir=tmp_path,
        )

        with open(file_path) as f:
            loaded = json.load(f)
        assert loaded == second_data

    def test_default_base_dir_uses_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When base_dir is None, path is rooted at CWD/.fdsx/."""
        monkeypatch.chdir(tmp_path)
        file_path = write_hook_data(
            {},
            state_name="S",
            filename=INPUT_FILENAME,
            thread_id="t",
        )

        expected_root = (
            tmp_path
            / ".fdsx"
            / RUNS_DIR_NAME
            / "t"
            / HOOKS_DIR_NAME
            / "S"
            / INPUT_FILENAME
        )
        assert file_path == expected_root

    def test_returns_path_object(self, tmp_path: Path) -> None:
        """Return value is a pathlib.Path."""
        result = write_hook_data(
            {},
            state_name="S",
            filename=INPUT_FILENAME,
            thread_id="t",
            base_dir=tmp_path,
        )
        assert isinstance(result, Path)

    def test_traversal_via_thread_id_raises(self, tmp_path: Path) -> None:
        """thread_id with path traversal segments raises ValueError."""
        with pytest.raises(ValueError, match="path resolved outside runs directory"):
            write_hook_data(
                {},
                state_name="S",
                filename=INPUT_FILENAME,
                thread_id="../../../etc",
                base_dir=tmp_path,
            )

    def test_traversal_via_state_name_raises(self, tmp_path: Path) -> None:
        """state_name with path traversal segments raises ValueError."""
        with pytest.raises(ValueError, match="path resolved outside runs directory"):
            write_hook_data(
                {},
                state_name="../../passwd",
                filename=INPUT_FILENAME,
                thread_id="t",
                base_dir=tmp_path,
            )


# ---------------------------------------------------------------------------
# T021: collect_hooks
# ---------------------------------------------------------------------------


class TestCollectHooks:
    """Tests for collect_hooks()."""

    def _make_config(self, commands: list[str], event: str = "on_start") -> HookConfig:
        entries = [HookEntry(command=cmd) for cmd in commands]
        kwargs: dict = {"on_start": [], "on_complete": []}
        kwargs[event] = entries
        return HookConfig(**kwargs)

    def test_all_none_returns_empty_list(self) -> None:
        """All-None configs produces an empty list."""
        result = collect_hooks(
            "on_start",
            global_hooks=None,
            project_hooks=None,
            flow_hooks=None,
            state_hooks=None,
        )
        assert result == []

    def test_single_level_global(self) -> None:
        """Only global hooks are returned."""
        global_cfg = self._make_config(["global-hook"])
        result = collect_hooks(
            "on_start",
            global_hooks=global_cfg,
            project_hooks=None,
            flow_hooks=None,
            state_hooks=None,
        )
        assert len(result) == 1
        assert result[0].command == "global-hook"

    def test_single_level_state(self) -> None:
        """Only state hooks are returned."""
        state_cfg = self._make_config(["state-hook"])
        result = collect_hooks(
            "on_start",
            global_hooks=None,
            project_hooks=None,
            flow_hooks=None,
            state_hooks=state_cfg,
        )
        assert len(result) == 1
        assert result[0].command == "state-hook"

    def test_merge_order_global_project_flow_state(self) -> None:
        """Hooks are concatenated in global → project → flow → state order."""
        global_cfg = self._make_config(["g1"])
        project_cfg = self._make_config(["p1"])
        flow_cfg = self._make_config(["fl1"])
        state_cfg = self._make_config(["st1"])

        result = collect_hooks(
            "on_start",
            global_hooks=global_cfg,
            project_hooks=project_cfg,
            flow_hooks=flow_cfg,
            state_hooks=state_cfg,
        )

        assert [h.command for h in result] == ["g1", "p1", "fl1", "st1"]

    def test_multiple_hooks_per_level(self) -> None:
        """Multiple hooks within a level are included in order."""
        global_cfg = self._make_config(["g1", "g2", "g3"])
        state_cfg = self._make_config(["s1", "s2"])

        result = collect_hooks(
            "on_start",
            global_hooks=global_cfg,
            project_hooks=None,
            flow_hooks=None,
            state_hooks=state_cfg,
        )

        assert [h.command for h in result] == ["g1", "g2", "g3", "s1", "s2"]

    def test_on_complete_event(self) -> None:
        """on_complete event selects the correct hook list."""
        config = HookConfig(
            on_start=[HookEntry(command="start-hook")],
            on_complete=[HookEntry(command="complete-hook")],
        )

        result_start = collect_hooks(
            "on_start",
            global_hooks=config,
            project_hooks=None,
            flow_hooks=None,
            state_hooks=None,
        )
        result_complete = collect_hooks(
            "on_complete",
            global_hooks=config,
            project_hooks=None,
            flow_hooks=None,
            state_hooks=None,
        )

        assert len(result_start) == 1
        assert result_start[0].command == "start-hook"
        assert len(result_complete) == 1
        assert result_complete[0].command == "complete-hook"

    def test_on_failure_policy_preserved(self) -> None:
        """on_failure values are preserved through collection."""
        cfg = HookConfig(
            on_start=[
                HookEntry(command="cmd-abort", on_failure="abort"),
                HookEntry(command="cmd-warn", on_failure="warn"),
            ]
        )

        result = collect_hooks(
            "on_start",
            global_hooks=cfg,
            project_hooks=None,
            flow_hooks=None,
            state_hooks=None,
        )

        assert result[0].on_failure == "abort"
        assert result[1].on_failure == "warn"

    def test_returns_list_of_hook_entry(self) -> None:
        """Return type is a list of HookEntry."""
        cfg = self._make_config(["cmd"])
        result = collect_hooks(
            "on_start",
            global_hooks=cfg,
            project_hooks=None,
            flow_hooks=None,
            state_hooks=None,
        )
        assert isinstance(result, list)
        assert all(isinstance(h, HookEntry) for h in result)

    def test_skip_none_levels_seamlessly(self) -> None:
        """None levels are skipped without error; remaining levels are included."""
        project_cfg = self._make_config(["p1"])
        state_cfg = self._make_config(["s1"])

        result = collect_hooks(
            "on_start",
            global_hooks=None,
            project_hooks=project_cfg,
            flow_hooks=None,
            state_hooks=state_cfg,
        )

        assert [h.command for h in result] == ["p1", "s1"]

    def test_empty_hook_config_contributes_no_entries(self) -> None:
        """HookConfig with empty on_start adds nothing."""
        empty_cfg = HookConfig()
        state_cfg = self._make_config(["s1"])

        result = collect_hooks(
            "on_start",
            global_hooks=empty_cfg,
            project_hooks=None,
            flow_hooks=None,
            state_hooks=state_cfg,
        )

        assert [h.command for h in result] == ["s1"]

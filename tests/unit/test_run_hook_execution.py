"""Unit tests for collect_run_hooks() and execute_run_hooks() — T004, T005."""

from __future__ import annotations

import logging
import subprocess as _subprocess
from unittest.mock import MagicMock, patch

from fdsx.core.config import RunHookConfig
from fdsx.core.hooks import (
    ENV_DATA_PATH,
    ENV_FLOW_NAME,
    ENV_HOOKS,
    ENV_STATE_NAME,
    ENV_STATUS,
    ENV_THREAD_ID,
    collect_run_hooks,
    execute_run_hooks,
)
from fdsx.models.flow import HookEntry

# ---------------------------------------------------------------------------
# T004: TestCollectRunHooks
# ---------------------------------------------------------------------------


class TestCollectRunHooks:
    """Tests for collect_run_hooks()."""

    def _make_config(
        self, commands: list[str], event: str = "on_run_start"
    ) -> RunHookConfig:
        entries = [HookEntry(command=cmd) for cmd in commands]
        kwargs: dict = {"on_run_start": [], "on_run_end": []}
        kwargs[event] = entries
        return RunHookConfig(**kwargs)

    def test_merges_global_then_project_order(self) -> None:
        """Global hooks come before project hooks in the merged list."""
        global_cfg = self._make_config(["g1"])
        project_cfg = self._make_config(["p1"])

        result = collect_run_hooks(
            "on_run_start",
            global_run_hooks=global_cfg,
            project_run_hooks=project_cfg,
        )

        assert [h.command for h in result] == ["g1", "p1"]

    def test_none_global_returns_project_only(self) -> None:
        """None global config falls back to project hooks only."""
        project_cfg = self._make_config(["p1"])

        result = collect_run_hooks(
            "on_run_start",
            global_run_hooks=None,
            project_run_hooks=project_cfg,
        )

        assert [h.command for h in result] == ["p1"]

    def test_none_project_returns_global_only(self) -> None:
        """None project config falls back to global hooks only."""
        global_cfg = self._make_config(["g1"])

        result = collect_run_hooks(
            "on_run_start",
            global_run_hooks=global_cfg,
            project_run_hooks=None,
        )

        assert [h.command for h in result] == ["g1"]

    def test_both_none_returns_empty_list(self) -> None:
        """Both configs None produces an empty list."""
        result = collect_run_hooks(
            "on_run_start",
            global_run_hooks=None,
            project_run_hooks=None,
        )

        assert result == []

    def test_empty_run_hook_config_contributes_nothing(self) -> None:
        """RunHookConfig() with default empty lists adds nothing."""
        empty_cfg = RunHookConfig()

        result = collect_run_hooks(
            "on_run_start",
            global_run_hooks=empty_cfg,
            project_run_hooks=None,
        )

        assert result == []

    def test_on_run_end_event_selects_correct_list(self) -> None:
        """on_run_end event selects the on_run_end list, not on_run_start."""
        config = RunHookConfig(
            on_run_start=[HookEntry(command="start-hook")],
            on_run_end=[HookEntry(command="end-hook")],
        )

        result_start = collect_run_hooks(
            "on_run_start",
            global_run_hooks=config,
            project_run_hooks=None,
        )
        result_end = collect_run_hooks(
            "on_run_end",
            global_run_hooks=config,
            project_run_hooks=None,
        )

        assert len(result_start) == 1
        assert result_start[0].command == "start-hook"
        assert len(result_end) == 1
        assert result_end[0].command == "end-hook"


# ---------------------------------------------------------------------------
# T005: TestExecuteRunHooks
# ---------------------------------------------------------------------------


class TestExecuteRunHooks:
    """Tests for execute_run_hooks()."""

    def _make_hook(self, command: str, on_failure: str = "warn") -> HookEntry:
        return HookEntry(command=command, on_failure=on_failure)  # type: ignore[arg-type]

    def test_env_contains_fdsx_hooks_on_run_start(self) -> None:
        """FDSX_HOOKS is set to the event name."""
        hook = self._make_hook("echo test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_run_hooks(
                [hook],
                status="starting",
                event="on_run_start",
            )
        env = mock_run.call_args[1]["env"]
        assert env[ENV_HOOKS] == "on_run_start"

    def test_env_contains_fdsx_status(self) -> None:
        """FDSX_STATUS is set to the provided status value."""
        hook = self._make_hook("echo test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_run_hooks(
                [hook],
                status="starting",
                event="on_run_start",
            )
        env = mock_run.call_args[1]["env"]
        assert env[ENV_STATUS] == "starting"

    def test_env_omits_fdsx_flow_name(self, monkeypatch) -> None:
        """FDSX_FLOW_NAME must not appear in the hook environment."""
        monkeypatch.setenv(ENV_FLOW_NAME, "SomeFlow")
        hook = self._make_hook("echo test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_run_hooks(
                [hook],
                status="starting",
                event="on_run_start",
            )
        env = mock_run.call_args[1]["env"]
        assert ENV_FLOW_NAME not in env

    def test_env_omits_fdsx_thread_id(self, monkeypatch) -> None:
        """FDSX_THREAD_ID must not appear in the hook environment."""
        monkeypatch.setenv(ENV_THREAD_ID, "thread-abc")
        hook = self._make_hook("echo test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_run_hooks(
                [hook],
                status="starting",
                event="on_run_start",
            )
        env = mock_run.call_args[1]["env"]
        assert ENV_THREAD_ID not in env

    def test_env_omits_fdsx_state_name(self, monkeypatch) -> None:
        """FDSX_STATE_NAME must not appear in the hook environment."""
        monkeypatch.setenv(ENV_STATE_NAME, "my_state")
        hook = self._make_hook("echo test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_run_hooks(
                [hook],
                status="starting",
                event="on_run_start",
            )
        env = mock_run.call_args[1]["env"]
        assert ENV_STATE_NAME not in env

    def test_env_omits_fdsx_data_path(self, monkeypatch) -> None:
        """FDSX_DATA_PATH must not appear in the hook environment."""
        monkeypatch.setenv(ENV_DATA_PATH, "/some/path/data.json")
        hook = self._make_hook("echo test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_run_hooks(
                [hook],
                status="starting",
                event="on_run_start",
            )
        env = mock_run.call_args[1]["env"]
        assert ENV_DATA_PATH not in env

    def test_non_zero_exit_logs_warning_does_not_raise(self, caplog) -> None:
        """Non-zero exit code logs a warning but does not raise."""
        hook = self._make_hook("false")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            with caplog.at_level(logging.WARNING, logger="fdsx.core.hooks"):
                execute_run_hooks(
                    [hook],
                    status="starting",
                    event="on_run_start",
                )

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "Expected a WARNING log when run hook exits non-zero"
        )

    def test_timeout_expired_logs_warning_does_not_raise(self, caplog) -> None:
        """Timeout logs a warning but does not raise TimeoutExpired."""
        hook = self._make_hook("slow-command")
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = _subprocess.TimeoutExpired(
                cmd="slow-command", timeout=30.0
            )
            with caplog.at_level(logging.WARNING, logger="fdsx.core.hooks"):
                execute_run_hooks(
                    [hook],
                    status="starting",
                    event="on_run_start",
                )

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "Expected a WARNING log when run hook times out"
        )

    def test_empty_hooks_list_does_nothing(self) -> None:
        """Empty hooks list results in no subprocess calls."""
        with patch("subprocess.run") as mock_run:
            execute_run_hooks(
                [],
                status="starting",
                event="on_run_start",
            )
        mock_run.assert_not_called()

    def test_no_positional_args_in_command(self) -> None:
        """No positional arguments are appended to the hook command."""
        hook = self._make_hook("echo test")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            execute_run_hooks(
                [hook],
                status="starting",
                event="on_run_start",
            )
        full_cmd: str = mock_run.call_args[0][0]
        assert full_cmd == "echo test"

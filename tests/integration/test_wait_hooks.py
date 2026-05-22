"""Integration tests: on_wait_start / on_wait_end lifecycle hooks for wait states.

Covers:
  T001 — state-level hook execution, env vars, failure semantics, resume semantics
  T002 — multi-level config (flow-level and additive state + flow merge)
"""

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fdsx.core.config import FdsxConfig
from fdsx.core.engine import resume_flow, run_flow
from fdsx.core.hooks import HookAbortError
from fdsx.models.flow import HookConfig, HookEntry

# ─── YAML helpers ─────────────────────────────────────────────────────────────


def _write_flow(
    path: Path,
    *,
    state_hooks_yaml: str = "",
    flow_hooks_yaml: str = "",
) -> Path:
    """Write a minimal single-wait-state flow YAML.

    *flow_hooks_yaml* lines are indented 2 spaces under ``hooks:``.
    *state_hooks_yaml* lines are indented 6 spaces under ``    hooks:``.
    """
    lines = [
        "name: WaitHooksTest",
        "description: wait hooks integration test",
        "start_at: await_approval",
    ]
    if flow_hooks_yaml:
        lines.append("hooks:")
        for line in textwrap.dedent(flow_hooks_yaml).strip().splitlines():
            lines.append("  " + line)
    lines += [
        "states:",
        "  await_approval:",
        "    type: wait",
        '    message: "Approve the request?"',
        "    choices:",
        "      - approve",
        "      - reject",
        "    result_path: $.approval",
    ]
    if state_hooks_yaml:
        lines.append("    hooks:")
        for line in textwrap.dedent(state_hooks_yaml).strip().splitlines():
            lines.append("      " + line)
    lines.append("    end: true")
    path.write_text("\n".join(lines) + "\n")
    return path


def _write_two_wait_flow(path: Path, *, flow_hooks_yaml: str = "") -> Path:
    """Write a flow with two sequential wait states (for multi-level hook tests)."""
    lines = [
        "name: TwoWaitFlow",
        "description: two-wait flow",
        "start_at: wait_one",
    ]
    if flow_hooks_yaml:
        lines.append("hooks:")
        for line in textwrap.dedent(flow_hooks_yaml).strip().splitlines():
            lines.append("  " + line)
    lines += [
        "states:",
        "  wait_one:",
        "    type: wait",
        '    message: "First decision?"',
        "    choices:",
        "      - approve",
        "      - reject",
        "    result_path: $.decision1",
        "    next: wait_two",
        "  wait_two:",
        "    type: wait",
        '    message: "Second decision?"',
        "    choices:",
        "      - approve",
        "      - reject",
        "    result_path: $.decision2",
        "    end: true",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


# ─── T001: state-level hook execution ─────────────────────────────────────────


class TestWaitHookExecution:
    """on_wait_start and on_wait_end fire at the right moments in the wait lifecycle."""

    def test_on_wait_start_fires_before_interrupt(self, tmp_path: Path) -> None:
        """execute_hooks must be called with event='on_wait_start' when the flow reaches
        the wait state (before the LangGraph interrupt suspends execution)."""
        path = _write_flow(
            tmp_path / "flow.yaml",
            state_hooks_yaml="""\
                on_wait_start:
                  - command: "echo on_wait_start"
            """,
        )
        with (
            patch("fdsx.core.compiler.nodes.execute_hooks", create=True) as mock_exec,
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        start_calls = [
            c
            for c in mock_exec.call_args_list
            if c.kwargs.get("event") == "on_wait_start"
        ]
        assert len(start_calls) == 1, (
            "expected execute_hooks called once with event='on_wait_start'"
        )

    def test_on_wait_end_fires_after_selection(self, tmp_path: Path) -> None:
        """execute_hooks must be called with event='on_wait_end' after user_selection
        is written to result_path and before state completion."""
        path = _write_flow(
            tmp_path / "flow.yaml",
            state_hooks_yaml="""\
                on_wait_end:
                  - command: "echo on_wait_end"
            """,
        )
        with (
            patch("fdsx.core.compiler.nodes.execute_hooks", create=True) as mock_exec,
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        end_calls = [
            c
            for c in mock_exec.call_args_list
            if c.kwargs.get("event") == "on_wait_end"
        ]
        assert len(end_calls) == 1, (
            "expected execute_hooks called once with event='on_wait_end'"
        )

    def test_on_wait_start_fires_before_on_wait_end(self, tmp_path: Path) -> None:
        """on_wait_start must be invoked before on_wait_end in a single wait state execution."""
        call_log: list[str] = []

        def _track(hooks, *, event, **kwargs):  # type: ignore[no-untyped-def]
            call_log.append(event)

        path = _write_flow(
            tmp_path / "flow.yaml",
            state_hooks_yaml="""\
                on_wait_start:
                  - command: "echo start"
                on_wait_end:
                  - command: "echo end"
            """,
        )
        with (
            patch(
                "fdsx.core.compiler.nodes.execute_hooks",
                create=True,
                side_effect=_track,
            ),
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        assert "on_wait_start" in call_log, "on_wait_start never fired"
        assert "on_wait_end" in call_log, "on_wait_end never fired"
        assert call_log.index("on_wait_start") < call_log.index("on_wait_end"), (
            "on_wait_start must fire before on_wait_end"
        )


class TestWaitHookEnvVars:
    """Hooks receive the correct FDSX_WAIT_* environment variables."""

    def test_on_wait_start_env_includes_wait_message_and_choices(
        self, tmp_path: Path
    ) -> None:
        """The subprocess spawned by on_wait_start must have FDSX_WAIT_MESSAGE and
        FDSX_WAIT_CHOICES in its environment."""
        captured_envs: list[dict] = []

        def _capture_subprocess(*args, **kwargs):  # type: ignore[no-untyped-def]
            env = kwargs.get("env") or {}
            if "FDSX_WAIT_MESSAGE" in env or "FDSX_WAIT_CHOICES" in env:
                captured_envs.append(dict(env))
            return MagicMock(returncode=0, stdout="", stderr="")

        path = _write_flow(
            tmp_path / "flow.yaml",
            state_hooks_yaml="""\
                on_wait_start:
                  - command: "echo check_env"
            """,
        )
        with (
            patch("fdsx.core.hooks.subprocess.run", side_effect=_capture_subprocess),
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        assert captured_envs, (
            "No subprocess calls with FDSX_WAIT_MESSAGE/FDSX_WAIT_CHOICES — "
            "on_wait_start hook env vars not yet implemented"
        )
        env = captured_envs[0]
        assert "FDSX_WAIT_MESSAGE" in env, (
            "FDSX_WAIT_MESSAGE missing from on_wait_start env"
        )
        assert "FDSX_WAIT_CHOICES" in env, (
            "FDSX_WAIT_CHOICES missing from on_wait_start env"
        )
        choices = json.loads(env["FDSX_WAIT_CHOICES"])
        assert "approve" in choices

    def test_on_wait_end_env_includes_wait_selection(self, tmp_path: Path) -> None:
        """The subprocess spawned by on_wait_end must have FDSX_WAIT_SELECTION in its env."""
        captured_envs: list[dict] = []

        def _capture_subprocess(*args, **kwargs):  # type: ignore[no-untyped-def]
            env = kwargs.get("env") or {}
            if "FDSX_WAIT_SELECTION" in env:
                captured_envs.append(dict(env))
            return MagicMock(returncode=0, stdout="", stderr="")

        path = _write_flow(
            tmp_path / "flow.yaml",
            state_hooks_yaml="""\
                on_wait_end:
                  - command: "echo check_end_env"
            """,
        )
        with (
            patch("fdsx.core.hooks.subprocess.run", side_effect=_capture_subprocess),
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        assert captured_envs, (
            "No subprocess call with FDSX_WAIT_SELECTION — "
            "on_wait_end env var not yet implemented"
        )
        assert "FDSX_WAIT_SELECTION" in captured_envs[0]


class TestWaitHookFailureSemantics:
    """on_failure: abort and on_failure: warn behave correctly."""

    def test_on_failure_abort_halts_workflow(self, tmp_path: Path) -> None:
        """A hook with on_failure: abort that exits non-zero must raise HookAbortError,
        preventing subsequent states from running."""
        path = _write_flow(
            tmp_path / "flow.yaml",
            state_hooks_yaml="""\
                on_wait_start:
                  - command: "exit 1"
                    on_failure: abort
            """,
        )
        with pytest.raises(HookAbortError), patch("builtins.input", return_value="1"):
            run_flow(path, base_dir=tmp_path)

    def test_on_failure_warn_workflow_continues(self, tmp_path: Path) -> None:
        """A hook with on_failure: warn that exits non-zero must log a warning and
        allow the workflow to continue normally to completion."""
        path = _write_flow(
            tmp_path / "flow.yaml",
            state_hooks_yaml="""\
                on_wait_start:
                  - command: "exit 1"
                    on_failure: warn
            """,
        )
        with (
            patch("fdsx.core.compiler.nodes.execute_hooks", create=True) as mock_exec,
            patch("builtins.input", return_value="1"),
        ):
            result = run_flow(path, base_dir=tmp_path)

        assert result is not None, "run_flow must return a result when on_failure=warn"
        warn_calls = [
            c
            for c in mock_exec.call_args_list
            if c.kwargs.get("event") == "on_wait_start"
        ]
        assert len(warn_calls) == 1, (
            "on_wait_start hook must have been called even with on_failure=warn"
        )


# ─── T001: resume semantics ────────────────────────────────────────────────────


class TestWaitHookResumeSemantics:
    """on_wait_start and on_wait_end fire exactly the right number of times across
    an initial run + checkpoint resume."""

    def _write_resumable_flow(self, tmp_path: Path) -> Path:
        return _write_flow(
            tmp_path / "flow.yaml",
            state_hooks_yaml="""\
                on_wait_start:
                  - command: "echo start"
                on_wait_end:
                  - command: "echo end"
            """,
        )

    def test_on_wait_start_does_not_refire_on_resume(self, tmp_path: Path) -> None:
        """on_wait_start fires exactly once — before the first suspension —
        and must NOT fire again when the workflow is resumed from a checkpoint."""
        path = self._write_resumable_flow(tmp_path)
        thread_id = "test-wait-hooks-no-refire"
        start_fire_count = 0

        def _count_start(hooks, *, event, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal start_fire_count
            if event == "on_wait_start":
                start_fire_count += 1

        with patch(
            "fdsx.core.compiler.nodes.execute_hooks",
            create=True,
            side_effect=_count_start,
        ):
            with (
                pytest.raises(RuntimeError),
                patch(
                    "fdsx.core.engine.interrupts.display_wait_prompt",
                    side_effect=RuntimeError("simulated interrupt for checkpoint"),
                ),
            ):
                run_flow(path, base_dir=tmp_path, thread_id=thread_id)

            count_after_first_run = start_fire_count

            with patch("builtins.input", return_value="1"):
                resume_flow(thread_id, tmp_path, path)

        assert count_after_first_run == 1, (
            "on_wait_start must fire exactly once before the first suspension"
        )
        assert start_fire_count == 1, (
            "on_wait_start must NOT re-fire on checkpoint resume"
        )

    def test_on_wait_end_fires_exactly_once_on_resume(self, tmp_path: Path) -> None:
        """on_wait_end fires exactly once — after user_selection is written on the
        resumed run — and must not fire during the initial suspended run."""
        path = self._write_resumable_flow(tmp_path)
        thread_id = "test-wait-hooks-end-once"
        end_fire_count = 0

        def _count_end(hooks, *, event, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal end_fire_count
            if event == "on_wait_end":
                end_fire_count += 1

        with patch(
            "fdsx.core.compiler.nodes.execute_hooks",
            create=True,
            side_effect=_count_end,
        ):
            with (
                pytest.raises(RuntimeError),
                patch(
                    "fdsx.core.engine.interrupts.display_wait_prompt",
                    side_effect=RuntimeError("simulated interrupt for checkpoint"),
                ),
            ):
                run_flow(path, base_dir=tmp_path, thread_id=thread_id)

            count_after_first_run = end_fire_count

            with patch("builtins.input", return_value="1"):
                resume_flow(thread_id, tmp_path, path)

        assert count_after_first_run == 0, (
            "on_wait_end must not fire during the initial suspended run"
        )
        assert end_fire_count == 1, (
            "on_wait_end must fire exactly once after user provides selection on resume"
        )


# ─── T002: multi-level configuration ──────────────────────────────────────────


class TestWaitHookMultiLevelConfig:
    """on_wait_start / on_wait_end hooks declared at flow level fire for all wait states,
    and state-level declarations are additive (not replacing) higher-level hooks."""

    def test_flow_level_on_wait_start_fires_for_all_wait_states(
        self, tmp_path: Path
    ) -> None:
        """on_wait_start declared in flow.hooks (not per-state) must fire for every
        wait state in the flow, even when the wait state has no per-state hooks."""
        path = _write_two_wait_flow(
            tmp_path / "flow.yaml",
            flow_hooks_yaml="""\
                on_wait_start:
                  - command: "echo flow-level start"
            """,
        )
        with (
            patch("fdsx.core.compiler.nodes.execute_hooks", create=True) as mock_exec,
            patch(
                "fdsx.core.compiler.nodes.write_hook_data",
                return_value=Path("/dev/null"),
            ),
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        start_calls = [
            c
            for c in mock_exec.call_args_list
            if c.kwargs.get("event") == "on_wait_start"
        ]
        assert len(start_calls) == 2, (
            "flow-level on_wait_start must fire once per wait state "
            f"(2 wait states); got {len(start_calls)} calls"
        )

    def test_flow_level_on_wait_end_fires_for_all_wait_states(
        self, tmp_path: Path
    ) -> None:
        """on_wait_end declared in flow.hooks must fire for every wait state in the flow."""
        path = _write_two_wait_flow(
            tmp_path / "flow.yaml",
            flow_hooks_yaml="""\
                on_wait_end:
                  - command: "echo flow-level end"
            """,
        )
        with (
            patch("fdsx.core.compiler.nodes.execute_hooks", create=True) as mock_exec,
            patch(
                "fdsx.core.compiler.nodes.write_hook_data",
                return_value=Path("/dev/null"),
            ),
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        end_calls = [
            c
            for c in mock_exec.call_args_list
            if c.kwargs.get("event") == "on_wait_end"
        ]
        assert len(end_calls) == 2, (
            "flow-level on_wait_end must fire once per wait state "
            f"(2 wait states); got {len(end_calls)} calls"
        )

    def test_state_level_hooks_append_to_flow_level_hooks(self, tmp_path: Path) -> None:
        """When on_wait_start is declared at both flow level and state level, both
        hook entries must run (additive merge, not replacement)."""
        path = _write_flow(
            tmp_path / "flow.yaml",
            flow_hooks_yaml="""\
                on_wait_start:
                  - command: "echo flow-level"
            """,
            state_hooks_yaml="""\
                on_wait_start:
                  - command: "echo state-level"
            """,
        )

        executed_commands: list[str] = []

        def _capture(hooks, *, event, **kwargs):  # type: ignore[no-untyped-def]
            if event == "on_wait_start":
                for h in hooks:
                    executed_commands.append(getattr(h, "command", str(h)))

        with (
            patch(
                "fdsx.core.compiler.nodes.execute_hooks",
                create=True,
                side_effect=_capture,
            ),
            patch(
                "fdsx.core.compiler.nodes.write_hook_data",
                return_value=Path("/dev/null"),
            ),
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        assert len(executed_commands) >= 2, (
            "Both flow-level and state-level on_wait_start hooks must run "
            f"(additive merge); only {len(executed_commands)} hook(s) ran"
        )

    def test_global_config_on_wait_start_fires_for_wait_state(
        self, tmp_path: Path
    ) -> None:
        """on_wait_start declared in global FdsxConfig.hooks fires even when the
        flow and state have no per-level declarations."""
        path = _write_flow(tmp_path / "flow.yaml")
        global_config = FdsxConfig(
            hooks=HookConfig(on_wait_start=[HookEntry(command="echo global-start")])
        )
        with (
            patch("fdsx.core.engine.run.load_config", return_value=global_config),
            patch("fdsx.core.compiler.nodes.execute_hooks", create=True) as mock_exec,
            patch(
                "fdsx.core.compiler.nodes.write_hook_data",
                return_value=Path("/dev/null"),
            ),
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        start_calls = [
            c
            for c in mock_exec.call_args_list
            if c.kwargs.get("event") == "on_wait_start"
        ]
        assert len(start_calls) == 1, (
            f"global on_wait_start must fire once; got {len(start_calls)} calls"
        )

    def test_global_flow_state_merge_order(self, tmp_path: Path) -> None:
        """Hooks at all three levels run in global -> flow -> state order."""
        path = _write_flow(
            tmp_path / "flow.yaml",
            flow_hooks_yaml="""\
                on_wait_start:
                  - command: "echo flow-level"
            """,
            state_hooks_yaml="""\
                on_wait_start:
                  - command: "echo state-level"
            """,
        )
        global_config = FdsxConfig(
            hooks=HookConfig(on_wait_start=[HookEntry(command="echo global-level")])
        )

        executed_commands: list[str] = []

        def _capture(hooks, *, event, **kwargs):  # type: ignore[no-untyped-def]
            if event == "on_wait_start":
                for h in hooks:
                    executed_commands.append(getattr(h, "command", str(h)))

        with (
            patch("fdsx.core.engine.run.load_config", return_value=global_config),
            patch(
                "fdsx.core.compiler.nodes.execute_hooks",
                create=True,
                side_effect=_capture,
            ),
            patch(
                "fdsx.core.compiler.nodes.write_hook_data",
                return_value=Path("/dev/null"),
            ),
            patch("builtins.input", return_value="1"),
        ):
            run_flow(path, base_dir=tmp_path)

        assert executed_commands == [
            "echo global-level",
            "echo flow-level",
            "echo state-level",
        ], f"Expected global->flow->state order; got {executed_commands}"

"""Unit tests for _run_subprocess FDSX_HOOKS scrub (T002)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fdsx.providers.base import _run_subprocess


def _make_mock_process() -> MagicMock:
    """Return a minimal mock subprocess.Popen object."""
    proc = MagicMock()
    proc.stdout.readline.return_value = ""
    proc.stderr.readline.return_value = ""
    proc.returncode = 0
    proc.pid = 12345
    return proc


class TestRunSubprocessFdsxHooksScrub:
    """Tests documenting the FDSX_HOOKS scrub contract for _run_subprocess (T002).

    These tests are written against the post-T008 contract and are expected to
    fail until T008 implements the scrub in base.py.
    """

    def test_run_subprocess_strips_fdsx_hooks_from_inherited_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FDSX_HOOKS in os.environ must not be forwarded to the subprocess.

        When no env arg is supplied, _run_subprocess must still build an explicit
        env dict (not pass env=None) and strip FDSX_HOOKS from it.
        """
        monkeypatch.setenv("FDSX_HOOKS", "on_state_start")

        with patch("fdsx.providers.base.subprocess.Popen") as mock_popen:
            mock_popen.return_value = _make_mock_process()
            _run_subprocess(["echo", "hi"])

        captured_env = mock_popen.call_args[1]["env"]

        assert captured_env is not None, (
            "Expected an explicit env dict to be passed to Popen, got None"
        )
        assert "FDSX_HOOKS" not in captured_env, (
            "FDSX_HOOKS from os.environ must be stripped before passing to Popen"
        )

    def test_run_subprocess_strips_fdsx_hooks_when_env_arg_provided(self) -> None:
        """FDSX_HOOKS in caller-supplied env must be stripped from the subprocess env."""
        with patch("fdsx.providers.base.subprocess.Popen") as mock_popen:
            mock_popen.return_value = _make_mock_process()
            _run_subprocess(
                ["echo", "hi"],
                env={"FDSX_HOOKS": "on_state_start", "MY_VAR": "hello"},
            )

        captured_env = mock_popen.call_args[1]["env"]

        assert "FDSX_HOOKS" not in captured_env, (
            "FDSX_HOOKS supplied via env arg must be stripped before passing to Popen"
        )
        assert "MY_VAR" in captured_env, (
            "Other vars supplied via env arg must be preserved"
        )

    def test_run_subprocess_does_not_strip_other_fdsx_vars(self) -> None:
        """Only FDSX_HOOKS must be stripped; other FDSX_* vars must be preserved."""
        with patch("fdsx.providers.base.subprocess.Popen") as mock_popen:
            mock_popen.return_value = _make_mock_process()
            _run_subprocess(
                ["echo", "hi"],
                env={"FDSX_HOOKS": "on_state_end", "FDSX_STATE_NAME": "MyState"},
            )

        captured_env = mock_popen.call_args[1]["env"]

        assert "FDSX_HOOKS" not in captured_env, (
            "FDSX_HOOKS must be stripped from the subprocess env"
        )
        assert "FDSX_STATE_NAME" in captured_env, (
            "FDSX_STATE_NAME and other FDSX_* vars must not be stripped"
        )

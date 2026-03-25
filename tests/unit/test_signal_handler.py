"""Unit tests for fdsx.core.engine.signals.SignalHandler.

T036/T037: Covers context manager lifecycle, process registration,
signal forwarding, SIGKILL escalation, lock release, and exit behavior.
"""
import signal
import subprocess
import threading
from typing import Any
from unittest.mock import MagicMock, call, patch


from fdsx.core.engine.signals import (
    SignalHandler,
    _INTERRUPT_MESSAGE,
    _SIGNAL_EXIT_BASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_handler(
    checkpoint_manager: Any = None,
    thread_id: str = "test-thread",
) -> SignalHandler:
    """Build a SignalHandler with optional mock CheckpointManager."""
    return SignalHandler(checkpoint_manager, thread_id)


def _make_process(
    *,
    alive: bool = True,
    poll_returns: list[int | None] | None = None,
) -> MagicMock:
    """Create a mock subprocess.Popen.

    Args:
        alive: When True, the initial poll() returns None (process running).
               When False, poll() returns 0 (process already exited).
        poll_returns: Explicit sequence of values for poll() side_effect.
    """
    proc = MagicMock(spec=subprocess.Popen)
    if poll_returns is not None:
        proc.poll.side_effect = poll_returns
    else:
        proc.poll.return_value = None if alive else 0
    return proc


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    """SignalHandler installs/restores signal handlers on enter/exit."""

    def test_enter_installs_custom_handlers(self) -> None:
        """__enter__ replaces SIGINT and SIGTERM with _handle_signal."""
        handler = _make_handler()
        with patch("signal.signal") as mock_signal:
            mock_signal.return_value = signal.SIG_DFL
            with handler:
                calls = mock_signal.call_args_list
        assert calls[0] == call(signal.SIGINT, handler._handle_signal)
        assert calls[1] == call(signal.SIGTERM, handler._handle_signal)

    def test_exit_restores_previous_handlers(self) -> None:
        """__exit__ restores the handlers that were in place before __enter__."""
        handler = _make_handler()
        sentinel_int = MagicMock()
        sentinel_term = MagicMock()
        with patch("signal.signal") as mock_signal:
            # First two calls (enter) return the sentinels.
            mock_signal.side_effect = [sentinel_int, sentinel_term, None, None]
            with handler:
                pass
            restore_calls = mock_signal.call_args_list[2:]
        assert restore_calls[0] == call(signal.SIGINT, sentinel_int)
        assert restore_calls[1] == call(signal.SIGTERM, sentinel_term)

    def test_returns_self_on_enter(self) -> None:
        """__enter__ returns the handler itself for ``as`` binding."""
        handler = _make_handler()
        with patch("signal.signal", return_value=signal.SIG_DFL):
            result = handler.__enter__()
        assert result is handler


# ---------------------------------------------------------------------------
# register_process
# ---------------------------------------------------------------------------


class TestRegisterProcess:
    """register_process adds processes to the active set thread-safely."""

    def test_register_single_process(self) -> None:
        """Registered process appears in the active set."""
        handler = _make_handler()
        proc = _make_process()
        handler.register_process(proc)
        assert proc in handler._active_processes

    def test_register_multiple_processes(self) -> None:
        """All registered processes appear in the active set."""
        handler = _make_handler()
        procs = [_make_process() for _ in range(3)]
        for proc in procs:
            handler.register_process(proc)
        assert handler._active_processes == set(procs)

    def test_register_is_thread_safe(self) -> None:
        """Concurrent registrations from multiple threads do not lose entries."""
        handler = _make_handler()
        procs = [_make_process() for _ in range(50)]
        threads = [
            threading.Thread(target=handler.register_process, args=(proc,))
            for proc in procs
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(handler._active_processes) == 50


# ---------------------------------------------------------------------------
# _handle_signal — forwarding
# ---------------------------------------------------------------------------


class TestHandleSignalForwarding:
    """Signal is forwarded to all active processes."""

    def test_forwards_signal_to_alive_processes(self) -> None:
        """send_signal is called on each alive process."""
        handler = _make_handler()
        procs = [_make_process(alive=True) for _ in range(2)]
        for proc in procs:
            handler.register_process(proc)

        with (
            patch("time.monotonic", return_value=0.0),
            patch("sys.exit"),
        ):
            # Make wait() return quickly so the test doesn't stall.
            for proc in procs:
                proc.wait.return_value = None

            handler._handle_signal(signal.SIGINT, None)

        for proc in procs:
            proc.send_signal.assert_called_once_with(signal.SIGINT)

    def test_skips_send_signal_for_dead_processes(self) -> None:
        """send_signal is NOT called on already-exited processes."""
        handler = _make_handler()
        dead_proc = _make_process(alive=False)
        handler.register_process(dead_proc)

        with patch("sys.exit"):
            handler._handle_signal(signal.SIGTERM, None)

        dead_proc.send_signal.assert_not_called()

    def test_ignores_oserror_from_send_signal(self) -> None:
        """OSError during send_signal does not abort the handler."""
        handler = _make_handler()
        proc = _make_process(alive=True)
        proc.send_signal.side_effect = OSError("already dead")
        handler.register_process(proc)

        with patch("sys.exit"):
            # Should not raise.
            handler._handle_signal(signal.SIGINT, None)


# ---------------------------------------------------------------------------
# _handle_signal — SIGKILL escalation
# ---------------------------------------------------------------------------


class TestHandleSignalSigkill:
    """Processes that survive the grace period are SIGKILLed."""

    def test_sigkill_if_process_survives_grace_period(self) -> None:
        """kill() is called when process still alive after wait timeout."""
        handler = _make_handler()
        proc = _make_process()
        # poll() always returns None (never exits on its own).
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=5)
        handler.register_process(proc)

        with patch("sys.exit"):
            handler._handle_signal(signal.SIGINT, None)

        proc.kill.assert_called_once()

    def test_no_sigkill_if_process_exits_voluntarily(self) -> None:
        """kill() is NOT called when process exits within the grace period."""
        handler = _make_handler()
        proc = _make_process(alive=True)
        # After wait() the process has exited; subsequent poll() returns 0.
        proc.wait.return_value = None
        proc.poll.side_effect = [None, None, 0]  # alive for forwarding, dead for kill check
        handler.register_process(proc)

        with patch("sys.exit"):
            handler._handle_signal(signal.SIGINT, None)

        proc.kill.assert_not_called()

    def test_ignores_oserror_from_kill(self) -> None:
        """OSError during kill() does not abort the handler."""
        handler = _make_handler()
        proc = _make_process()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="sleep", timeout=5)
        proc.kill.side_effect = OSError("already dead")
        handler.register_process(proc)

        with patch("sys.exit"):
            # Should not raise.
            handler._handle_signal(signal.SIGTERM, None)


# ---------------------------------------------------------------------------
# _handle_signal — lock release
# ---------------------------------------------------------------------------


class TestHandleSignalLockRelease:
    """Checkpoint lock is released when a CheckpointManager is provided."""

    def test_releases_lock_when_checkpoint_manager_provided(self) -> None:
        """release_lock is called with the correct thread_id."""
        cm = MagicMock()
        handler = _make_handler(checkpoint_manager=cm, thread_id="my-thread")

        with patch("sys.exit"):
            handler._handle_signal(signal.SIGINT, None)

        cm.release_lock.assert_called_once_with("my-thread")

    def test_skips_lock_release_when_no_checkpoint_manager(self) -> None:
        """No AttributeError when checkpoint_manager is None."""
        handler = _make_handler(checkpoint_manager=None)

        with patch("sys.exit"):
            # Should not raise.
            handler._handle_signal(signal.SIGINT, None)

    def test_continues_after_lock_release_exception(self) -> None:
        """Exception in release_lock does not prevent exit."""
        cm = MagicMock()
        cm.release_lock.side_effect = RuntimeError("lock already released")
        handler = _make_handler(checkpoint_manager=cm)

        with patch("sys.exit") as mock_exit:
            handler._handle_signal(signal.SIGINT, None)

        mock_exit.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_signal — stderr message and exit code
# ---------------------------------------------------------------------------


class TestHandleSignalExit:
    """Correct message printed and exit code used."""

    def test_prints_interrupt_message_to_stderr(self, capsys: Any) -> None:
        """'Workflow interrupted' is printed to stderr."""
        handler = _make_handler()

        with patch("sys.exit"):
            handler._handle_signal(signal.SIGINT, None)

        captured = capsys.readouterr()
        assert _INTERRUPT_MESSAGE.strip() in captured.err

    def test_exits_with_128_plus_signum_for_sigint(self) -> None:
        """sys.exit is called with 130 (128+2) for SIGINT."""
        handler = _make_handler()

        with patch("sys.exit") as mock_exit:
            handler._handle_signal(signal.SIGINT, None)

        mock_exit.assert_called_once_with(_SIGNAL_EXIT_BASE + signal.SIGINT)

    def test_exits_with_128_plus_signum_for_sigterm(self) -> None:
        """sys.exit is called with 143 (128+15) for SIGTERM."""
        handler = _make_handler()

        with patch("sys.exit") as mock_exit:
            handler._handle_signal(signal.SIGTERM, None)

        mock_exit.assert_called_once_with(_SIGNAL_EXIT_BASE + signal.SIGTERM)

    def test_exit_called_even_with_no_active_processes(self) -> None:
        """sys.exit is called even when no processes were registered."""
        handler = _make_handler()

        with patch("sys.exit") as mock_exit:
            handler._handle_signal(signal.SIGINT, None)

        mock_exit.assert_called_once_with(_SIGNAL_EXIT_BASE + signal.SIGINT)

"""TDD anchor tests for max_suspend_duration parameter on _run_subprocess (T002).

These four tests define the behavioral contracts for max_suspend_duration:
1. None/0 leaves existing behavior byte-identical.
2. Calling suspend_fn() without resume_fn auto-resumes after max_suspend_duration seconds.
3. Calling resume_fn() before the cap fires cancels the auto-resume.
4. A second call to suspend_fn() resets the cap deadline.
"""

import sys
import threading
import time
from collections.abc import Callable

from fdsx.providers.base import ProviderResult, _run_subprocess

_PYTHON = sys.executable


class TestMaxSuspendDurationDefault:
    """max_suspend_duration=None leaves existing behavior byte-identical."""

    def test_default_none_behavior_unchanged(self):
        """echo hello with and without max_suspend_duration=None returns identical results."""
        result_without = _run_subprocess(args=["echo", "hello"])
        result_with = _run_subprocess(
            args=["echo", "hello"],
            max_suspend_duration=None,
        )

        assert result_without.exit_code == result_with.exit_code
        assert result_without.stdout == result_with.stdout
        assert result_without.stderr == result_with.stderr


class TestMaxSuspendDurationAutoResume:
    """Suspend without a matching resume call auto-resumes after max_suspend_duration."""

    def test_suspend_without_resume_auto_resumes(self):
        """Suspending without calling resume_fn auto-resumes after max_suspend_duration seconds.

        Setup:
          - inactivity_timeout=3, max_suspend_duration=2
          - suspend_fn() is called at ~0.3 s
          - resume_fn is never called

        Expected: the watchdog auto-resumes at ~2 s after suspend, then the inactivity
        timer re-fires at ~3 s after the auto-resume. Total wall-clock < 3+2+4 = 9 s.
        """
        suspend_holder: list[Callable[[], None] | None] = [None]
        resume_holder: list[Callable[[], None] | None] = [None]

        def capture_hooks(s: Callable[[], None], r: Callable[[], None]) -> None:
            suspend_holder[0] = s
            resume_holder[0] = r

        result_holder: list[ProviderResult | None] = [None]
        done = threading.Event()

        def run() -> None:
            result_holder[0] = _run_subprocess(
                args=[_PYTHON, "-c", "import time; time.sleep(30)"],
                inactivity_timeout=3,
                max_suspend_duration=2,
                on_inactivity_hooks=capture_hooks,
            )
            done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()

        # Wait for hooks to be registered
        deadline = time.monotonic() + 3
        while suspend_holder[0] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert suspend_holder[0] is not None, "suspend hook was not registered in time"

        # Suspend at ~0.3 s; do NOT call resume_fn
        time.sleep(0.3)
        suspend_holder[0]()

        # Should auto-resume at ~2 s after suspend and be killed ~3 s later
        # Total budget: 0.3 + 2 + 3 + 4 (slack) = 9.3 s
        assert done.wait(timeout=10), (
            "Process was not killed within the expected window"
        )
        assert result_holder[0] is not None
        assert result_holder[0].exit_code == 124


class TestMaxSuspendDurationManualResume:
    """Calling resume_fn before the cap fires cancels the auto-resume."""

    def test_resume_before_cap_cancels_auto_resume(self):
        """Manual resume before the cap prevents the auto-resume from triggering early.

        Setup:
          - inactivity_timeout=3, max_suspend_duration=5
          - suspend_fn() at ~0.2 s, resume_fn() at ~0.5 s

        Expected: the process is eventually killed by the normal inactivity watchdog
        (after resume, the idle clock runs from ~0.5 s and fires at ~3.5 s).
        Total wall-clock < 0.5 + 3 + 4 = 7.5 s.
        The auto-resume cap of 5 s was NOT the trigger.
        """
        suspend_holder: list[Callable[[], None] | None] = [None]
        resume_holder: list[Callable[[], None] | None] = [None]

        def capture_hooks(s: Callable[[], None], r: Callable[[], None]) -> None:
            suspend_holder[0] = s
            resume_holder[0] = r

        result_holder: list[ProviderResult | None] = [None]
        done = threading.Event()

        def run() -> None:
            result_holder[0] = _run_subprocess(
                args=[_PYTHON, "-c", "import time; time.sleep(30)"],
                inactivity_timeout=3,
                max_suspend_duration=5,
                on_inactivity_hooks=capture_hooks,
            )
            done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()

        # Wait for hooks to be registered
        deadline = time.monotonic() + 3
        while suspend_holder[0] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert suspend_holder[0] is not None, "suspend hook was not registered in time"
        assert resume_holder[0] is not None, "resume hook was not registered in time"

        time.sleep(0.2)
        suspend_holder[0]()
        time.sleep(0.3)
        resume_holder[0]()

        # After manual resume, inactivity watchdog fires at ~3 s from resume.
        # Total budget: 0.5 + 3 + 4 = 7.5 s
        assert done.wait(timeout=8), "Process was not killed within the expected window"
        assert result_holder[0] is not None
        assert result_holder[0].exit_code == 124


class TestMaxSuspendDurationCapReset:
    """A second suspend call resets the cap deadline."""

    def test_suspend_repeatedly_resets_cap(self):
        """A second suspend_fn() call resets the auto-resume deadline.

        Setup:
          - inactivity_timeout=4, max_suspend_duration=2
          - First suspend_fn() at ~0.2 s (cap fires at ~2.2 s)
          - Second suspend_fn() at ~0.9 s (cap resets to fire at ~2.9 s)
          - At 1.5 s from first suspend (absolute t≈1.7 s), process must still be alive
            (second cap has not fired yet)
          - Eventually killed at ~2.9 + 4 = ~6.9 s
        """
        suspend_holder: list[Callable[[], None] | None] = [None]

        def capture_hooks(s: Callable[[], None], r: Callable[[], None]) -> None:
            suspend_holder[0] = s

        result_holder: list[ProviderResult | None] = [None]
        done = threading.Event()

        def run() -> None:
            result_holder[0] = _run_subprocess(
                args=[_PYTHON, "-c", "import time; time.sleep(30)"],
                inactivity_timeout=4,
                max_suspend_duration=2,
                on_inactivity_hooks=capture_hooks,
            )
            done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()

        # Wait for hooks to be registered
        deadline = time.monotonic() + 3
        while suspend_holder[0] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert suspend_holder[0] is not None, "suspend hook was not registered in time"

        first_suspend_at = time.monotonic()
        time.sleep(0.2)
        suspend_holder[0]()  # first suspend; cap would fire at +2 s = 0.2+2=2.2s abs

        time.sleep(0.7)
        suspend_holder[
            0
        ]()  # second suspend at ~0.9 s abs; cap resets to 0.9+2=2.9 s abs

        # At 1.5 s from first suspend (abs ~1.7 s), process should still be alive
        check_at = (
            first_suspend_at + 0.2 + 1.5
        )  # 0.2s sleep before first suspend + 1.5s
        remaining = check_at - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

        assert not done.is_set(), (
            "Process was killed too early — second suspend did not reset the cap"
        )

        # Eventually killed: second cap at ~2.9 s + inactivity 4 s + slack
        assert done.wait(timeout=12), (
            "Process was not killed within the expected window"
        )
        assert result_holder[0] is not None
        assert result_holder[0].exit_code == 124

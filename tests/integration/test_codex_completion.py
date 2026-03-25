"""Integration tests for Codex provider completion_event cascade (Phase 3: T004).

These tests verify the full end-to-end flow:
  CodexProvider._make_stream_callback sets completion_event on terminal JSONL events
  → _run_subprocess receives the event and initiates the termination cascade
  → A hanging subprocess (still sleeping after terminal event) is killed within ~15s
  → All output emitted before the hang is preserved in the result

All tests use real subprocesses via sys.executable to ensure realistic behavior.

Test criteria (T006): python -m pytest tests/integration/test_codex_completion.py -v
"""

import json
import sys
import threading
import time

from fdsx.providers.base import _run_subprocess
from fdsx.providers.codex import (
    CodexProvider,
    _EVENT_ITEM_COMPLETED,
    _ITEM_TYPE_AGENT_MESSAGE,
)

# Use the same Python interpreter as the test runner.
_PYTHON = sys.executable

# Upper bound on how long each test may take:
# completion_event fires immediately after terminal event is read,
# then cascade: 5s voluntary wait + SIGTERM + 5s + SIGKILL.
_TEST_TIMEOUT = 15


class TestCodexHangingProcessKilledByCompletionEvent:
    """A process that emits a terminal Codex JSONL event then hangs is killed."""

    def test_codex_hanging_process_killed_by_completion_event(self) -> None:
        """subprocess emits JSONL terminal event then hangs; termination cascade
        kills it within _TEST_TIMEOUT and output is preserved."""
        terminal_line = json.dumps(
            {
                "type": _EVENT_ITEM_COMPLETED,
                "item": {
                    "id": "item_001",
                    "type": _ITEM_TYPE_AGENT_MESSAGE,
                    "text": "Hello world",
                },
            }
        )
        script = (
            f"import sys, time\n"
            f"print({terminal_line!r}, flush=True)\n"
            f"time.sleep(999)\n"
        )

        provider = CodexProvider()
        output_lines: list[str] = []
        completion_event = threading.Event()
        stream_callback, get_result = provider._make_stream_callback(
            output_lines.append, completion_event
        )

        start = time.time()
        _run_subprocess(
            args=[_PYTHON, "-c", script],
            output_callback=stream_callback,
            completion_event=completion_event,
        )
        elapsed = time.time() - start

        assert elapsed < _TEST_TIMEOUT, (
            f"Test took {elapsed:.1f}s — exceeds {_TEST_TIMEOUT}s limit"
        )
        assert output_lines == ["Hello world"], (
            f"Expected ['Hello world'], got {output_lines!r}"
        )
        assert get_result() == "Hello world", (
            f"Expected 'Hello world' from get_result(), got {get_result()!r}"
        )

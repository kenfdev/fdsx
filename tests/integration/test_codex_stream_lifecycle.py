"""Integration tests for Codex streaming lifecycle behavior.

These tests verify that streamed ``agent_message`` events are treated as partial
content, not as a signal to terminate the underlying Codex process early.
"""

import json
from unittest.mock import MagicMock, patch

from fdsx.providers.base import ProviderResult
from fdsx.providers.codex import (
    CodexProvider,
    _EVENT_ITEM_COMPLETED,
    _EVENT_ITEM_STARTED,
    _ITEM_TYPE_AGENT_MESSAGE,
    _ITEM_TYPE_REASONING,
)


def _fake_run_subprocess_factory(lines: list[str]) -> MagicMock:
    """Replay JSONL lines through the provided output callback."""

    def fake_run_subprocess(**kwargs: object) -> ProviderResult:
        output_callback = kwargs.get("output_callback")
        if output_callback is not None:
            for line in lines:
                output_callback(line)  # type: ignore[operator]
        return ProviderResult(exit_code=0, stdout="<raw jsonl>", stderr="")

    return MagicMock(side_effect=fake_run_subprocess)


class TestCodexStreamLifecycle:
    """Codex streaming stays alive after partial message events."""

    def setup_method(self) -> None:
        self.provider = CodexProvider()

    def test_execute_does_not_pass_completion_event(self) -> None:
        """Streaming Codex execution must not ask _run_subprocess to terminate early."""
        mock_run = _fake_run_subprocess_factory([])

        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            self.provider.execute("review this diff", output_callback=lambda _: None)

        assert mock_run.call_args.kwargs.get("completion_event") is None

    def test_stream_continues_after_agent_message_completed(self) -> None:
        """Later events after an agent message are still processed and final stdout is preserved."""
        lines = [
            json.dumps(
                {
                    "type": _EVENT_ITEM_COMPLETED,
                    "item": {
                        "id": "item_001",
                        "type": _ITEM_TYPE_AGENT_MESSAGE,
                        "text": "First pass",
                    },
                }
            ),
            json.dumps(
                {
                    "type": _EVENT_ITEM_STARTED,
                    "item": {"id": "item_002", "type": _ITEM_TYPE_REASONING},
                }
            ),
            json.dumps(
                {
                    "type": _EVENT_ITEM_COMPLETED,
                    "item": {
                        "id": "item_002",
                        "type": _ITEM_TYPE_REASONING,
                        "text": "Need one more pass",
                    },
                }
            ),
            json.dumps(
                {
                    "type": _EVENT_ITEM_COMPLETED,
                    "item": {
                        "id": "item_003",
                        "type": _ITEM_TYPE_AGENT_MESSAGE,
                        "text": "Final answer",
                    },
                }
            ),
        ]
        received: list[str] = []
        mock_run = _fake_run_subprocess_factory(lines)

        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            result = self.provider.execute(
                "review this diff", output_callback=received.append
            )

        assert received == [
            "First pass",
            "[thinking] Need one more pass",
            "Final answer",
        ]
        assert result.stdout == "First pass\nFinal answer"

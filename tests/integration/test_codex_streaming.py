"""Integration test for Codex provider streaming end-to-end.

Uses a mocked subprocess that replays the ``tests/fixtures/codex_stream.jsonl``
fixture to verify:
- output_callback is called with human-readable content fragments
- ProviderResult.stdout matches concatenated agent_message texts (not raw JSONL)
- No raw JSON leaks through to the callback
- Streaming CLI flag (--json) is added to subprocess args when output_callback provided
- Without output_callback, --json flag is absent
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fdsx.providers.base import ProviderResult
from fdsx.providers.codex import _STREAM_FORMAT_FLAGS, CodexProvider

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "codex_stream.jsonl"

# Expected values derived from the fixture file (two agent_message events joined with "\n")
FIXTURE_RESULT_TEXT = "Hello! \nHere are 3 items:\n\n1. Apple\n2. Banana\n3. Cherry"
FIXTURE_AGENT_MESSAGE_TEXTS = [
    "Hello! ",
    "Here are 3 items:\n\n1. Apple\n2. Banana\n3. Cherry",
]


def _load_fixture_lines() -> list[str]:
    """Return non-empty JSONL lines from the fixture file."""
    return [
        line.rstrip("\n")
        for line in FIXTURE_PATH.read_text().splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_run_subprocess_factory(
    fixture_lines: list[str], exit_code: int = 0
) -> MagicMock:
    """Return a mock for _run_subprocess that replays fixture lines via output_callback."""

    def fake_run_subprocess(**kwargs: object) -> ProviderResult:
        output_callback = kwargs.get("output_callback")
        if output_callback is not None:
            for line in fixture_lines:
                output_callback(line)  # type: ignore[operator]
        return ProviderResult(exit_code=exit_code, stdout="<raw jsonl>", stderr="")

    mock = MagicMock(side_effect=fake_run_subprocess)
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCodexStreamingEndToEnd:
    """End-to-end streaming test using the codex_stream.jsonl fixture."""

    def setup_method(self) -> None:
        self.fixture_lines = _load_fixture_lines()
        self.provider = CodexProvider()

    def test_fixture_file_exists(self) -> None:
        """Sanity check: the fixture file must exist and be non-empty."""
        assert FIXTURE_PATH.exists(), f"Fixture not found: {FIXTURE_PATH}"
        assert len(self.fixture_lines) > 0

    def test_stdout_comes_from_agent_message_concatenation(self) -> None:
        """ProviderResult.stdout must match concatenated agent_message texts, not raw JSONL."""
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            result = self.provider.execute(
                "Say hello and list 3 items", output_callback=lambda _: None
            )

        assert result.stdout == FIXTURE_RESULT_TEXT

    def test_output_callback_receives_agent_message_texts(self) -> None:
        """output_callback must be called with each agent_message text."""
        received: list[str] = []
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            self.provider.execute(
                "Say hello and list 3 items", output_callback=received.append
            )

        for text in FIXTURE_AGENT_MESSAGE_TEXTS:
            assert text in received, (
                f"Expected agent_message text not received: {text!r}"
            )

    def test_output_callback_receives_tool_events(self) -> None:
        """output_callback must be called for command_execution, file_change, mcp_tool_call."""
        received: list[str] = []
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            self.provider.execute(
                "Say hello and list 3 items", output_callback=received.append
            )

        assert "[tool: echo hello]" in received
        assert "[tool: file_change]" in received
        assert "[tool: web_search]" in received

    def test_output_callback_receives_thinking(self) -> None:
        """output_callback must be called with [thinking] prefix for reasoning items."""
        received: list[str] = []
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            self.provider.execute(
                "Say hello and list 3 items", output_callback=received.append
            )

        assert "[thinking] I need to say hello in a friendly way." in received

    def test_no_raw_json_leaks_to_callback(self) -> None:
        """output_callback must not receive any raw JSON JSONL lines."""
        received: list[str] = []
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            self.provider.execute(
                "Say hello and list 3 items", output_callback=received.append
            )

        for item in received:
            assert not item.strip().startswith("{"), (
                f"Raw JSON leaked to output_callback: {item!r}"
            )

    def test_streaming_flag_added_to_args(self) -> None:
        """_run_subprocess must be called with all _STREAM_FORMAT_FLAGS when output_callback is provided."""
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            self.provider.execute(
                "Say hello and list 3 items", output_callback=lambda _: None
            )

        mock_run.assert_called_once()
        called_args: list[str] = mock_run.call_args.kwargs["args"]
        for flag in _STREAM_FORMAT_FLAGS:
            assert flag in called_args, f"Expected {flag!r} in args: {called_args}"

    def test_no_streaming_flag_without_callback(self) -> None:
        """Without output_callback, _run_subprocess must NOT receive any _STREAM_FORMAT_FLAGS."""
        fake_result = ProviderResult(exit_code=0, stdout="plain response", stderr="")
        with patch(
            "fdsx.providers.codex._run_subprocess", return_value=fake_result
        ) as mock_run:
            result = self.provider.execute("Say hello")

        mock_run.assert_called_once()
        called_args: list[str] = mock_run.call_args.kwargs["args"]
        for flag in _STREAM_FORMAT_FLAGS:
            assert flag not in called_args, (
                f"Unexpected {flag!r} found in args: {called_args}"
            )
        assert result.stdout == "plain response"

    def test_exit_code_preserved(self) -> None:
        """ProviderResult.exit_code must be propagated from _run_subprocess."""
        mock_run = _fake_run_subprocess_factory(self.fixture_lines, exit_code=0)
        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            result = self.provider.execute("Say hello", output_callback=lambda _: None)

        assert result.exit_code == 0

    def test_malformed_lines_do_not_crash(self) -> None:
        """Malformed JSONL lines interspersed in the stream do not raise exceptions."""
        # Use last fixture line (a completed agent_message) plus malformed lines
        last_line = self.fixture_lines[-1]
        mixed_lines = ["not valid json", last_line, "{{broken"]
        mock_run = _fake_run_subprocess_factory(mixed_lines)
        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            result = self.provider.execute("Say hello", output_callback=lambda _: None)

        # Last fixture line is the second agent_message completed event
        assert result.stdout == "Here are 3 items:\n\n1. Apple\n2. Banana\n3. Cherry"
        assert result.exit_code == 0

    def test_fallback_stdout_when_no_agent_message(self) -> None:
        """When no agent_message completed events, stdout comes from raw _run_subprocess result."""
        # Feed only non-agent_message lines
        non_message_lines = [
            line for line in self.fixture_lines if '"agent_message"' not in line
        ]
        mock_run = _fake_run_subprocess_factory(non_message_lines)
        with patch("fdsx.providers.codex._run_subprocess", mock_run):
            result = self.provider.execute("Say hello", output_callback=lambda _: None)

        # No agent_message events → get_result() returns None → fall back to raw result
        assert result.stdout == "<raw jsonl>"

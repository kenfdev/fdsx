"""Integration test for Claude provider streaming end-to-end.

Uses a mocked subprocess that replays the ``tests/fixtures/claude_stream.ndjson``
fixture to verify:
- output_callback is called with human-readable text fragments
- ProviderResult.stdout matches the ``result`` event text (not raw NDJSON)
- No raw JSON leaks through to the callback
- Streaming CLI flags are added to the subprocess args when output_callback is provided
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fdsx.providers.base import ProviderResult
from fdsx.providers.claude import _STREAM_FORMAT_FLAGS, ClaudeProvider

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "claude_stream.ndjson"

# Expected values derived from the fixture file
FIXTURE_RESULT_TEXT = "Hello! Here are 3 items:\n\n1. Apple\n2. Banana\n3. Cherry"
FIXTURE_TEXT_DELTAS = [
    "Hello",
    "! Here are 3 items:\n\n",
    "1. Apple\n2. Banana\n3. Cherry",
]
# After line buffering, fragments are combined and split at newlines
FIXTURE_BUFFERED_LINES = [
    "Hello! Here are 3 items:",
    "1. Apple",
    "2. Banana",
    "3. Cherry",
]


def _load_fixture_lines() -> list[str]:
    """Return non-empty NDJSON lines from the fixture file."""
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
        return ProviderResult(exit_code=exit_code, stdout="<raw ndjson>", stderr="")

    mock = MagicMock(side_effect=fake_run_subprocess)
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestClaudeStreamingEndToEnd:
    """End-to-end streaming test using the claude_stream.ndjson fixture."""

    def setup_method(self) -> None:
        self.fixture_lines = _load_fixture_lines()
        self.provider = ClaudeProvider()

    def test_fixture_file_exists(self) -> None:
        """Sanity check: the fixture file must exist and be non-empty."""
        assert FIXTURE_PATH.exists(), f"Fixture not found: {FIXTURE_PATH}"
        assert len(self.fixture_lines) > 0

    def test_stdout_comes_from_result_event(self) -> None:
        """ProviderResult.stdout must match the result event text, not raw NDJSON."""
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            result = self.provider.execute(
                "Say hello and list 3 items", output_callback=lambda _: None
            )

        assert result.stdout == FIXTURE_RESULT_TEXT

    def test_output_callback_receives_text_lines(self) -> None:
        """output_callback must be called with buffered, line-delimited text."""
        received: list[str] = []
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            self.provider.execute(
                "Say hello and list 3 items", output_callback=received.append
            )

        # Fragments are buffered and emitted as complete lines
        assert received == FIXTURE_BUFFERED_LINES

    def test_no_raw_json_leaks_to_callback(self) -> None:
        """output_callback must not receive any raw JSON NDJSON lines."""
        received: list[str] = []
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            self.provider.execute(
                "Say hello and list 3 items", output_callback=received.append
            )

        for item in received:
            assert not item.strip().startswith("{"), (
                f"Raw JSON leaked to output_callback: {item!r}"
            )

    def test_streaming_flags_added_to_args(self) -> None:
        """_run_subprocess must be called with stream-json CLI flags."""
        mock_run = _fake_run_subprocess_factory(self.fixture_lines)
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            self.provider.execute(
                "Say hello and list 3 items", output_callback=lambda _: None
            )

        mock_run.assert_called_once()
        called_args: list[str] = mock_run.call_args.kwargs["args"]
        for flag in _STREAM_FORMAT_FLAGS:
            assert flag in called_args, f"Expected streaming flag not in args: {flag!r}"

    def test_no_streaming_flags_without_callback(self) -> None:
        """Without output_callback, _run_subprocess must NOT receive stream-json flags."""
        fake_result = ProviderResult(exit_code=0, stdout="plain response", stderr="")
        with patch(
            "fdsx.providers.claude._run_subprocess", return_value=fake_result
        ) as mock_run:
            result = self.provider.execute("Say hello")

        mock_run.assert_called_once()
        called_args: list[str] = mock_run.call_args.kwargs["args"]
        for flag in _STREAM_FORMAT_FLAGS:
            assert flag not in called_args, (
                f"Unexpected streaming flag found in args: {flag!r}"
            )
        assert result.stdout == "plain response"

    def test_exit_code_preserved(self) -> None:
        """ProviderResult.exit_code must be propagated from _run_subprocess."""
        mock_run = _fake_run_subprocess_factory(self.fixture_lines, exit_code=0)
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            result = self.provider.execute("Say hello", output_callback=lambda _: None)

        assert result.exit_code == 0

    def test_fallback_stdout_when_result_event_absent(self) -> None:
        """When no result event in stream, stdout falls back to concatenated text_delta."""
        # Only feed text_delta lines (no result event)
        delta_only_lines = [
            line
            for line in self.fixture_lines
            if '"type":"content_block_delta"' in line
            or '"type": "content_block_delta"' in line
        ]
        mock_run = _fake_run_subprocess_factory(delta_only_lines)
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            result = self.provider.execute("Say hello", output_callback=lambda _: None)

        expected_fallback = "".join(FIXTURE_TEXT_DELTAS)
        assert result.stdout == expected_fallback

    def test_malformed_lines_do_not_crash(self) -> None:
        """Malformed NDJSON lines interspersed in the stream do not raise exceptions."""
        mixed_lines = ["not valid json", self.fixture_lines[-1], "{{broken"]
        mock_run = _fake_run_subprocess_factory(mixed_lines)
        with patch("fdsx.providers.claude._run_subprocess", mock_run):
            result = self.provider.execute("Say hello", output_callback=lambda _: None)

        # result event was in the last fixture line, so stdout should be correct
        assert result.stdout == FIXTURE_RESULT_TEXT
        assert result.exit_code == 0

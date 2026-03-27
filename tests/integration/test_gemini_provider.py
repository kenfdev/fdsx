"""Integration tests for GeminiProvider (T003)."""

from unittest.mock import patch

from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD, ProviderResult
from fdsx.providers.gemini import GeminiOptions, GeminiProvider

FAKE_SUCCESS = ProviderResult(exit_code=0, stdout="ok", stderr="")


class TestGeminiBasicExecution:
    """Verify basic GeminiProvider execution builds correct CLI args."""

    def test_gemini_basic_execution(self):
        """args start with ["gemini", "-p", <prompt>]."""
        provider = GeminiProvider()

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.gemini._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello world")

        assert len(captured_args) == 1
        args = captured_args[0]
        assert args[0] == "gemini"
        assert args[1] == "-p"
        assert args[2] == "hello world"

    def test_gemini_with_model(self):
        """--model <model> present in args when model is specified."""
        provider = GeminiProvider()

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.gemini._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello", model="gemini-2.5-pro")

        args = captured_args[0]
        assert "--model" in args
        assert args[args.index("--model") + 1] == "gemini-2.5-pro"

    def test_gemini_with_options(self):
        """Option flags from to_cli_flags() appended to args."""
        options = GeminiOptions(yolo=True, sandbox=True)
        provider = GeminiProvider(options)

        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.gemini._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello")

        args = captured_args[0]
        assert "--yolo" in args
        assert "--sandbox" in args

    def test_gemini_large_prompt_stdin(self):
        """For prompt >= 128KB: args contain "-p", "-" and stdin_data equals prompt."""
        options = GeminiOptions()
        provider = GeminiProvider(options)

        large_prompt = "x" * (ARG_MAX_STDIN_THRESHOLD + 1)

        captured_args: list[list[str]] = []
        captured_kwargs: list[dict] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            captured_kwargs.append(dict(kwargs))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.gemini._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt=large_prompt)

        assert len(captured_args) == 1
        args = captured_args[0]
        assert "-p" in args
        assert "-" in args
        assert captured_kwargs[0].get("stdin_data") == large_prompt


class TestGeminiStreamingExecution:
    """Verify streaming execution wires _make_stream_callback correctly."""

    def test_gemini_streaming_appends_format_flag(self):
        """When output_callback is provided, --output-format and stream-json are in args."""
        provider = GeminiProvider()
        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.gemini._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello", output_callback=lambda x: None)

        args = captured_args[0]
        assert "--output-format" in args
        assert "stream-json" in args

    def test_gemini_streaming_parses_ndjson(self):
        """NDJSON assistant delta lines are parsed and forwarded to output_callback."""
        provider = GeminiProvider()
        received_lines: list[str] = []

        def fake_run_subprocess(args, **kwargs):
            cb = kwargs.get("output_callback")
            if cb:
                cb(
                    '{"type":"message","role":"assistant","delta":true,"content":"hello"}'
                )
                cb(
                    '{"type":"message","role":"assistant","delta":true,"content":" world"}'
                )
                cb('{"type":"result"}')
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="", stderr="")

        with patch(
            "fdsx.providers.gemini._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(
                prompt="hello", output_callback=lambda x: received_lines.append(x)
            )

        assert "hello world" in received_lines

    def test_gemini_streaming_result_from_messages(self):
        """ProviderResult.stdout is the concatenated assistant delta content."""
        provider = GeminiProvider()

        def fake_run_subprocess(args, **kwargs):
            cb = kwargs.get("output_callback")
            if cb:
                cb(
                    '{"type":"message","role":"assistant","delta":true,"content":"partial1"}'
                )
                cb(
                    '{"type":"message","role":"assistant","delta":true,"content":" partial2"}'
                )
                cb('{"type":"result"}')
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="raw stdout", stderr="")

        with patch(
            "fdsx.providers.gemini._run_subprocess", side_effect=fake_run_subprocess
        ):
            result = provider.execute(prompt="hello", output_callback=lambda x: None)

        assert result.stdout == "partial1 partial2"

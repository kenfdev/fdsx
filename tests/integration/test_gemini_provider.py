"""Integration tests for GeminiProvider (T003)."""

from unittest.mock import patch

import yaml

from fdsx.core.engine import run_flow
from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD, ProviderResult, get_provider
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


class TestGeminiProviderRegistration:
    """Verify GeminiProvider is registered in the provider factory."""

    def test_get_provider_returns_gemini_provider(self):
        """get_provider("gemini") returns a GeminiProvider instance."""
        provider = get_provider("gemini")
        assert isinstance(provider, GeminiProvider)

    def test_get_provider_with_options(self):
        """get_provider("gemini", {"yolo": True}) returns provider with yolo=True."""
        provider = get_provider("gemini", {"yolo": True})
        assert isinstance(provider, GeminiProvider)
        assert provider.options.yolo is True


class TestGeminiWorkflowExecution:
    """T013: Workflow-level integration tests for Gemini provider."""

    def test_gemini_workflow_execution(self, tmp_path):
        """A workflow with provider: gemini executes successfully via run_flow()."""
        flow_dict = {
            "name": "Gemini Workflow Test",
            "description": "Two gemini tasks in sequence",
            "version": "1.0",
            "start_at": "step1",
            "states": {
                "step1": {
                    "type": "task",
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                    "prompt_template": "Say hello",
                    "result_path": "$.greeting",
                    "next": "step2",
                },
                "step2": {
                    "type": "task",
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                    "prompt_template": "Say goodbye",
                    "result_path": "$.farewell",
                    "end": True,
                },
            },
        }
        flow_path = tmp_path / "gemini_workflow.yaml"
        with flow_path.open("w") as f:
            yaml.dump(flow_dict, f)

        fake = ProviderResult(exit_code=0, stdout="gemini output", stderr="")
        with patch("fdsx.providers.gemini._run_subprocess", return_value=fake):
            result = run_flow(flow_path, base_dir=tmp_path)

        assert "greeting" in result
        assert "farewell" in result
        assert result["greeting"] == "gemini output"
        assert result["farewell"] == "gemini output"

    def test_gemini_mixed_provider_workflow(self, tmp_path):
        """A mixed claude+gemini workflow passes state correctly between providers."""
        flow_dict = {
            "name": "Mixed Provider Workflow",
            "description": "Claude task followed by gemini task",
            "version": "1.0",
            "start_at": "claude_step",
            "states": {
                "claude_step": {
                    "type": "task",
                    "provider": "claude",
                    "model": "claude-sonnet-4-6",
                    "prompt_template": "Generate a greeting",
                    "result_path": "$.claude_output",
                    "next": "gemini_step",
                },
                "gemini_step": {
                    "type": "task",
                    "provider": "gemini",
                    "model": "gemini-2.5-flash",
                    "prompt_template": "Transform: {claude_output}",
                    "result_path": "$.gemini_output",
                    "end": True,
                },
            },
        }
        flow_path = tmp_path / "mixed_workflow.yaml"
        with flow_path.open("w") as f:
            yaml.dump(flow_dict, f)

        claude_fake = ProviderResult(exit_code=0, stdout="claude result", stderr="")
        gemini_fake = ProviderResult(exit_code=0, stdout="gemini result", stderr="")

        gemini_calls = []

        def capture_gemini_call(*args, **kwargs):
            gemini_calls.append((args, kwargs))
            return gemini_fake

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=claude_fake),
            patch(
                "fdsx.providers.gemini._run_subprocess", side_effect=capture_gemini_call
            ),
        ):
            result = run_flow(flow_path, base_dir=tmp_path)

        assert "claude_output" in result
        assert "gemini_output" in result
        assert result["claude_output"] == "claude result"
        assert result["gemini_output"] == "gemini result"

        # Verify Gemini received the interpolated Claude output
        assert len(gemini_calls) == 1
        call_args, call_kwargs = gemini_calls[0]
        gemini_args = call_kwargs.get("args") or call_args[0]  # keyword or positional
        gemini_cmd = " ".join(gemini_args)
        assert "claude result" in gemini_cmd, (
            f"Gemini subprocess should receive interpolated Claude output, got: {gemini_cmd}"
        )
        assert "{claude_output}" not in gemini_cmd, (
            f"Raw placeholder should be resolved, got: {gemini_cmd}"
        )

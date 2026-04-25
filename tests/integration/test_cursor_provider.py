"""Integration tests for CursorProvider (T005, T006, T011)."""

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD, ProviderResult, get_provider
from fdsx.providers.cursor import CursorOptions, CursorProvider, CursorProviderError

FAKE_SUCCESS = ProviderResult(exit_code=0, stdout="ok", stderr="")


class TestCursorProviderExecution:
    """T005: Verify CursorProvider.execute() builds correct CLI args."""

    def test_basic_invocation_argv_order(self):
        """args[0]=='agent', args[1]=='-p', args[2]==prompt, args[3]=='--trust'."""
        provider = CursorProvider()
        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="<prompt>")

        assert len(captured_args) == 1
        args = captured_args[0]
        assert args[0] == "agent"
        assert args[1] == "-p"
        assert args[2] == "<prompt>"
        assert "--trust" in args
        assert args.index("--trust") == 3

    def test_trust_always_passed(self):
        """'--trust' is always in argv regardless of options."""
        provider = CursorProvider()
        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello")

        assert "--trust" in captured_args[0]

    def test_model_flag_appended_when_model_truthy(self):
        """--model <value> present when model is truthy; absent when model=None."""
        provider = CursorProvider()
        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello", model="cursor-fast")

        args = captured_args[0]
        assert "--model" in args
        assert args[args.index("--model") + 1] == "cursor-fast"

        captured_args.clear()
        with patch(
            "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello", model=None)

        assert "--model" not in captured_args[0]

    def test_options_flags_appended_after_trust(self):
        """CursorOptions(force=True) → '--force' appears after '--trust' in argv."""
        options = CursorOptions(force=True)
        provider = CursorProvider(options)
        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello")

        args = captured_args[0]
        assert "--force" in args
        assert args.index("--force") > args.index("--trust")

    def test_large_prompt_uses_stdin(self):
        """For prompt >= ARG_MAX_STDIN_THRESHOLD bytes: args contain '-p', '-' and stdin_data equals prompt."""
        provider = CursorProvider()
        large_prompt = "x" * ARG_MAX_STDIN_THRESHOLD

        captured_args: list[list[str]] = []
        captured_kwargs: list[dict] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            captured_kwargs.append(dict(kwargs))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt=large_prompt)

        assert len(captured_args) == 1
        args = captured_args[0]
        assert "-p" in args
        assert "-" in args
        assert captured_kwargs[0].get("stdin_data") == large_prompt

    def test_missing_binary_raises_domain_error(self):
        """When 'agent' binary is not found, CursorProviderError is raised."""
        provider = CursorProvider()
        with (
            patch("fdsx.providers.cursor.shutil.which", return_value=None),
            pytest.raises(CursorProviderError),
        ):
            provider.execute(prompt="hello")

    def test_nonstreaming_no_output_format_flag(self):
        """'--output-format' and '--stream-partial-output' not in argv when output_callback=None."""
        provider = CursorProvider()
        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            return FAKE_SUCCESS

        with patch(
            "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
        ):
            provider.execute(prompt="hello", output_callback=None)

        args = captured_args[0]
        assert "--output-format" not in args
        assert "--stream-partial-output" not in args


class TestCursorProviderFactory:
    """T006: Verify CursorProvider is registered in the provider factory."""

    def test_factory_returns_cursor_provider_with_defaults(self):
        """get_provider('cursor') returns a CursorProvider with default CursorOptions."""
        provider = get_provider("cursor")
        assert isinstance(provider, CursorProvider)
        assert provider.options == CursorOptions()

    def test_factory_validates_options(self):
        """get_provider('cursor', {'force': True}) returns provider with force=True."""
        provider = get_provider("cursor", {"force": True})
        assert isinstance(provider, CursorProvider)
        assert provider.options.force is True

    def test_factory_rejects_unknown_option(self):
        """get_provider('cursor', {'yolo': True}) raises ValidationError."""
        with pytest.raises(ValidationError):
            get_provider("cursor", {"yolo": True})


class TestCursorStreamingExecution:
    """T011: Verify CursorProvider.execute() streaming branch."""

    def test_streaming_appends_output_format_flag(self):
        """When output_callback is provided, --output-format stream-json and --stream-partial-output are in args."""
        provider = CursorProvider()
        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="", stderr="")

        with (
            patch(
                "fdsx.providers.cursor.shutil.which",
                return_value="/usr/local/bin/agent",
            ),
            patch(
                "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
            ),
        ):
            provider.execute(prompt="hello", output_callback=lambda x: None)

        assert len(captured_args) == 1
        args = captured_args[0]
        assert "--output-format" in args
        assert "stream-json" in args
        assert "--stream-partial-output" in args

    def test_streaming_max_suspend_duration_default(self):
        """Streaming branch passes max_suspend_duration=DEFAULT_INACTIVITY_TIMEOUT to _run_subprocess."""
        provider = CursorProvider()
        captured_kwargs: list[dict] = []

        def fake_run_subprocess(args, **kwargs):
            captured_kwargs.append(dict(kwargs))
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="", stderr="")

        with (
            patch(
                "fdsx.providers.cursor.shutil.which",
                return_value="/usr/local/bin/agent",
            ),
            patch(
                "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
            ),
        ):
            provider.execute(prompt="hello", output_callback=lambda x: None)

        assert len(captured_kwargs) == 1
        assert captured_kwargs[0].get("max_suspend_duration") == 300

    def test_streaming_parses_ndjson(self):
        """NDJSON assistant delta lines are parsed and forwarded to output_callback."""
        provider = CursorProvider()
        received_lines: list[str] = []

        def fake_run_subprocess(args, **kwargs):
            cb = kwargs.get("output_callback")
            if cb:
                cb(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {"content": [{"type": "text", "text": "hello"}]},
                        }
                    )
                )
                cb(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [{"type": "text", "text": " world"}]
                            },
                        }
                    )
                )
                cb(json.dumps({"type": "result", "status": "success"}))
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="", stderr="")

        with (
            patch(
                "fdsx.providers.cursor.shutil.which",
                return_value="/usr/local/bin/agent",
            ),
            patch(
                "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
            ),
        ):
            provider.execute(
                prompt="hello", output_callback=lambda x: received_lines.append(x)
            )

        assert "hello" in received_lines
        assert " world" in received_lines

    def test_streaming_result_from_parsed_content(self):
        """ProviderResult.stdout is the concatenated assistant text content."""
        provider = CursorProvider()

        def fake_run_subprocess(args, **kwargs):
            cb = kwargs.get("output_callback")
            if cb:
                cb(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [{"type": "text", "text": "partial1"}]
                            },
                        }
                    )
                )
                cb(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [{"type": "text", "text": " partial2"}]
                            },
                        }
                    )
                )
                cb(json.dumps({"type": "result", "status": "success"}))
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="raw stdout", stderr="")

        with (
            patch(
                "fdsx.providers.cursor.shutil.which",
                return_value="/usr/local/bin/agent",
            ),
            patch(
                "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
            ),
        ):
            result = provider.execute(prompt="hello", output_callback=lambda x: None)

        assert result.stdout == "partial1 partial2"

    def test_streaming_raw_fallback_when_no_ndjson(self):
        """When no NDJSON content is parsed, ProviderResult.stdout is the raw stdout."""
        provider = CursorProvider()
        captured_args: list[list[str]] = []

        def fake_run_subprocess(args, **kwargs):
            captured_args.append(list(args))
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="raw", stderr="")

        with (
            patch(
                "fdsx.providers.cursor.shutil.which",
                return_value="/usr/local/bin/agent",
            ),
            patch(
                "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
            ),
        ):
            result = provider.execute(prompt="hello", output_callback=lambda x: None)

        # These flags are only present in the streaming branch — ensures T013 is wired.
        assert "--output-format" in captured_args[0]
        assert "stream-json" in captured_args[0]
        # When parser accumulates no text, fall back to raw subprocess stdout.
        assert result.stdout == "raw"

    def test_streaming_summary_callback_for_tool_call(self):
        """tool_call/started event forwards [tool: <key>] to summary_callback."""
        provider = CursorProvider()
        summary_received: list[str] = []

        def fake_run_subprocess(args, **kwargs):
            cb = kwargs.get("output_callback")
            if cb:
                cb(
                    json.dumps(
                        {
                            "type": "tool_call",
                            "subtype": "started",
                            "toolKey": "writeToolCall",
                        }
                    )
                )
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="", stderr="")

        with (
            patch(
                "fdsx.providers.cursor.shutil.which",
                return_value="/usr/local/bin/agent",
            ),
            patch(
                "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
            ),
        ):
            provider.execute(
                prompt="hello",
                output_callback=lambda x: None,
                summary_callback=summary_received.append,
            )

        assert "[tool: writeToolCall]" in summary_received

    def test_streaming_summary_callback_for_thinking_line(self):
        """thinking content in assistant event is forwarded as '[thinking] ...' to summary_callback."""
        provider = CursorProvider()
        summary_received: list[str] = []

        def fake_run_subprocess(args, **kwargs):
            cb = kwargs.get("output_callback")
            if cb:
                cb(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [
                                    {"type": "thinking", "thinking": "I am reasoning"}
                                ]
                            },
                        }
                    )
                )
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="", stderr="")

        with (
            patch(
                "fdsx.providers.cursor.shutil.which",
                return_value="/usr/local/bin/agent",
            ),
            patch(
                "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
            ),
        ):
            provider.execute(
                prompt="hello",
                output_callback=lambda x: None,
                summary_callback=summary_received.append,
            )

        assert any("[thinking]" in s for s in summary_received)

    def test_streaming_on_inactivity_hooks_suspend_resume(self):
        """on_inactivity_hooks are wired: suspend called on tool_call started, resume on completed."""
        provider = CursorProvider()
        suspend_calls: list[bool] = []
        resume_calls: list[bool] = []

        def fake_run_subprocess(args, **kwargs):
            hooks_fn = kwargs.get("on_inactivity_hooks")
            if hooks_fn is not None:
                hooks_fn(
                    lambda: suspend_calls.append(True),
                    lambda: resume_calls.append(True),
                )
            cb = kwargs.get("output_callback")
            if cb:
                cb(
                    json.dumps(
                        {
                            "type": "tool_call",
                            "subtype": "started",
                            "toolKey": "readTool",
                        }
                    )
                )
                cb(
                    json.dumps(
                        {
                            "type": "tool_call",
                            "subtype": "completed",
                            "toolKey": "readTool",
                        }
                    )
                )
            evt = kwargs.get("completion_event")
            if evt:
                evt.set()
            return ProviderResult(exit_code=0, stdout="", stderr="")

        with (
            patch(
                "fdsx.providers.cursor.shutil.which",
                return_value="/usr/local/bin/agent",
            ),
            patch(
                "fdsx.providers.cursor._run_subprocess", side_effect=fake_run_subprocess
            ),
        ):
            provider.execute(prompt="hello", output_callback=lambda x: None)

        assert suspend_calls == [True]
        assert resume_calls == [True]

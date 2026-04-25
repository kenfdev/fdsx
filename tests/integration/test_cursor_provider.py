"""Integration tests for CursorProvider (T005, T006)."""

from unittest.mock import patch

import pytest
from fdsx.providers.cursor import CursorOptions, CursorProvider, CursorProviderError
from pydantic import ValidationError

from fdsx.providers.base import ARG_MAX_STDIN_THRESHOLD, ProviderResult, get_provider

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

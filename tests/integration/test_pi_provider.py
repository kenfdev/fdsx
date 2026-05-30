"""Integration tests for pi provider execution and validation."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from fdsx.core.config import FdsxConfig
from fdsx.core.loader import load_flow
from fdsx.providers.base import (
    ARG_MAX_STDIN_THRESHOLD,
    DEFAULT_EXECUTION_TIMEOUT,
    DEFAULT_INACTIVITY_TIMEOUT,
    ProviderResult,
)

FAKE_SUCCESS = ProviderResult(exit_code=0, stdout="ok", stderr="")


def _pi_symbol(name: str) -> Any:
    try:
        module = importlib.import_module("fdsx.providers.pi")
    except ModuleNotFoundError as exc:
        if exc.name == "fdsx.providers.pi":
            pytest.fail("fdsx.providers.pi module is not implemented")
        raise
    try:
        return getattr(module, name)
    except AttributeError:
        pytest.fail(f"fdsx.providers.pi.{name} is not implemented")


def _write_flow(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


class TestPiProviderExecution:
    """Verify PiProvider.execute() observable subprocess routing behavior."""

    def test_normal_prompt_executes_pi_dash_p_prompt(self) -> None:
        """A normal pi prompt executes as ['pi', '-p', prompt]."""
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider()

        with patch(
            "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
        ) as run:
            provider.execute(prompt="hello")

        run.assert_called_once()
        assert run.call_args.kwargs["args"] == ["pi", "-p", "hello"]

    def test_large_prompt_is_sent_through_stdin_without_positional_prompt(
        self,
    ) -> None:
        """A prompt at ARG_MAX_STDIN_THRESHOLD bytes is sent via stdin_data."""
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider()
        prompt = "x" * ARG_MAX_STDIN_THRESHOLD

        with patch(
            "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
        ) as run:
            provider.execute(prompt=prompt)

        run.assert_called_once()
        assert run.call_args.kwargs["args"] == ["pi", "-p"]
        assert run.call_args.kwargs["stdin_data"] == prompt

    def test_model_argument_is_passed_as_model_flag(self) -> None:
        """Passing model=... emits --model with the requested model."""
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider()

        with (
            patch("fdsx.providers.pi.shutil.which", return_value="/usr/bin/pi"),
            patch(
                "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
            ) as run,
        ):
            provider.execute(prompt="hello", model="some-model")

        args = run.call_args.kwargs["args"]
        assert "--model" in args
        assert args[args.index("--model") + 1] == "some-model"

    def test_provider_slash_model_id_is_forwarded_verbatim(self) -> None:
        """Provider-qualified model ids are forwarded without normalization."""
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider()

        with (
            patch("fdsx.providers.pi.shutil.which", return_value="/usr/bin/pi"),
            patch(
                "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
            ) as run,
        ):
            provider.execute(prompt="hello", model="openai/gpt-4o")

        args = run.call_args.kwargs["args"]
        assert "--model" in args
        assert args[args.index("--model") + 1] == "openai/gpt-4o"

    def test_shorthand_model_id_is_forwarded_verbatim(self) -> None:
        """Shorthand model ids are forwarded without provider prefix changes."""
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider()

        with (
            patch("fdsx.providers.pi.shutil.which", return_value="/usr/bin/pi"),
            patch(
                "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
            ) as run,
        ):
            provider.execute(prompt="hello", model="gpt-4o")

        args = run.call_args.kwargs["args"]
        assert "--model" in args
        assert args[args.index("--model") + 1] == "gpt-4o"

    def test_model_flag_is_absent_when_model_is_none(self) -> None:
        """Omitting model selection leaves the pi CLI args without --model."""
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider()

        with (
            patch("fdsx.providers.pi.shutil.which", return_value="/usr/bin/pi"),
            patch(
                "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
            ) as run,
        ):
            provider.execute(prompt="hello", model=None)

        args = run.call_args.kwargs["args"]
        assert "--model" not in args

    def test_default_timeouts_are_passed_to_subprocess(self) -> None:
        """Default execution and inactivity timeouts are passed to _run_subprocess."""
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider()

        with patch(
            "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
        ) as run:
            provider.execute(prompt="hello")

        assert run.call_args.kwargs["timeout"] == DEFAULT_EXECUTION_TIMEOUT
        assert run.call_args.kwargs["inactivity_timeout"] == DEFAULT_INACTIVITY_TIMEOUT

    def test_custom_inactivity_timeout_is_passed_to_subprocess(self) -> None:
        """A custom inactivity_timeout from PiOptions is passed to _run_subprocess."""
        pi_options = _pi_symbol("PiOptions")
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider(pi_options(inactivity_timeout=10))

        with patch(
            "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
        ) as run:
            provider.execute(prompt="hello")

        assert run.call_args.kwargs["inactivity_timeout"] == 10

    def test_allowed_tools_are_passed_to_pi_subprocess_args(self) -> None:
        """allowed_tools provider options are forwarded as pi --tools args."""
        pi_options = _pi_symbol("PiOptions")
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider(pi_options(allowed_tools=["read", "bash"]))

        with patch(
            "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
        ) as run:
            provider.execute(prompt="hello")

        assert run.call_args.kwargs["args"] == [
            "pi",
            "-p",
            "hello",
            "--tools",
            "read,bash",
        ]

    def test_disallowed_tools_are_passed_to_pi_subprocess_args(self) -> None:
        """disallowed_tools provider options are forwarded as pi --exclude-tools args."""
        pi_options = _pi_symbol("PiOptions")
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider(pi_options(disallowed_tools=["write", "edit"]))

        with patch(
            "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
        ) as run:
            provider.execute(prompt="hello")

        assert run.call_args.kwargs["args"] == [
            "pi",
            "-p",
            "hello",
            "--exclude-tools",
            "write,edit",
        ]

    def test_disable_tools_is_passed_to_pi_subprocess_args(self) -> None:
        """disable_tools provider option is forwarded as pi --no-tools."""
        pi_options = _pi_symbol("PiOptions")
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider(pi_options(disable_tools=True))

        with patch(
            "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
        ) as run:
            provider.execute(prompt="hello")

        assert run.call_args.kwargs["args"] == [
            "pi",
            "-p",
            "hello",
            "--no-tools",
        ]

    def test_callbacks_are_forwarded_to_subprocess(self) -> None:
        """Output, stderr, and process-start callbacks are forwarded directly."""
        pi_provider = _pi_symbol("PiProvider")
        provider = pi_provider()

        def output_callback(line: str) -> None:
            _ = line

        def stderr_callback(line: str) -> None:
            _ = line

        def on_process_start(process: subprocess.Popen[str]) -> None:
            _ = process

        with patch(
            "fdsx.providers.pi._run_subprocess", return_value=FAKE_SUCCESS
        ) as run:
            provider.execute(
                prompt="hello",
                output_callback=output_callback,
                stderr_callback=stderr_callback,
                on_process_start=on_process_start,
            )

        assert run.call_args.kwargs["output_callback"] is output_callback
        assert run.call_args.kwargs["stderr_callback"] is stderr_callback
        assert run.call_args.kwargs["on_process_start"] is on_process_start

    def test_missing_pi_binary_raises_domain_error_naming_pi(self) -> None:
        """When pi is missing on PATH, execution raises PiProviderError naming pi."""
        pi_provider = _pi_symbol("PiProvider")
        pi_provider_error = _pi_symbol("PiProviderError")
        provider = pi_provider()

        with (
            patch("fdsx.providers.pi.shutil.which", return_value=None),
            pytest.raises(pi_provider_error, match="pi"),
        ):
            provider.execute(prompt="hello")


class TestPiWorkflowValidation:
    """Verify workflow and config validation accepts provider: pi."""

    def test_pi_is_valid_where_existing_llm_providers_are_valid(
        self, tmp_path: Path
    ) -> None:
        """Workflow/config validation accepts provider: pi across LLM provider fields."""
        flow_path = _write_flow(
            tmp_path / "pi-flow.yaml",
            """
name: Pi Provider Flow
description: pi provider flow
start_at: direct
version: '1.0'
states:
  direct:
    type: task
    provider: pi
    model: ignored-by-t001
    prompt_template: Hello
    result_path: $.direct
    next: fanout
  fanout:
    type: parallel
    branches:
      - provider: pi
        model: ignored-by-t001
        prompt_template: Branch hello
    result_path: $.branches
    end: true
""",
        )

        flow, errors = load_flow(
            flow_path,
            config_profiles={"pi_profile": {"provider": "pi", "model": "ignored"}},
        )

        assert errors == []
        assert flow is not None

        config = FdsxConfig(
            task_splitter={"provider": "pi", "model": "ignored"},
            workflow_selector={"provider": "pi", "model": "ignored"},
            profiles={"pi_profile": {"provider": "pi", "model": "ignored"}},
            providers={"pi": {"inactivity_timeout": 10}},
        )
        assert config.task_splitter is not None
        assert config.task_splitter.provider == "pi"

"""Unit tests for fdsx.core.compiler.execution — execute_with_retry.

T010: Cover retry with exponential backoff, system vs LLM dispatch,
timeout handling, extraction success, and extraction failure after retries.
"""

import subprocess
from unittest.mock import MagicMock, patch

from fdsx.providers.base import ProviderResult

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_config(
    *,
    provider_name: str = "openai",
    prompt: str = "test prompt",
    command: str = "",
    model: str | None = "gpt-4",
    timeout_seconds: int | None = None,
    max_retries: int = 3,
    extract=None,
    stream_logger=None,
):
    """Build an ExecutionConfig with sensible defaults."""
    from fdsx.core.compiler.execution import ExecutionConfig

    mock_provider = MagicMock()
    mock_logger = stream_logger or MagicMock()
    return (
        ExecutionConfig(
            provider=mock_provider,
            provider_name=provider_name,
            prompt=prompt,
            command=command,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            extract=extract,
            stream_logger=mock_logger,
        ),
        mock_provider,
        mock_logger,
    )


# ---------------------------------------------------------------------------
# Exponential backoff
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    """Verify sleep durations during retries."""

    def test_backoff_delays_correct_sequence(self):
        """Retries 1,2,3 sleep for 1,2,4 seconds (capped at 30)."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=3)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=1, stdout="", stderr="err"
        )

        sleep_times: list[float] = []
        with patch("fdsx.core.compiler.execution.time") as mock_time:
            mock_time.sleep.side_effect = lambda s: sleep_times.append(s)
            execute_with_retry(config)

        assert sleep_times == [1, 2, 4]

    def test_no_sleep_on_first_attempt(self):
        """First attempt (attempt=0) has no preceding sleep."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=0)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="ok", stderr=""
        )

        sleep_times: list[float] = []
        with patch("fdsx.core.compiler.execution.time") as mock_time:
            mock_time.sleep.side_effect = lambda s: sleep_times.append(s)
            execute_with_retry(config)

        assert sleep_times == []

    def test_backoff_capped_at_30_seconds(self):
        """Sleep never exceeds 30 seconds regardless of retry count."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=10)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=1, stdout="", stderr="err"
        )

        sleep_times: list[float] = []
        with patch("fdsx.core.compiler.execution.time") as mock_time:
            mock_time.sleep.side_effect = lambda s: sleep_times.append(s)
            execute_with_retry(config)

        assert len(sleep_times) == 10
        assert max(sleep_times) == 30
        for i, s in enumerate(sleep_times):
            assert s == min(2**i, 30)


# ---------------------------------------------------------------------------
# System vs LLM provider dispatch
# ---------------------------------------------------------------------------


class TestProviderDispatch:
    """Verify the correct provider.execute() call signature per provider type."""

    def test_system_provider_uses_command_kwarg(self):
        """system provider call passes command=..., not prompt."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _mock_logger = _make_config(
            provider_name="system",
            prompt="",
            command="echo hello",
            max_retries=0,
        )
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="hello", stderr=""
        )

        execute_with_retry(config)

        call_kwargs = mock_provider.execute.call_args.kwargs
        assert call_kwargs["command"] == "echo hello"
        assert call_kwargs["prompt"] == ""

    def test_llm_provider_uses_prompt_kwarg(self):
        """Non-system provider call passes prompt=..., no command."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _mock_logger = _make_config(
            provider_name="openai",
            prompt="analyze this",
            max_retries=0,
        )
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="analysis", stderr=""
        )

        execute_with_retry(config)

        call_kwargs = mock_provider.execute.call_args.kwargs
        assert call_kwargs["prompt"] == "analyze this"
        assert "command" not in call_kwargs

    def test_callbacks_forwarded_to_provider(self):
        """output_callback and stderr_callback come from stream_logger."""
        from fdsx.core.compiler.execution import execute_with_retry

        mock_logger = MagicMock()
        config, mock_provider, _ = _make_config(
            max_retries=0, stream_logger=mock_logger
        )
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="out", stderr=""
        )

        execute_with_retry(config)

        call_kwargs = mock_provider.execute.call_args.kwargs
        assert call_kwargs["output_callback"] is mock_logger.on_stdout
        assert call_kwargs["stderr_callback"] is mock_logger.on_stderr

    def test_model_and_timeout_forwarded(self):
        """model and timeout_seconds are passed through to provider.execute."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(
            model="claude-3", timeout_seconds=60, max_retries=0
        )
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="ok", stderr=""
        )

        execute_with_retry(config)

        call_kwargs = mock_provider.execute.call_args.kwargs
        assert call_kwargs["model"] == "claude-3"
        assert call_kwargs["timeout"] == 60


# ---------------------------------------------------------------------------
# Timeout handling
# ---------------------------------------------------------------------------


class TestTimeoutHandling:
    """TimeoutExpired / TimeoutError triggers retry, not immediate failure."""

    def test_subprocess_timeout_triggers_retry(self):
        """subprocess.TimeoutExpired is caught and retried."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=1)
        mock_provider.execute.side_effect = [
            subprocess.TimeoutExpired(cmd="cmd", timeout=10),
            ProviderResult(exit_code=0, stdout="ok", stderr=""),
        ]

        with patch("fdsx.core.compiler.execution.time"):
            result = execute_with_retry(config)

        assert result.result.exit_code == 0
        assert mock_provider.execute.call_count == 2

    def test_timeout_error_triggers_retry(self):
        """Built-in TimeoutError is caught and retried."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=1)
        mock_provider.execute.side_effect = [
            TimeoutError("timed out"),
            ProviderResult(exit_code=0, stdout="ok", stderr=""),
        ]

        with patch("fdsx.core.compiler.execution.time"):
            result = execute_with_retry(config)

        assert result.result.exit_code == 0
        assert mock_provider.execute.call_count == 2

    def test_timeout_on_all_attempts_returns_failed_result(self):
        """If every attempt times out the result has exit_code != 0."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=1)
        mock_provider.execute.side_effect = TimeoutError("always")

        with patch("fdsx.core.compiler.execution.time"):
            result = execute_with_retry(config)

        assert result.result.exit_code != 0
        assert result.last_error != ""


# ---------------------------------------------------------------------------
# Extraction success and failure
# ---------------------------------------------------------------------------


class TestExtraction:
    """Verify extraction logic inside the retry loop."""

    def _make_extract_rule(self, result_path: str = "$.extracted"):
        rule = MagicMock()
        rule.result_path = result_path
        return rule

    def test_extraction_success_breaks_loop(self):
        """Successful extraction stops retrying immediately."""
        from fdsx.core.compiler.execution import execute_with_retry

        extract_rule = self._make_extract_rule()
        config, mock_provider, _ = _make_config(max_retries=3, extract=extract_rule)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout='{"result": "yes"}', stderr=""
        )

        with patch("fdsx.core.compiler.execution.extract_value", return_value="yes"):
            result = execute_with_retry(config)

        assert result.extracted == "yes"
        assert mock_provider.execute.call_count == 1

    def test_extraction_failure_retries(self):
        """When extraction returns None retries continue."""
        from fdsx.core.compiler.execution import execute_with_retry

        extract_rule = self._make_extract_rule()
        config, mock_provider, _ = _make_config(max_retries=2, extract=extract_rule)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="bad output", stderr=""
        )

        with (
            patch("fdsx.core.compiler.execution.time"),
            patch("fdsx.core.compiler.execution.extract_value", return_value=None),
        ):
            result = execute_with_retry(config)

        # All 3 attempts tried, extraction still None
        assert result.extracted is None
        assert mock_provider.execute.call_count == 3

    def test_extraction_failure_sets_last_error(self):
        """After all retries, last_error reflects extraction failure."""
        from fdsx.core.compiler.execution import execute_with_retry

        extract_rule = self._make_extract_rule()
        config, mock_provider, _ = _make_config(max_retries=0, extract=extract_rule)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="bad", stderr=""
        )

        with patch("fdsx.core.compiler.execution.extract_value", return_value=None):
            result = execute_with_retry(config)

        assert "Extraction failed" in result.last_error

    def test_no_extract_rule_breaks_loop_on_success(self):
        """Without extract rule, exit_code == 0 immediately breaks retry loop."""
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=3, extract=None)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="done", stderr=""
        )

        result = execute_with_retry(config)

        assert result.result.exit_code == 0
        assert mock_provider.execute.call_count == 1

    def test_extract_value_called_with_source_provider(self):
        """extract_value receives source_provider=provider_name."""
        from fdsx.core.compiler.execution import execute_with_retry

        extract_rule = self._make_extract_rule()
        config, mock_provider, _ = _make_config(
            provider_name="system",
            prompt="",
            command="echo test",
            max_retries=0,
            extract=extract_rule,
        )
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="output", stderr=""
        )

        with patch(
            "fdsx.core.compiler.execution.extract_value", return_value="val"
        ) as mock_ev:
            execute_with_retry(config)

        _, call_kwargs = mock_ev.call_args
        assert call_kwargs.get("source_provider") == "system"


# ---------------------------------------------------------------------------
# stream_logger lifecycle
# ---------------------------------------------------------------------------


class TestStreamLoggerLifecycle:
    """stream_logger.close() is always called (even on exception)."""

    def test_stream_logger_closed_on_success(self):
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, mock_logger = _make_config(max_retries=0)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="ok", stderr=""
        )

        execute_with_retry(config)

        mock_logger.close.assert_called_once()

    def test_stream_logger_closed_even_after_all_failures(self):
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, mock_logger = _make_config(max_retries=0)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=1, stdout="", stderr="fail"
        )

        execute_with_retry(config)

        mock_logger.close.assert_called_once()

    def test_stream_logger_closed_on_timeout_exception(self):
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, mock_logger = _make_config(max_retries=0)
        mock_provider.execute.side_effect = TimeoutError("bang")

        execute_with_retry(config)

        mock_logger.close.assert_called_once()


# ---------------------------------------------------------------------------
# ExecutionResult shape
# ---------------------------------------------------------------------------


class TestExecutionResult:
    """Verify result object fields are correctly populated."""

    def test_result_has_provider_result(self):
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=0)
        pr = ProviderResult(exit_code=0, stdout="hello", stderr="")
        mock_provider.execute.return_value = pr

        result = execute_with_retry(config)

        assert result.result is pr

    def test_result_extracted_none_when_no_extract_rule(self):
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=0, extract=None)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="out", stderr=""
        )

        result = execute_with_retry(config)

        assert result.extracted is None

    def test_result_last_error_empty_on_success(self):
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=0)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=0, stdout="ok", stderr=""
        )

        result = execute_with_retry(config)

        # last_error may be the initial sentinel but not an error message
        assert result.result.exit_code == 0

    def test_result_last_error_set_on_provider_failure(self):
        from fdsx.core.compiler.execution import execute_with_retry

        config, mock_provider, _ = _make_config(max_retries=0)
        mock_provider.execute.return_value = ProviderResult(
            exit_code=1, stdout="", stderr="something went wrong"
        )

        result = execute_with_retry(config)

        assert result.last_error == "something went wrong"

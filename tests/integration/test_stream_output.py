"""Integration tests for StreamLogger output streaming (T001, T002).

Tests verify that:
- Summary lines are always visible on stderr regardless of quiet mode
- Raw stdout/stderr output is suppressed in quiet mode
- Raw stdout/stderr output is visible in normal mode
- Summary lines are written to the log file
"""

from fdsx.logging.stream_logger import StreamLogger


class TestSummaryLinesInQuietMode:
    """T001: Summary lines are visible even when quiet=True."""

    def test_summary_lines_visible_in_quiet_mode(self, capsys):
        """on_summary() output appears on stderr even when quiet=True."""
        logger = StreamLogger(state_name="s", quiet=True)
        logger.on_summary("test")

        stderr = capsys.readouterr().err
        assert "[s] test" in stderr


class TestRawOutputInQuietMode:
    """T002a: Raw stdout is suppressed when quiet=True."""

    def test_raw_output_suppressed_in_quiet_mode(self, capsys):
        """on_stdout() output is suppressed when quiet=True."""
        logger = StreamLogger(state_name="s", quiet=True)
        logger.on_stdout("test")

        stderr = capsys.readouterr().err
        assert "test" not in stderr


class TestRawOutputInNormalMode:
    """T002b: Raw stdout is visible when quiet=False."""

    def test_raw_output_visible_in_normal_mode(self, capsys):
        """on_stdout() output appears on stderr when quiet=False."""
        logger = StreamLogger(state_name="s", quiet=False)
        logger.on_stdout("test")

        stderr = capsys.readouterr().err
        assert "[s] test" in stderr


class TestSummaryWrittenToLogFile:
    """T002c: Summary lines are written to the per-state log file."""

    def test_summary_written_to_log_file(self, tmp_path):
        """on_summary() writes content to the log file when log_dir is set."""
        logger = StreamLogger(state_name="s", log_dir=tmp_path, quiet=True)
        logger.on_summary("hello")
        logger.close()

        log_file = tmp_path / "s_1.log"
        content = log_file.read_text()
        assert "hello" in content


class TestQuietModeShowsSummarySuppressesRaw:
    """T008: Quiet mode shows summary_callback lines but suppresses output_callback.

    Tests the full pipeline: ExecutionConfig → execute_with_retry → provider.execute()
    → _make_stream_callback(). _run_subprocess is mocked to invoke stream_callback
    with stream-json events.
    """

    def test_quiet_mode_shows_summary_suppresses_raw(self, capsys, tmp_path):
        """In quiet mode, summary lines appear on stderr via the full pipeline."""
        import json
        from unittest.mock import patch

        from fdsx.core.compiler.execution import ExecutionConfig, execute_with_retry
        from fdsx.providers.base import ProviderResult, get_provider

        events = [
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tu_001",
                        "name": "Bash",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "hello world"},
                }
            ),
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 2,
                    "delta": {
                        "type": "thinking_delta",
                        "thinking": "I should think",
                    },
                }
            ),
            json.dumps({"type": "result", "result": "done"}),
        ]

        def fake_run_subprocess(**kwargs):
            cb = kwargs.get("output_callback")
            if cb:
                for event in events:
                    cb(event)
            return ProviderResult(exit_code=0, stdout="hello world", stderr="")

        provider = get_provider("claude", {})
        stream_logger = StreamLogger("test", tmp_path, quiet=True)

        exec_config = ExecutionConfig(
            provider=provider,
            provider_name="claude",
            prompt="test prompt",
            command="",
            model=None,
            timeout_seconds=None,
            max_retries=0,
            extract=None,
            stream_logger=stream_logger,
            summary_callback=stream_logger.on_summary,
        )

        with patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=fake_run_subprocess,
        ):
            execute_with_retry(exec_config)

        stderr = capsys.readouterr().err
        assert "[test] [tool: Bash]" in stderr
        assert "[test] [thinking]" in stderr
        assert "hello world" not in stderr


class TestNormalModeShowsBoth:
    """T009: Normal mode (quiet=False) shows both summary and output lines
    through the full execution pipeline.
    """

    def test_normal_mode_shows_both(self, capsys, tmp_path):
        """In normal mode, both summary and output lines appear via the pipeline."""
        import json
        from unittest.mock import patch

        from fdsx.core.compiler.execution import ExecutionConfig, execute_with_retry
        from fdsx.providers.base import ProviderResult, get_provider

        events = [
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tu_001",
                        "name": "Bash",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "output text"},
                }
            ),
            json.dumps({"type": "result", "result": "done"}),
        ]

        def fake_run_subprocess(**kwargs):
            cb = kwargs.get("output_callback")
            if cb:
                for event in events:
                    cb(event)
            return ProviderResult(exit_code=0, stdout="output text", stderr="")

        provider = get_provider("claude", {})
        stream_logger = StreamLogger("s", tmp_path, quiet=False)

        exec_config = ExecutionConfig(
            provider=provider,
            provider_name="claude",
            prompt="test",
            command="",
            model=None,
            timeout_seconds=None,
            max_retries=0,
            extract=None,
            stream_logger=stream_logger,
            summary_callback=stream_logger.on_summary,
        )

        with patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=fake_run_subprocess,
        ):
            execute_with_retry(exec_config)

        stderr = capsys.readouterr().err
        assert "[s] [tool: Bash]" in stderr  # summary via on_summary
        assert "[s] output text" in stderr  # raw output via on_stdout


class TestParallelBranchesOutputPrefixed:
    """T010: Branch logger prefix works through the execution pipeline."""

    def test_branch_logger_prefix(self, capsys, tmp_path):
        """Branch logger prefix [myworkflow_branch1] works through the pipeline."""
        import json
        from unittest.mock import patch

        from fdsx.core.compiler.execution import ExecutionConfig, execute_with_retry
        from fdsx.providers.base import ProviderResult, get_provider

        events = [
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tu_001",
                        "name": "Read",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "branch output"},
                }
            ),
            json.dumps({"type": "result", "result": "done"}),
        ]

        def fake_run_subprocess(**kwargs):
            cb = kwargs.get("output_callback")
            if cb:
                for event in events:
                    cb(event)
            return ProviderResult(exit_code=0, stdout="branch output", stderr="")

        provider = get_provider("claude", {})
        stream_logger = StreamLogger("myworkflow_branch1", tmp_path, quiet=False)

        exec_config = ExecutionConfig(
            provider=provider,
            provider_name="claude",
            prompt="test",
            command="",
            model=None,
            timeout_seconds=None,
            max_retries=0,
            extract=None,
            stream_logger=stream_logger,
            summary_callback=stream_logger.on_summary,
        )

        with patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=fake_run_subprocess,
        ):
            execute_with_retry(exec_config)

        stderr = capsys.readouterr().err
        assert "[myworkflow_branch1] [tool: Read]" in stderr
        assert "[myworkflow_branch1] branch output" in stderr

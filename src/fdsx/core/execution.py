"""Shared execution loop for task nodes and branch executors.

Extracts the retry / dispatch / extract pattern that was duplicated between
``_create_task_node`` and ``_create_branch_executor`` in ``compiler.py``.

Design notes:
- Callers are responsible for template resolution and provider instantiation
  before calling ``execute_with_retry``.
- ``StreamLogger`` lifecycle (open / close) is owned by this module.
- No terminal display or recorder calls happen here; those differ per caller
  and stay in ``compiler.py``.
- Returns ``ExecutionResult``; callers decide whether to raise (task node) or
  capture (branch executor).
"""
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fdsx.core.extraction import extract_value
from fdsx.providers.base import ProviderBase, ProviderResult, get_provider

if TYPE_CHECKING:
    from fdsx.logging.stream_logger import StreamLogger
    from fdsx.models.flow import ExtractRule


@dataclass
class ExecutionConfig:
    """Pre-resolved inputs for a single execute_with_retry call.

    All template resolution and provider construction must be done by the
    caller before creating this object.

    Attributes:
        provider: An already-constructed provider instance.
        provider_name: The string name of the provider (e.g. ``"system"``,
            ``"openai"``).  Used to select the dispatch branch.
        prompt: Resolved prompt string for LLM providers.
        command: Resolved shell command string for the system provider.
            Pass ``""`` when not applicable.
        model: Model name forwarded to ``provider.execute()``.
        timeout_seconds: Execution timeout forwarded to ``provider.execute()``.
        max_retries: Number of *retry* attempts after the first attempt.
            Total attempts = ``max_retries + 1``.
        extract: Optional extraction rule applied to successful output.
            When ``None`` any exit-code-0 result is treated as success.
        stream_logger: Logger whose ``on_stdout`` / ``on_stderr`` callbacks are
            forwarded to ``provider.execute()``.  ``close()`` is called by
            ``execute_with_retry`` in a ``finally`` block.
    """

    provider: ProviderBase
    provider_name: str
    prompt: str
    command: str
    model: str | None
    timeout_seconds: int | None
    max_retries: int
    extract: "ExtractRule | None"
    stream_logger: "StreamLogger"


@dataclass
class ExecutionResult:
    """Outcome of an ``execute_with_retry`` call.

    Attributes:
        result: The ``ProviderResult`` from the last attempt.
        extracted: The extracted value when an ``ExtractRule`` succeeded,
            otherwise ``None``.
        last_error: Description of the last error encountered.  When all
            attempts succeed this holds the initial sentinel value.
    """

    result: ProviderResult
    extracted: str | None
    last_error: str


# Sentinel used as the initial last_error before any attempt
_NO_ATTEMPTS_ERROR = "No attempts made"


def execute_with_retry(config: ExecutionConfig) -> ExecutionResult:
    """Run a provider with exponential backoff retries and optional extraction.

    The retry loop:
    1. Dispatches to the system command path or the LLM prompt path depending
       on ``config.provider_name``.
    2. Catches ``subprocess.TimeoutExpired`` and ``TimeoutError``; records the
       error and continues to the next attempt.
    3. On exit-code-0 with no ``extract`` rule: breaks immediately.
    4. On exit-code-0 with an ``extract`` rule: attempts extraction; breaks on
       first successful extraction.
    5. Sleeps ``min(2 ** (attempt - 1), 30)`` seconds before each retry
       (no sleep before the first attempt).

    ``config.stream_logger.close()`` is **always** called in a ``finally``
    block after the loop.

    Args:
        config: Pre-resolved execution configuration.

    Returns:
        ``ExecutionResult`` describing the outcome.  The caller must inspect
        ``result.exit_code`` and ``extracted`` to decide whether to raise or
        continue.
    """
    last_error = _NO_ATTEMPTS_ERROR
    result = ProviderResult(exit_code=1, stdout="", stderr="")
    extracted: str | None = None

    try:
        for attempt in range(config.max_retries + 1):
            if attempt > 0:
                time.sleep(min(2 ** (attempt - 1), 30))
            try:
                if config.provider_name == "system":
                    result = config.provider.execute(
                        prompt="",
                        model=config.model,
                        timeout=config.timeout_seconds,
                        command=config.command,
                        output_callback=config.stream_logger.on_stdout,
                        stderr_callback=config.stream_logger.on_stderr,
                    )
                else:
                    result = config.provider.execute(
                        prompt=config.prompt,
                        model=config.model,
                        timeout=config.timeout_seconds,
                        output_callback=config.stream_logger.on_stdout,
                        stderr_callback=config.stream_logger.on_stderr,
                    )
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                last_error = str(exc)
                result = ProviderResult(exit_code=1, stdout="", stderr=last_error)
                continue

            if result.exit_code == 0:
                if config.extract:
                    extracted = extract_value(
                        result.stdout.strip(),
                        config.extract,
                        get_provider,
                        source_provider=config.provider_name,
                    )
                    if extracted is not None:
                        break
                    last_error = "Extraction failed: all strategies returned None"
                else:
                    break
            else:
                last_error = result.stderr
    finally:
        config.stream_logger.close()

    return ExecutionResult(result=result, extracted=extracted, last_error=last_error)

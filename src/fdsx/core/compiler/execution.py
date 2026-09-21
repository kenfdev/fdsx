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

import subprocess  # nosec B404 - callback types and timeout exception handling.
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from fdsx.core.cancellation import check_cancelled, current_cancellation
from fdsx.core.extraction import extract_value
from fdsx.core.structured_output import (
    StructuredOutputValidationError,
    create_structured_output_validator,
    parse_structured_output,
    prepare_provider_schema,
)
from fdsx.logging.attempts import record_attempt
from fdsx.providers.base import (
    ProviderBase,
    ProviderResult,
    ProviderSessionError,
    SessionRequest,
    get_provider,
)

if TYPE_CHECKING:
    from fdsx.core.compiler.helpers import EscalationTarget
    from fdsx.core.extraction_fallback import FallbackEvent, ResolvedFallback
    from fdsx.logging.stream_logger import StreamLogger
    from fdsx.models.flow import ExtractRule, StructuredOutput


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
        summary_callback: Optional callback for summary lines that should be
            visible even in quiet mode. When ``None``, ``stream_logger.on_summary``
            is used.
        resolved_fallback: Pre-resolved fallback for this state's extract rule, or
            ``None`` if no fallback applies (no rule fallback, no workflow override,
            no global default, or workflow disable).
        flow_profiles: Serialised workflow-level profiles dict (``Flow.profiles``),
            passed through for profile resolution inside ``execute_default_fallback``.
            ``None`` when the flow defines no profiles.
        config_profiles: Serialised config-level profiles dict (``FdsxConfig.profiles``
            serialised to plain dicts), passed for profile resolution. ``None`` when
            the global config defines no profiles.
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
    prompt_prefix: str = ""
    structured_output: "StructuredOutput | None" = None
    on_process_start: Callable[[subprocess.Popen[str]], None] | None = None
    summary_callback: Callable[[str], None] | None = None
    resolved_fallback: "ResolvedFallback | None" = None
    flow_profiles: "dict[str, dict[str, Any]] | None" = None
    config_profiles: "dict[str, dict[str, Any]] | None" = None
    on_fallback: "Callable[[FallbackEvent], None] | None" = None
    escalation: "EscalationTarget | None" = None
    on_escalation_activated: Callable[[], None] | None = None
    session_request: SessionRequest | None = None


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
    extracted: Any | None
    last_error: str
    structured_value: dict[str, Any] | list[Any] | None = None
    last_provider_name: str = ""


# Sentinel used as the initial last_error before any attempt
_NO_ATTEMPTS_ERROR = "No attempts made"


class TaskExecutionError(RuntimeError):
    """A task exhausted its configured attempts or extraction strategies."""


def retry_wait(delay: float) -> None:
    """Retain the item slot while allowing interruption to wake retry backoff."""
    cancellation = current_cancellation.get()
    if cancellation is None:
        time.sleep(delay)
    else:
        cancellation.stopped.wait(delay)
    check_cancelled()


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
    extracted: Any | None = None
    structured_value: dict[str, Any] | list[Any] | None = None
    escalation_notified = False
    last_used_provider_name = config.provider_name
    active_model: str | None = None
    validation_feedback: str | None = None

    try:
        for attempt in range(config.max_retries + 1):
            check_cancelled()
            if attempt > 0:
                delay = min(2 ** (attempt - 1), 30)
                retry_wait(delay)
                check_cancelled()

            if attempt > 0 and config.escalation is not None:
                active_provider = config.escalation.provider
                active_provider_name = config.escalation.provider_name
                active_model = config.escalation.model
                if not escalation_notified:
                    escalation_notified = True
                    if config.on_escalation_activated is not None:
                        config.on_escalation_activated()
            else:
                active_provider = config.provider
                active_provider_name = config.provider_name
                active_model = config.model

            last_used_provider_name = active_provider_name

            record_attempt()
            try:
                if active_provider_name == "system":
                    result = active_provider.execute(
                        prompt="",
                        model=active_model,
                        timeout=config.timeout_seconds,
                        command=config.command,
                        output_callback=config.stream_logger.on_stdout,
                        stderr_callback=config.stream_logger.on_stderr,
                        on_process_start=config.on_process_start,
                        summary_callback=config.summary_callback,
                    )
                else:
                    active_prompt = config.prompt
                    output_schema: Any | None = None
                    if config.structured_output is not None:
                        schema_document = config.structured_output.schema_document
                        if schema_document is None:
                            raise StructuredOutputValidationError(
                                "Structured output schema was not loaded"
                            )
                        output_schema = prepare_provider_schema(
                            schema_document,
                            allow_extra_fields=config.structured_output.allow_extra_fields,
                        )
                    if validation_feedback is not None:
                        active_prompt = (
                            f"{config.prompt}\n\n"
                            "Your previous response did not satisfy the required "
                            "structured output contract. Correct this validation "
                            f"error and return only the JSON value:\n{validation_feedback}"
                        )
                    if config.prompt_prefix.strip():
                        active_prompt = f"{config.prompt_prefix}\n\n{active_prompt}"
                    execute = active_provider.execute
                    if config.session_request is not None:
                        native_execute = getattr(
                            active_provider, "execute_with_session", None
                        )
                        if not callable(native_execute):
                            raise ProviderSessionError(
                                f"State '{config.session_request.state_name}': provider lacks native session support"
                            )
                        from functools import partial

                        execute = partial(native_execute, config.session_request)
                    result = execute(
                        prompt=active_prompt,
                        model=active_model,
                        timeout=config.timeout_seconds,
                        output_callback=config.stream_logger.on_stdout,
                        stderr_callback=config.stream_logger.on_stderr,
                        on_process_start=config.on_process_start,
                        summary_callback=config.summary_callback,
                        output_schema=output_schema,
                    )
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                last_error = str(exc).strip() or (
                    f"Provider {active_provider_name} timed out ({type(exc).__name__})"
                )
                config.stream_logger.on_stderr(last_error)
                result = ProviderResult(exit_code=1, stdout="", stderr=last_error)
                continue

            check_cancelled()
            if result.exit_code == 0:
                if config.structured_output is not None:
                    schema_document = config.structured_output.schema_document
                    if schema_document is None:
                        raise StructuredOutputValidationError(
                            "Structured output schema was not loaded"
                        )
                    validator = create_structured_output_validator(
                        schema_document,
                        allow_extra_fields=config.structured_output.allow_extra_fields,
                    )
                    candidates = (
                        [result.final_message]
                        if result.final_message is not None
                        else []
                    )
                    if result.stdout != result.final_message:
                        candidates.append(result.stdout)
                    # The final message is the authoritative response. Keep its
                    # actionable error if the compatibility fallback also fails.
                    primary_error: StructuredOutputValidationError | None = None
                    for candidate in candidates:
                        try:
                            structured_value = parse_structured_output(
                                candidate, validator
                            )
                        except StructuredOutputValidationError as exc:
                            if primary_error is None:
                                primary_error = exc
                            continue
                        break
                    if structured_value is None:
                        if primary_error is None:
                            primary_error = StructuredOutputValidationError(
                                "Provider returned no structured output candidate"
                            )
                        last_error = str(primary_error)[:1000]
                        validation_feedback = last_error
                        if active_provider_name == "system":
                            break
                        continue
                    break
                if config.extract:
                    extracted = extract_value(
                        result.stdout.strip(),
                        config.extract,
                        get_provider,
                        source_provider=active_provider_name,
                        resolved_fallback=config.resolved_fallback,
                        flow_profiles=config.flow_profiles,
                        config_profiles=config.config_profiles,
                        on_fallback=config.on_fallback,
                    )
                    if extracted is not None:
                        break
                    last_error = "Extraction failed: all strategies returned None"
                    if active_provider_name == "system":
                        break
                else:
                    break
            else:
                last_error = result.stderr.strip() or (
                    f"Provider {active_provider_name} exited with exit code "
                    f"{result.exit_code} without an error message on stderr"
                )
                config.stream_logger.on_stderr(last_error)
    finally:
        config.stream_logger.close()

    check_cancelled()
    return ExecutionResult(
        result=result,
        extracted=extracted,
        structured_value=structured_value,
        last_error=last_error,
        last_provider_name=last_used_provider_name,
    )


def execute_internal_task(
    config: ExecutionConfig,
    outer_state: dict[str, Any],
    state_name: str,
    fork_from: str | None,
) -> ExecutionResult:
    """Use outer source metadata and preserve internal-task failure policies."""
    try:
        if fork_from is not None:
            references = outer_state.get("_session_references", {})
            reference = (
                references.get(fork_from) if isinstance(references, dict) else None
            )
            if (
                not isinstance(reference, dict)
                or reference.get("provider") != config.provider_name
            ):
                raise ProviderSessionError(
                    f"State '{state_name}': missing native session reference for '{fork_from}'; rerun the source task (older checkpoints cannot reconstruct history)"
                )
            config.session_request = SessionRequest(
                state_name=state_name, source=reference
            )
        return execute_with_retry(config)
    except ProviderSessionError as exc:
        structlog.get_logger(__name__).error(
            "internal_task_session_failed", state=state_name, error=str(exc)
        )
        return ExecutionResult(
            result=ProviderResult(1, "", str(exc)), extracted=None, last_error=str(exc)
        )

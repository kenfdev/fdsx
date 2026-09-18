"""Jev's SDK adapter. The shared evaluator exclusively owns network retries."""

import json
import subprocess  # nosec B404 - process callback type annotations only.
from collections.abc import Callable
from typing import Any

import structlog

from fdsx.core.evaluation import EvaluationError, evaluate
from fdsx.core.evaluation_schema import compile_evaluation_output
from fdsx.providers.base import ProviderResult

log = structlog.get_logger(__name__)


class JevProvider:
    def __init__(self, location: str = "jev") -> None:
        self.location = location

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout: int | None = None,
        command: str | None = None,
        output_callback: Callable[[str], None] | None = None,
        stderr_callback: Callable[[str], None] | None = None,
        on_process_start: Callable[[subprocess.Popen[str]], None] | None = None,
        summary_callback: Callable[[str], None] | None = None,
        output_schema: Any | None = None,
    ) -> ProviderResult:
        if timeout is not None or command is not None:
            log.error("evaluation_options_invalid", location=self.location)
            raise EvaluationError(
                f"Evaluation {self.location}: unsupported task option"
            )
        output = compile_evaluation_output(output_schema, location=self.location)
        result = evaluate(
            {"prompt": prompt},
            output.questions,
            model=model or "jev-1.13.0",
            location=self.location,
        )
        # No streaming callbacks: neither inputs nor SDK responses belong in logs.
        return ProviderResult(
            0, json.dumps(output.project(result)), "", evaluation=result
        )

"""Shared Jev boundary. No graph transitions, checkpoints, or provider fallback."""

import hashlib
import json
import logging
import math
import os
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, TypedDict, cast

import structlog

from fdsx.models.evaluation import EvaluationQuestion

log = structlog.get_logger(__name__)
_evaluating: ContextVar[bool] = ContextVar("fdsx_evaluating", default=False)


class EvaluationError(RuntimeError):
    """Safe evaluation failure; never contains input or SDK exception text."""


class _SuppressEvaluationLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _evaluating.get()


class ChoiceResult(TypedDict):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float


class NoulResult(TypedDict):
    type: Literal["noul"]
    noul: float


class ScoreResult(TypedDict):
    type: Literal["score"]
    score: float
    legend: dict[str, str]
    probabilities: dict[str, float]
    confidence: float


EvaluationAnswer = ChoiceResult | NoulResult | ScoreResult


@dataclass(frozen=True)
class EvaluationResult:
    requested_model: str
    reported_model: str
    answers: dict[str, EvaluationAnswer]
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": {
                "requested": self.requested_model,
                "reported": self.reported_model,
            },
            "answers": self.answers,
        }


def _invalid(location: str, reason: str) -> EvaluationError:
    log.error("evaluation_failed", location=location, reason=reason)
    return EvaluationError(f"Evaluation {location}: {reason}")


def _request_failed(
    location: str, error: Exception, *, private_payload: str = ""
) -> EvaluationError:
    """Select diagnostic fields, never format the exception or full response body."""
    from typesafe_sdk import (
        TypeSafeAPIConnectionError,
        TypeSafeAPIError,
        TypeSafeAPIResponseValidationError,
        TypeSafeAPITimeoutError,
        TypeSafeAuthenticationError,
        TypeSafeBadRequestError,
        TypeSafeError,
        TypeSafeInternalServerError,
        TypeSafeNotFoundError,
        TypeSafePermissionDeniedError,
        TypeSafeRateLimitError,
        TypeSafeUnprocessableEntityError,
    )

    # Subclasses must precede their parents, especially validation (an API error)
    # and timeout (a connection error). Labels come from trusted classes, never
    # from an arbitrary exception's class name, str, repr, cause or traceback.
    known = (
        (TypeSafeAPIResponseValidationError, "response_validation"),
        (TypeSafeAPITimeoutError, "timeout"),
        (TypeSafeAPIConnectionError, "connection"),
        (TypeSafeBadRequestError, "http"),
        (TypeSafeAuthenticationError, "http"),
        (TypeSafePermissionDeniedError, "http"),
        (TypeSafeNotFoundError, "http"),
        (TypeSafeUnprocessableEntityError, "http"),
        (TypeSafeRateLimitError, "http"),
        (TypeSafeInternalServerError, "http"),
        (TypeSafeAPIError, "http"),
        (UnicodeEncodeError, "encoding"),
        (UnicodeDecodeError, "encoding"),
        (UnicodeTranslateError, "encoding"),
        (UnicodeError, "encoding"),
        (TypeSafeError, "sdk"),
    )
    category, exception_type = next(
        (category, cls.__name__) for cls, category in known if isinstance(error, cls)
    )
    details: dict[str, str | int] = {
        "category": category,
        "exception_type": exception_type,
    }
    if isinstance(error, TypeSafeAPIError):
        if type(error.status) is int and 100 <= error.status <= 599:
            details["http_status"] = error.status
        request_id = error.request_id
        if (
            type(request_id) is str
            and 1 <= len(request_id) <= 128
            and re.fullmatch(r"[A-Za-z0-9._-]+", request_id)
        ):
            # A syntactically valid header can still echo a credential/material.
            # Retain only a stable fingerprint, not the externally supplied ID.
            details["request_id_sha256"] = hashlib.sha256(
                request_id.encode("ascii")
            ).hexdigest()
        body = error.body
        detail = body.get("detail") if isinstance(body, dict) else None
        error_type = detail.get("error_type") if isinstance(detail, dict) else None
        # Accept new service codes without a fixed vocabulary. Do not copy free
        # text, nested data, terminal controls, or echoed input/credentials.
        api_key = os.environ.get("TYPESAFE_API_KEY", "")
        if (
            type(error_type) is str
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", error_type)
            and error_type not in private_payload
            and not (api_key and api_key in error_type)
        ):
            details["error_type"] = error_type
            if error_type == "max_tokens_exceeded":
                details["guidance"] = (
                    "input limit exceeded; retrying the same input will not resolve "
                    "this error; revise the materials or questions before resuming"
                )
    log.error(
        "evaluation_failed", location=location, reason="Jev request failed", **details
    )
    diagnostic = ", ".join(f"{key}={value}" for key, value in details.items())
    return EvaluationError(f"Evaluation {location}: Jev request failed ({diagnostic})")


def _json_value(value: Any, location: str, ancestors: set[int]) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (list, dict):
        raise _invalid(location, "material must contain only finite JSON values")
    if id(value) in ancestors:
        raise _invalid(location, "cyclic material")
    ancestors.add(id(value))
    try:
        if isinstance(value, dict):
            if any(type(key) is not str for key in value):
                raise _invalid(location, "material object keys must be strings")
            children = value.values()
        else:
            children = value
        # Report the declared material location, never keys from private content.
        for child in children:
            _json_value(child, location, ancestors)
    finally:
        ancestors.remove(id(value))


def encode_materials(materials: dict[str, Any], location: str) -> str:
    if not materials:
        raise _invalid(location, "at least one material is required")
    for name, value in materials.items():
        place = f"{location}.input.{name}"
        if (
            value is None
            or (isinstance(value, str) and not value.strip())
            or (isinstance(value, (dict, list)) and not value)
        ):
            raise _invalid(place, "required material is empty")
        try:
            _json_value(value, place, set())
        except RecursionError:
            raise _invalid(place, "material nesting is too deep") from None
    try:
        encoded = json.dumps(materials, allow_nan=False, ensure_ascii=False)
        # Lone surrogates need JSON escapes to survive UTF-8 transport; valid
        # Unicode (including Japanese and emoji) stays unchanged.
        return encoded.encode("utf-8", errors="backslashreplace").decode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise _invalid(location, "materials cannot be encoded as JSON") from None


def _number(value: Any, maximum: float, location: str) -> float:
    if (
        type(value) not in (int, float)
        or not 0 <= value <= maximum
        or not math.isfinite(value)
    ):
        raise _invalid(location, "invalid numeric answer")
    return float(value)


def _distribution(value: Any, keys: set[Any], location: str) -> dict[Any, float]:
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or any(type(k) not in {type(expected) for expected in keys} for k in value)
    ):
        raise _invalid(location, "probability keys do not match criteria")
    result = {
        key: _number(probability, 1, location) for key, probability in value.items()
    }
    return result


def evaluate(
    materials: dict[str, Any],
    questions: dict[str, EvaluationQuestion],
    *,
    model: str = "jev-1.13.0",
    location: str,
) -> EvaluationResult:
    """Evaluate all questions atomically; SDK types never leave this boundary."""
    from typesafe_sdk import (
        ChoiceAnswer,
        NoulAnswer,
        Question,
        RetryPolicy,
        ScoreAnswer,
        TypeSafeClient,
        TypeSafeError,
    )

    state = encode_materials(materials, location)
    sdk_log = logging.getLogger("typesafe_sdk")
    if not any(isinstance(f, _SuppressEvaluationLogs) for f in sdk_log.filters):
        sdk_log.addFilter(_SuppressEvaluationLogs())
    token = _evaluating.set(True)
    try:
        try:
            with TypeSafeClient(
                model=model,
                base_url="https://api.typesafe.ai",
                timeout=30,
                retry=RetryPolicy(
                    max_retries=2,
                    backoff_initial=1,
                    backoff_max=2,
                    backoff_jitter=0,
                    timeout=60,
                ),
            ) as client:
                response = client.system_one(
                    state=state,
                    questions={
                        name: cast(Question, question.model_dump(exclude_none=True))
                        for name, question in questions.items()
                    },
                    model=model,
                )
        except (TypeSafeError, UnicodeError) as error:
            raise _request_failed(
                location,
                error,
                private_payload=state
                + json.dumps(
                    {
                        name: question.model_dump()
                        for name, question in questions.items()
                    },
                    ensure_ascii=False,
                ),
            ) from None
        try:
            raw_response = response.raw_http_response
        except TypeSafeError:
            raw_response = (
                None  # Supports SDK typed responses supplied by callers/tests.
            )
        if raw_response is not None:
            try:
                raw_answers = raw_response.json()["answers"]
            except (ValueError, KeyError, TypeError):
                raise _invalid(location, "invalid answer envelope") from None
            if not isinstance(raw_answers, dict) or set(raw_answers) != set(questions):
                raise _invalid(location, "answer names do not match questions")
        if set(response.answers) != set(questions):
            raise _invalid(location, "answer names do not match questions")
        answers: dict[str, EvaluationAnswer] = {}
        for name, question in questions.items():
            answer = response.answers[name]
            place = f"{location}.questions.{name}"
            if question.type == "noul" and isinstance(answer, NoulAnswer):
                answers[name] = {"type": "noul", "noul": _number(answer.noul, 1, place)}
            elif (
                question.type == "choice"
                and isinstance(answer, ChoiceAnswer)
                and isinstance(question.criteria, dict)
            ):
                probabilities = _distribution(
                    answer.probabilities, set(question.criteria), place
                )
                if answer.choice not in probabilities or probabilities[
                    answer.choice
                ] != max(probabilities.values()):
                    raise _invalid(
                        place, "choice must be a maximum-probability candidate"
                    )
                answers[name] = {
                    "type": "choice",
                    "choice": answer.choice,
                    "probabilities": probabilities,
                    "confidence": _number(answer.confidence, 1, place),
                }
            elif (
                question.type == "score"
                and isinstance(answer, ScoreAnswer)
                and isinstance(question.criteria, list)
            ):
                legend = dict(enumerate(question.criteria))
                if answer.legend != legend or any(
                    type(k) is not int for k in answer.legend
                ):
                    raise _invalid(place, "score legend does not match criteria")
                probabilities = _distribution(answer.probabilities, set(legend), place)
                score = _number(answer.score, len(legend) - 1, place)
                expected = sum(
                    key * probability for key, probability in probabilities.items()
                )
                if abs(score - expected) > 1e-6 * max(1, len(legend) - 1):
                    raise _invalid(place, "score does not match its distribution")
                answers[name] = {
                    "type": "score",
                    "score": score,
                    "legend": {str(key): value for key, value in legend.items()},
                    "probabilities": {
                        str(key): value for key, value in probabilities.items()
                    },
                    "confidence": _number(answer.confidence, 1, place),
                }
            else:
                raise _invalid(place, "answer type does not match question")
        # Missing service measurements remain unknown, not zero or estimates.
        usage = response.usage
        return EvaluationResult(
            model,
            response.model,
            answers,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
    finally:
        _evaluating.reset(token)

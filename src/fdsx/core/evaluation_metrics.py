"""Allowlisted, unvalidated numeric diagnostics from a Jev response."""

import json
import math
from typing import Any

from fdsx.models.evaluation import EvaluationQuestion


def numeric_metrics(
    raw: Any, questions: dict[str, EvaluationQuestion]
) -> dict[str, Any]:
    def number(value: Any) -> dict[str, Any]:
        if type(value) not in (int, float):
            return {"availability": "unavailable"}
        if isinstance(value, float) and not math.isfinite(value):
            return {"value": "nonfinite", "valid_range": False}
        # Bound hostile integer representations; never stringify arbitrary objects.
        if abs(value) > 1e100:
            return {"value": "out_of_range", "valid_range": False}
        return {"value": value, "valid_range": 0 <= value <= 1}

    if type(raw) is str and len(raw) <= 1024 * 1024:
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError):
            raw = None
    answers = raw.get("answers") if type(raw) is dict else None
    found: dict[str, Any] = {}
    if type(answers) is dict:
        for name, question in questions.items():
            answer = answers.get(name)
            if type(answer) is not dict or not isinstance(question.criteria, dict):
                continue
            probabilities = answer.get("probabilities")
            found[name] = {
                "confidence": number(answer.get("confidence")),
                "probabilities": {
                    candidate: number(probabilities.get(candidate))
                    for candidate in question.criteria
                }
                if type(probabilities) is dict
                else {},
            }
    return {
        "validated": False,
        "availability": "available" if found else "unavailable",
        "questions": found,
    }

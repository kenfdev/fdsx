"""Parsing and validation for provider-independent structured output."""

import json
from typing import Any

from jsonschema import ValidationError
from jsonschema.protocols import Validator


class StructuredOutputValidationError(Exception):
    """Provider output could not satisfy its structured-output contract."""


def parse_structured_output(
    stdout: str, validator: Validator
) -> dict[str, Any] | list[Any]:
    """Parse complete stdout as one object/list and validate it."""
    text = stdout.strip()
    if text.startswith("```") and text.endswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 : -3].strip()

    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputValidationError(
            f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(value, (dict, list)):
        raise StructuredOutputValidationError(
            "Structured output must be a JSON object or list"
        )

    try:
        validator.validate(value)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        prefix = f" at {location}" if location else ""
        raise StructuredOutputValidationError(
            f"JSON Schema validation failed{prefix}: {exc.message}"
        ) from exc
    return value


def upsert_structured_items(
    existing: Any, updates: Any, key: str
) -> list[dict[str, Any]]:
    """Replace existing keyed objects in place and append new keyed objects."""
    if existing is None:
        existing = []
    if not isinstance(existing, list) or not all(
        isinstance(item, dict) for item in existing
    ):
        raise StructuredOutputValidationError(
            "Existing merge state must be a list of objects"
        )
    if not isinstance(updates, list) or not all(
        isinstance(item, dict) for item in updates
    ):
        raise StructuredOutputValidationError(
            "Upsert structured output must be a list of objects"
        )

    seen: list[Any] = []
    for item in updates:
        if key not in item:
            raise StructuredOutputValidationError(f"Item is missing merge key '{key}'")
        item_key = item[key]
        if item_key in seen:
            raise StructuredOutputValidationError(
                f"Update batch contains duplicate merge key '{item_key}'"
            )
        seen.append(item_key)

    merged = [dict(item) for item in existing]
    for update in updates:
        update_key = update[key]
        for index, current in enumerate(merged):
            if key not in current:
                raise StructuredOutputValidationError(
                    f"Existing item is missing merge key '{key}'"
                )
            if current[key] == update_key:
                merged[index] = dict(update)
                break
        else:
            merged.append(dict(update))
    return merged

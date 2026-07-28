"""Unit tests for structured-output parsing and schema policy."""

import json
from typing import Any

import pytest

from fdsx.core.structured_output import (
    StructuredOutputValidationError,
    create_structured_output_validator,
    parse_structured_output,
)

SCHEMA = {
    "type": "object",
    "required": ["status", "message", "attempt"],
    "properties": {
        "status": {"enum": ["COMMITTED", "NO_CHANGES", "BLOCKED"]},
        "message": {"type": "string", "pattern": "^test:"},
        "attempt": {"type": "integer", "minimum": 1},
    },
    "additionalProperties": False,
}


def _parse(value: Any, *, allow_extra_fields: bool = True) -> Any:
    validator = create_structured_output_validator(
        SCHEMA, allow_extra_fields=allow_extra_fields
    )
    return parse_structured_output(json.dumps(value), validator)


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (
            {"message": "test: commit", "attempt": 1, "notes": "extra"},
            "'status' is a required property",
        ),
        (
            {
                "status": "DONE",
                "message": "test: commit",
                "attempt": 1,
                "notes": "extra",
            },
            "'DONE' is not one of",
        ),
        (
            {
                "status": "COMMITTED",
                "message": 7,
                "attempt": 1,
                "notes": "extra",
            },
            "is not of type 'string'",
        ),
        (
            {
                "status": "COMMITTED",
                "message": "feat: commit",
                "attempt": 1,
                "notes": "extra",
            },
            "does not match",
        ),
        (
            {
                "status": "COMMITTED",
                "message": "test: commit",
                "attempt": 0,
                "notes": "extra",
            },
            "is less than the minimum of 1",
        ),
    ],
)
def test_allow_extra_fields_preserves_other_schema_constraints(
    value: Any, error: str
) -> None:
    with pytest.raises(StructuredOutputValidationError, match=error):
        _parse(value)


def test_allow_extra_fields_preserves_additional_property_schemas() -> None:
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "additionalProperties": {"type": "string"},
    }
    validator = create_structured_output_validator(schema, allow_extra_fields=True)

    with pytest.raises(StructuredOutputValidationError, match="is not of type"):
        parse_structured_output('{"status": "ok", "attempt": 1}', validator)


def test_allow_extra_fields_applies_inside_composite_schemas() -> None:
    schema = {
        "oneOf": [
            {
                "type": "object",
                "required": ["kind", "value"],
                "properties": {
                    "kind": {"const": "text"},
                    "value": {"type": "string"},
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["kind", "value"],
                "properties": {
                    "kind": {"const": "count"},
                    "value": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        ]
    }
    validator = create_structured_output_validator(schema, allow_extra_fields=True)

    value = parse_structured_output(
        '{"kind": "text", "value": "done", "notes": "extra"}', validator
    )

    assert value == {"kind": "text", "value": "done", "notes": "extra"}


def test_allow_extra_fields_supports_unevaluated_properties() -> None:
    schema = {
        "type": "object",
        "required": ["status"],
        "properties": {"status": {"type": "string"}},
        "unevaluatedProperties": False,
    }
    validator = create_structured_output_validator(schema, allow_extra_fields=True)

    value = parse_structured_output(
        '{"status": "COMMITTED", "notes": "extra"}', validator
    )

    assert value == {"status": "COMMITTED", "notes": "extra"}


def test_allow_extra_fields_does_not_relax_json_parsing() -> None:
    validator = create_structured_output_validator(SCHEMA, allow_extra_fields=True)

    with pytest.raises(StructuredOutputValidationError, match="Invalid JSON"):
        parse_structured_output("not json", validator)


def test_allow_extra_fields_does_not_accept_scalar_output() -> None:
    validator = create_structured_output_validator(SCHEMA, allow_extra_fields=True)

    with pytest.raises(
        StructuredOutputValidationError,
        match="Structured output must be a JSON object or list",
    ):
        parse_structured_output('"COMMITTED"', validator)


def test_extra_fields_are_rejected_when_disabled() -> None:
    with pytest.raises(
        StructuredOutputValidationError, match="Additional properties are not allowed"
    ):
        _parse(
            {
                "status": "COMMITTED",
                "message": "test: commit",
                "attempt": 1,
                "notes": "extra",
            },
            allow_extra_fields=False,
        )

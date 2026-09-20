"""Strict author-facing evaluation contracts, independent of the SDK."""

import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_serializer,
    model_validator,
)

NAME = r"[A-Za-z_][A-Za-z0-9_]*"
PATH_FIELD = r"[^\W\d]\w*"
# Only concrete paths supported by the existing resolver. It cannot unescape
# quoted keys or consume a closing bracket inside one.
REF = re.compile(
    r"\$\."
    + PATH_FIELD
    + r"""(?:\."""
    + PATH_FIELD
    + r"""|\[(?:0|[1-9][0-9]*|"[^"\]\\]*"|'[^'\]\\]*')\])*"""
)


class Material(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    literal: Any = None
    ref: str | None = None
    allow_empty: bool = True

    @model_serializer
    def serialize(self) -> dict[str, Any]:
        value: dict[str, Any] = (
            {"ref": self.ref} if self.ref is not None else {"literal": self.literal}
        )
        if not self.allow_empty:
            value["allow_empty"] = False
        return value

    @model_validator(mode="before")
    @classmethod
    def exclusive(cls, value: Any) -> Any:
        if not isinstance(value, dict) or len(set(value) & {"literal", "ref"}) != 1:
            raise ValueError("specify exactly one of literal or ref")
        if "ref" in value and (
            not isinstance(value["ref"], str) or not REF.fullmatch(value["ref"])
        ):
            raise ValueError("ref must be a concrete JSONPath")
        if "ref" in value:
            for quoted in re.finditer(r"""\[("[^"]*"|'[^']*')\]""", value["ref"]):
                key = quoted.group(1)[1:-1]
                if key != key.strip("\"'"):
                    raise ValueError(
                        "ref contains a quoted key unsupported by the resolver"
                    )
        return value


class EvaluationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["choice", "noul", "score"]
    instructions: str
    criteria: dict[str, str] | list[str] | None = None

    @field_validator("instructions")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instructions must not be blank")
        return value

    @model_validator(mode="after")
    def rubric(self) -> "EvaluationQuestion":
        criteria = self.criteria
        if self.type == "choice":
            if not isinstance(criteria, dict) or len(criteria) < 2:
                raise ValueError("choice criteria require at least two candidates")
        elif self.type == "score":
            if not isinstance(criteria, list) or len(criteria) < 2:
                raise ValueError("score criteria require at least two levels")
        elif criteria is not None and (
            not isinstance(criteria, dict) or not set(criteria) <= {"true", "false"}
        ):
            raise ValueError('noul criteria allow only string keys "true" and "false"')
        if isinstance(criteria, dict):
            if any(not key.strip() for key in criteria):
                raise ValueError("criteria names must not be blank")
            descriptions = list(criteria.values())
        else:
            descriptions = criteria or []
        if any(not value.strip() for value in descriptions):
            raise ValueError("criteria descriptions must not be blank")
        return self


class EvaluationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["evaluate"] = "evaluate"
    evaluator: Literal["jev"]
    input: dict[str, Material]
    questions: dict[str, EvaluationQuestion]
    result_path: str
    model: str = "jev-1.13.0"
    next: str | None = None
    end: Literal[True] | None = None

    @field_validator("input", "questions")
    @classmethod
    def named_nonempty(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value or any(re.fullmatch(NAME, key) is None for key in value):
            raise ValueError("requires named entries matching [A-Za-z_][A-Za-z0-9_]*")
        return value

    @field_validator("model", "next")
    @classmethod
    def nonempty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("end", mode="before")
    @classmethod
    def true_only(cls, value: Any) -> Any:
        if value is not None and value is not True:
            raise ValueError("end must be true")
        return value

    @field_validator("result_path")
    @classmethod
    def destination(cls, value: str) -> str:
        if re.fullmatch(r"\$\.[^\s.\[\]*$]+", value) is None:
            raise ValueError("result_path must name one top-level key")
        if value[2:].startswith(("_meta", "__", "_br_", "_state_")) or value[2:] in {
            "_session_references",
            "remaining_steps",
            "run_path",
            "state",
        }:
            raise ValueError("result_path must not overwrite an internal key")
        return value

    @model_validator(mode="after")
    def transition(self) -> "EvaluationDefinition":
        if (self.next is None) == (self.end is None):
            raise ValueError("specify exactly one of next or end: true")
        return self

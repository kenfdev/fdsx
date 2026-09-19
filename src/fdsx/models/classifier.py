"""Declarations for a single Jev choice with threshold-based LLM fallback."""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fdsx.models.evaluation import EvaluationDefinition, EvaluationQuestion, Material
from fdsx.models.validators import validate_llm_provider

Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]


class Acceptance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    probability: Probability | None = None
    probability_by_choice: dict[str, Probability] = Field(default_factory=dict)
    confidence: Probability | None = None
    mode: Literal["all", "any"] = "all"

    def has_thresholds(self) -> bool:
        return (
            self.probability is not None
            or self.confidence is not None
            or bool(self.probability_by_choice)
        )


class ClassifierFallback(BaseModel):
    """Profiles are resolved by the loader before this model is constructed."""

    model_config = ConfigDict(extra="forbid", strict=True)
    provider: str
    model: str = Field(min_length=1)
    provider_options: dict[str, Any] | None = None
    timeout_seconds: int = Field(default=1800, gt=0)
    inactivity_timeout: int = Field(default=300, ge=0)
    include_jev_result: bool = False

    @model_validator(mode="after")
    def llm(self) -> "ClassifierFallback":
        validate_llm_provider(self.provider, "classifier fallback")
        if not self.model.strip():
            raise ValueError("fallback model must not be blank")
        return self


class ClassifierDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["classifier"] = "classifier"
    evaluator: Literal["jev"] = "jev"
    model: str = "jev-1.13.0"
    input: dict[str, Material]
    question: EvaluationQuestion
    acceptance: Acceptance = Field(default_factory=Acceptance)
    fallback: ClassifierFallback | None = None
    record_full_input: bool = False
    result_path: str

    @model_validator(mode="after")
    def contract(self) -> "ClassifierDefinition":
        EvaluationDefinition.named_nonempty(self.input)
        EvaluationDefinition.destination(self.result_path)
        if not self.model.strip():
            raise ValueError("model must not be blank")
        if self.question.type != "choice":
            raise ValueError("classifier requires one choice question")
        criteria = self.question.criteria
        if not isinstance(criteria, dict):
            raise ValueError("classifier requires candidate criteria")
        if any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", candidate) is None
            for candidate in criteria
        ):
            raise ValueError(
                "classifier candidates require ASCII identifiers of at most 128 characters"
            )
        if set(self.acceptance.probability_by_choice) - set(criteria):
            raise ValueError("probability_by_choice contains unknown candidates")
        if self.acceptance.has_thresholds() and self.fallback is None:
            raise ValueError("classifier thresholds require fallback")
        return self

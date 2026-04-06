from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from fdsx.models.validators import validate_llm_provider

PROFILE_NAMES = frozenset({"smarty", "doer", "specialist", "generalist", "behemoth"})


class TemplateInfo(BaseModel):
    """Template information for init discovery."""

    name: str = Field(..., description="Template name, e.g. 'linear-basic'")
    path: Path = Field(..., description="Absolute path to template directory")
    source: Literal["builtin", "user"] = Field(
        ..., description="Origin of the template"
    )


class ProviderSelection(BaseModel):
    """Provider and model selected during init."""

    provider: str = Field(..., description="LLM provider name")
    model: str = Field(..., description="Model name")

    @model_validator(mode="after")
    def validate_provider(self) -> "ProviderSelection":
        validate_llm_provider(self.provider, "ProviderSelection")
        return self


class InitConfig(BaseModel):
    """Configuration produced during init."""

    providers: list[ProviderSelection] = Field(
        ..., min_length=1, description="Selected providers and models"
    )
    templates: list[TemplateInfo] = Field(
        default_factory=list, description="Available templates"
    )
    profile_assignments: dict[str, ProviderSelection] = Field(
        ...,
        description="Mapping of profile names to provider selections",
    )

    @model_validator(mode="after")
    def validate_profile_assignments(self) -> "InitConfig":
        assignment_keys = frozenset(self.profile_assignments.keys())
        if assignment_keys == PROFILE_NAMES:
            return self
        missing = PROFILE_NAMES - assignment_keys
        extra = assignment_keys - PROFILE_NAMES
        parts = []
        if missing:
            parts.append(f"missing: {missing}")
        if extra:
            parts.append(f"invalid: {extra}")
        raise ValueError(
            f"profile_assignments must contain exactly {PROFILE_NAMES}. "
            + ", ".join(parts)
        )


class ScaffoldResult(BaseModel):
    """Result from scaffold() operation."""

    created: list[str] = Field(
        default_factory=list, description="Relative paths of created files"
    )
    skipped_config: bool = Field(
        default=False, description="True if config.yaml was skipped (already existed)"
    )
    skipped_workflows: list[str] = Field(
        default_factory=list,
        description="Workflow names that were skipped (conflicted)",
    )

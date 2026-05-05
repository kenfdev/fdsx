from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fdsx.core.paths import parse_jsonpath
from fdsx.models.validators import validate_llm_provider


class LLMClassifyFallback(BaseModel):
    """LLM-based classification fallback."""

    type: Literal["llm_classify"] = "llm_classify"
    provider: str = Field(..., description="LLM provider")
    prompt: str = Field(..., description="Classification prompt")

    @model_validator(mode="after")
    def validate_provider(self) -> "LLMClassifyFallback":
        validate_llm_provider(self.provider, "LLM classify fallback")
        return self


class ExtractionFallback(BaseModel):
    """Global extraction fallback: retry extraction with a different provider or profile."""

    provider: str | None = Field(default=None)
    profile: str | None = Field(default=None)
    extra_instructions: str | None = Field(default=None)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def validate_provider_xor_profile(cls, values: Any) -> Any:
        if isinstance(values, dict):
            has_provider = values.get("provider") is not None
            has_profile = values.get("profile") is not None
            if has_provider and has_profile:
                raise ValueError(
                    "provider and profile are mutually exclusive in ExtractionFallback"
                )
            if not has_provider and not has_profile:
                raise ValueError(
                    "exactly one of provider or profile must be set in ExtractionFallback"
                )
        return values

    @model_validator(mode="after")
    def validate_provider_name(self) -> "ExtractionFallback":
        if self.provider is not None:
            validate_llm_provider(self.provider, "ExtractionFallback")
        return self


class ExtractRule(BaseModel):
    """Output extraction configuration."""

    strategy: list[Literal["json", "regex", "keyword"]] = Field(
        ..., description="Extraction strategies tried in order"
    )
    pattern: str = Field(..., description="Pattern for extraction")
    fallback: LLMClassifyFallback | None = Field(
        default=None, description="LLM classification fallback"
    )
    result_path: str = Field(..., description="JSONPath for extracted value")

    @model_validator(mode="after")
    def validate_strategy_not_empty(self) -> "ExtractRule":
        if len(self.strategy) == 0:
            raise ValueError("strategy list must not be empty")
        return self


class WebhookConfig(BaseModel):
    """Webhook notification configuration."""

    url: str = Field(..., description="Webhook URL")
    template: str = Field(..., description="Message template with {variable} refs")

    @field_validator("url")
    @classmethod
    def validate_https(cls, v: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(v)
        if parsed.scheme == "https":
            return v
        if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
            return v
        raise ValueError(
            f"Webhook URL must use HTTPS (got {parsed.scheme}://). "
            "HTTP is only allowed for localhost."
        )


class NotifyConfig(BaseModel):
    """Notification configuration."""

    webhook: WebhookConfig = Field(..., description="Webhook configuration")


class HookEntry(BaseModel):
    """Single hook entry with a command and failure handling policy."""

    command: str = Field(..., min_length=1, description="Shell command to execute")
    on_failure: Literal["abort", "warn"] = Field(
        default="warn", description="Action on hook failure: abort or warn"
    )


class HookConfig(BaseModel):
    """Hook configuration for a state or flow."""

    on_state_start: list[HookEntry] = Field(
        default_factory=list, description="Hooks to run before execution"
    )
    on_state_end: list[HookEntry] = Field(
        default_factory=list, description="Hooks to run after execution"
    )
    on_workflow_start: list[HookEntry] = Field(
        default_factory=list, description="Hooks to run when the workflow starts"
    )
    on_workflow_end: list[HookEntry] = Field(
        default_factory=list, description="Hooks to run when the workflow ends"
    )

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_keys(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if (
            "on_start" in values
        ):  # legacy key name — detecting invalid input to reject it
            raise ValueError(
                "Hook key 'on_start' has been renamed to 'on_state_start'. "
                "Update the YAML file and retry."
            )
        if (
            "on_complete" in values
        ):  # legacy key name — detecting invalid input to reject it
            raise ValueError(
                "Hook key 'on_complete' has been renamed to 'on_state_end'. "
                "Update the YAML file and retry."
            )
        return values

    @model_validator(mode="before")
    @classmethod
    def reject_run_scope_keys(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if "on_run_start" in values:
            raise ValueError(
                "Hook key 'on_run_start' may only appear in global or project "
                "configuration, not in flow or state YAML."
            )
        if "on_run_end" in values:
            raise ValueError(
                "Hook key 'on_run_end' may only appear in global or project "
                "configuration, not in flow or state YAML."
            )
        return values


class StateHookConfig(BaseModel):
    """Hook configuration for a state block (workflow-scope keys are rejected)."""

    on_state_start: list[HookEntry] = Field(
        default_factory=list, description="Hooks to run before execution"
    )
    on_state_end: list[HookEntry] = Field(
        default_factory=list, description="Hooks to run after execution"
    )

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_keys(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if (
            "on_start" in values
        ):  # legacy key name — detecting invalid input to reject it
            raise ValueError(
                "Hook key 'on_start' has been renamed to 'on_state_start'. "
                "Update the YAML file and retry."
            )
        if (
            "on_complete" in values
        ):  # legacy key name — detecting invalid input to reject it
            raise ValueError(
                "Hook key 'on_complete' has been renamed to 'on_state_end'. "
                "Update the YAML file and retry."
            )
        return values

    @model_validator(mode="before")
    @classmethod
    def reject_workflow_scope_keys(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if "on_workflow_start" in values:
            raise ValueError(
                "Hook key 'on_workflow_start' is only valid at flow/project/global scope, "
                "not in state blocks."
            )
        if "on_workflow_end" in values:
            raise ValueError(
                "Hook key 'on_workflow_end' is only valid at flow/project/global scope, "
                "not in state blocks."
            )
        return values

    @model_validator(mode="before")
    @classmethod
    def reject_run_scope_keys(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        if "on_run_start" in values:
            raise ValueError(
                "Hook key 'on_run_start' may only appear in global or project "
                "configuration, not in flow or state YAML."
            )
        if "on_run_end" in values:
            raise ValueError(
                "Hook key 'on_run_end' may only appear in global or project "
                "configuration, not in flow or state YAML."
            )
        return values


class ChoiceRule(BaseModel):
    """Choice rule for branching."""

    variable: str = Field(..., description="JSONPath to compare")
    operator: Literal[
        "equals", "not_equals", "greater_than", "less_than", "contains"
    ] = Field(
        ...,
        description="Comparison operator",
    )
    value: Any = Field(..., description="Comparison value")
    next: str = Field(..., description="Target state name")


def _validate_provider_fields(
    provider: str,
    prompt_template: str | None,
    prompt_file: str | None,
    command: str | None,
    model: str | None,
) -> None:
    """Shared provider-field validation logic for TaskState and Branch."""
    valid_providers = {"claude", "opencode", "codex", "gemini", "system"}
    if provider not in valid_providers:
        raise ValueError(
            f"provider must be one of {', '.join(sorted(valid_providers))}, got '{provider}'"
        )
    if provider == "system":
        if prompt_template is not None:
            raise ValueError("provider=system forbids prompt_template")
        if prompt_file is not None:
            raise ValueError("provider=system forbids prompt_file")
        if model is not None:
            raise ValueError("provider=system forbids model")
        if command is None:
            raise ValueError("provider=system requires command")
    else:
        if command is not None:
            raise ValueError(f"provider={provider} forbids command")
        if model is None:
            raise ValueError(f"provider={provider} requires model")
        has_prompt = prompt_template is not None or prompt_file is not None
        if not has_prompt:
            raise ValueError(
                f"provider={provider} requires prompt_template or prompt_file"
            )


class ProfileConfig(BaseModel):
    """Named provider/model configuration bundle."""

    provider: str = Field(..., description="LLM provider")
    model: str = Field(..., description="Model name")
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_provider(self) -> "ProfileConfig":
        validate_llm_provider(self.provider, "Profile")
        return self


class EscalationConfig(BaseModel):
    """Workflow-level retry escalation: substitute a different provider on retries."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    provider_options: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "EscalationConfig":
        if self.provider is None:
            raise ValueError("retry_escalation: 'provider' is required")
        if self.model is None:
            raise ValueError(
                "retry_escalation: 'model' is required when 'provider' is set"
            )
        validate_llm_provider(self.provider, "retry_escalation")
        return self


class Branch(BaseModel):
    """Parallel branch definition."""

    provider: str = Field(..., description="Provider name")
    model: str | None = Field(
        default=None, description="Model name for non-system providers"
    )
    prompt_template: str | None = Field(
        default=None, description="Prompt template (exclusive with prompt_file)"
    )
    prompt_file: str | None = Field(
        default=None, description="Prompt file path (exclusive with prompt_template)"
    )
    command: str | None = Field(default=None, description="Command for system provider")
    extract: ExtractRule | None = Field(default=None, description="Output extraction")
    retry: int = Field(default=3, description="Retry count")
    timeout_seconds: int | None = Field(default=None, description="Timeout in seconds")
    provider_options: dict[str, Any] | None = Field(
        default=None, description="Per-branch provider option overrides"
    )

    @model_validator(mode="after")
    def validate_provider(self) -> "Branch":
        _validate_provider_fields(
            self.provider,
            self.prompt_template,
            self.prompt_file,
            self.command,
            self.model,
        )
        if self.prompt_template is not None and self.prompt_file is not None:
            raise ValueError("prompt_template and prompt_file are mutually exclusive")
        return self

    @model_validator(mode="after")
    def validate_extract_no_reserved_keys(self) -> "Branch":
        if self.extract is None:
            return self
        ep = self.extract.result_path
        if ep.startswith("$."):
            ep = ep[2:]
        parts = parse_jsonpath(ep)
        first_segment = parts[0] if parts else ""
        reserved = {"output", "exit_code", "error"}
        if isinstance(first_segment, str) and first_segment in reserved:
            raise ValueError(
                f"Branch extract.result_path '{self.extract.result_path}' "
                f"must not use reserved key '{first_segment}'"
            )
        return self


class AggregateRule(BaseModel):
    """Aggregation rule for parallel results."""

    source: str = Field(..., description="JSONPath to parallel results")
    field: str = Field(..., description="Field name to aggregate")
    strategy: str = Field(..., description="Aggregation strategy: majority|all|any")
    match: str = Field(..., description="Match value")
    no_match: str = Field(..., description="Non-match value")
    result_path: str = Field(..., description="JSONPath for result")


def _validate_result_file(v: str | None) -> str | None:
    """Validate that result_file uses a top-level '$.varname' path.

    Requirements:
    - Must start with '$.'
    - Must be a single top-level key (no dots or brackets after '$.')
    """
    if v is None:
        return v
    if not v.startswith("$."):
        raise ValueError(
            f"result_file must start with '$.' (got '{v}'). Example: '$.plan_ref'"
        )
    remainder = v[2:]
    if not remainder or not remainder.strip():
        raise ValueError(
            f"result_file must specify a variable name after '$.' (got '{v}'). "
            "Example: '$.plan_ref'"
        )
    if "." in remainder or "[" in remainder:
        raise ValueError(
            f"result_file must be a top-level variable path — nested paths are not allowed (got '{v}'). "
            "Example: '$.plan_ref'"
        )
    return v


class TaskState(BaseModel):
    """Task state - executes a provider to generate output."""

    type: Literal["task"] = "task"
    provider: str = Field(..., description="Provider: claude|opencode|codex|system")
    model: str | None = Field(default=None, description="Model name")
    prompt_template: str | None = Field(
        default=None, description="Prompt template (exclusive with prompt_file)"
    )
    prompt_file: str | None = Field(
        default=None, description="Prompt file path (exclusive with prompt_template)"
    )
    command: str | None = Field(default=None, description="Command for system provider")
    result_path: str | None = Field(default=None, description="JSONPath for result")
    result_file: str | None = Field(
        default=None,
        description="Top-level JSONPath variable to store the absolute path of a result file",
    )
    extract: ExtractRule | None = Field(default=None, description="Output extraction")
    max_iterations: int | None = Field(
        default=None, ge=1, description="Max times this state can be entered"
    )
    retry: int = Field(default=3, description="Retry count")
    timeout_seconds: int | None = Field(default=None, description="Timeout in seconds")
    provider_options: dict[str, Any] | None = Field(
        default=None, description="Per-task provider option overrides"
    )
    hooks: StateHookConfig | None = Field(
        default=None, description="Hook configuration"
    )
    next: str | None = Field(
        default=None, description="Next state (exclusive with end)"
    )
    end: bool | None = Field(default=None, description="End flow (exclusive with next)")

    @field_validator("result_file")
    @classmethod
    def validate_result_file(cls, v: str | None) -> str | None:
        return _validate_result_file(v)

    @model_validator(mode="after")
    def validate_provider_fields(self) -> "TaskState":
        _validate_provider_fields(
            self.provider,
            self.prompt_template,
            self.prompt_file,
            self.command,
            self.model,
        )
        return self

    @model_validator(mode="after")
    def validate_prompt_exclusive(self) -> "TaskState":
        if self.prompt_template is not None and self.prompt_file is not None:
            raise ValueError("prompt_template and prompt_file are mutually exclusive")
        return self

    @model_validator(mode="after")
    def validate_next_end_exclusive(self) -> "TaskState":
        if self.next is not None and self.end is not None:
            raise ValueError("next and end are mutually exclusive")
        return self

    @model_validator(mode="after")
    def validate_extract_path_no_overlap(self) -> "TaskState":
        if self.extract is None or self.result_path is None:
            return self
        rp = self.result_path
        if rp.startswith("$."):
            rp = rp[2:]
        ep = self.extract.result_path
        if ep.startswith("$."):
            ep = ep[2:]
        rp_parts = parse_jsonpath(rp)
        ep_parts = parse_jsonpath(ep)
        min_len = min(len(rp_parts), len(ep_parts))
        if rp_parts[:min_len] == ep_parts[:min_len]:
            raise ValueError(
                f"result_path '{self.result_path}' and extract.result_path "
                f"'{self.extract.result_path}' must not overlap"
            )
        return self


class ChoiceState(BaseModel):
    """Choice state - branching based on variable values."""

    type: Literal["choice"] = "choice"
    choices: list[ChoiceRule] = Field(..., description="Condition-transition pairs")
    default: str | None = Field(default=None, description="Fallback transition")
    max_iterations: int | None = Field(
        default=None, ge=1, description="Max times this state can be entered"
    )
    hooks: StateHookConfig | None = Field(
        default=None, description="Hook configuration"
    )


class ParallelState(BaseModel):
    """Parallel state - executes multiple branches concurrently."""

    type: Literal["parallel"] = "parallel"
    branches: list[Branch] = Field(..., description="Parallel branch definitions")
    result_path: str = Field(..., description="JSONPath for results array")
    result_file: str | None = Field(
        default=None,
        description="Top-level JSONPath variable to store the absolute path of a result file",
    )
    min_success: int | None = Field(default=None, description="Min successful branches")
    max_iterations: int | None = Field(
        default=None, ge=1, description="Max times this state can be entered"
    )
    hooks: StateHookConfig | None = Field(
        default=None, description="Hook configuration"
    )
    next: str | None = Field(
        default=None, description="Next state (exclusive with end)"
    )
    end: bool | None = Field(default=None, description="End flow (exclusive with next)")

    @field_validator("result_file")
    @classmethod
    def validate_result_file(cls, v: str | None) -> str | None:
        return _validate_result_file(v)

    @model_validator(mode="after")
    def validate_next_end_exclusive(self) -> "ParallelState":
        if self.next is not None and self.end is not None:
            raise ValueError("next and end are mutually exclusive")
        return self


class PassState(BaseModel):
    """Pass state - data transformation and aggregation."""

    type: Literal["pass"] = "pass"
    parameters: dict[str, Any] | None = Field(
        default=None, description="Variable transformation"
    )
    aggregate: AggregateRule | None = Field(
        default=None, description="Parallel result aggregation"
    )
    max_iterations: int | None = Field(
        default=None, ge=1, description="Max times this state can be entered"
    )
    hooks: HookConfig | None = Field(default=None, description="Hook configuration")
    next: str | None = Field(
        default=None, description="Next state (exclusive with end)"
    )
    end: bool | None = Field(default=None, description="End flow (exclusive with next)")

    @model_validator(mode="after")
    def validate_next_end_exclusive(self) -> "PassState":
        if self.next is not None and self.end is not None:
            raise ValueError("next and end are mutually exclusive")
        return self


class WaitState(BaseModel):
    """Wait state - human input or external trigger."""

    type: Literal["wait"] = "wait"
    mode: Literal["prompt"] = "prompt"
    message: str = Field(..., description="Terminal display message")
    choices: list[str] = Field(..., min_length=1, description="User selection options")
    result_path: str = Field(..., description="JSONPath for selection result")
    notify: NotifyConfig | None = Field(
        default=None, description="Webhook notification"
    )
    max_iterations: int | None = Field(
        default=None, ge=1, description="Max times this state can be entered"
    )
    hooks: StateHookConfig | None = Field(
        default=None, description="Hook configuration"
    )
    next: str | None = Field(
        default=None, description="Next state (exclusive with end)"
    )
    end: bool | None = Field(default=None, description="End flow (exclusive with next)")

    @model_validator(mode="after")
    def validate_next_end_exclusive(self) -> "WaitState":
        if self.next is not None and self.end is not None:
            raise ValueError("next and end are mutually exclusive")
        return self


class IteratorTaskState(BaseModel):
    """Task state for use inside iterator definitions."""

    type: Literal["task"] = "task"
    name: str = Field(..., description="State name within the iterator")
    provider: str = Field(..., description="Provider: claude|opencode|codex|system")
    model: str | None = Field(default=None, description="Model name")
    prompt_template: str | None = Field(
        default=None, description="Prompt template (exclusive with prompt_file)"
    )
    prompt_file: str | None = Field(
        default=None, description="Prompt file path (exclusive with prompt_template)"
    )
    command: str | None = Field(default=None, description="Command for system provider")
    result_path: str = Field(..., description="JSONPath for result")
    result_file: str | None = Field(
        default=None,
        description="Top-level JSONPath variable to store the absolute path of a result file",
    )
    extract: ExtractRule | None = Field(default=None, description="Output extraction")
    retry: int = Field(default=3, description="Retry count")
    timeout_seconds: int | None = Field(default=None, description="Timeout in seconds")
    provider_options: dict[str, Any] | None = Field(
        default=None, description="Per-task provider option overrides"
    )

    @field_validator("result_file")
    @classmethod
    def validate_result_file(cls, v: str | None) -> str | None:
        return _validate_result_file(v)

    @model_validator(mode="after")
    def validate_provider_fields(self) -> "IteratorTaskState":
        _validate_provider_fields(
            self.provider,
            self.prompt_template,
            self.prompt_file,
            self.command,
            self.model,
        )
        return self

    @model_validator(mode="after")
    def validate_prompt_exclusive(self) -> "IteratorTaskState":
        if self.prompt_template is not None and self.prompt_file is not None:
            raise ValueError("prompt_template and prompt_file are mutually exclusive")
        return self

    @model_validator(mode="after")
    def validate_extract_path_no_overlap(self) -> "IteratorTaskState":
        if self.extract is None:
            return self
        rp = self.result_path
        if rp.startswith("$."):
            rp = rp[2:]
        ep = self.extract.result_path
        if ep.startswith("$."):
            ep = ep[2:]
        rp_parts = parse_jsonpath(rp)
        ep_parts = parse_jsonpath(ep)
        min_len = min(len(rp_parts), len(ep_parts))
        if rp_parts[:min_len] == ep_parts[:min_len]:
            raise ValueError(
                f"result_path '{self.result_path}' and extract.result_path "
                f"'{self.extract.result_path}' must not overlap"
            )
        return self


class IteratorDef(BaseModel):
    """Iterator sub-workflow definition for Map state."""

    states: list[IteratorTaskState] = Field(
        ..., min_length=1, description="Ordered list of iterator states"
    )

    @model_validator(mode="before")
    @classmethod
    def reject_non_task_types(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        states = values.get("states", [])
        if not isinstance(states, list):
            return values
        for idx, state in enumerate(states):
            if isinstance(state, dict):
                state_type = state.get("type")
                if state_type is not None and state_type != "task":
                    raise ValueError(
                        f"iterator states must have type 'task', "
                        f"got type '{state_type}' at position {idx}"
                    )
        return values

    @model_validator(mode="after")
    def validate_unique_names(self) -> "IteratorDef":
        names = [s.name for s in self.states]
        if len(names) != len(set(names)):
            seen: set[str] = set()
            for name in names:
                if name in seen:
                    raise ValueError(f"duplicate iterator state name '{name}'")
                seen.add(name)
        return self


class MapState(BaseModel):
    """Map state - iterates over an array and executes a sub-workflow for each item."""

    type: Literal["map"] = "map"
    items_path: str = Field(..., description="JSONPath to input array")
    iterator: IteratorDef = Field(
        ..., description="Sub-workflow to execute for each item"
    )
    result_path: str = Field(..., description="JSONPath for results array")
    fail_fast: bool = Field(default=True, description="Stop on first failure")
    max_iterations: int | None = Field(
        default=None, ge=1, description="Max times this state can be entered"
    )
    hooks: StateHookConfig | None = Field(
        default=None, description="Hook configuration"
    )
    next: str | None = Field(
        default=None, description="Next state (exclusive with end)"
    )
    end: bool | None = Field(default=None, description="End flow (exclusive with next)")

    @model_validator(mode="after")
    def validate_next_end_exclusive(self) -> "MapState":
        if self.next is not None and self.end is not None:
            raise ValueError("next and end are mutually exclusive")
        return self


class FailState(BaseModel):
    """Fail state - terminates the flow with an error."""

    type: Literal["fail"] = "fail"
    error: str = Field(..., min_length=1, description="Error name")
    cause: str = Field(..., min_length=1, description="Error cause description")
    hooks: StateHookConfig | None = Field(
        default=None, description="Hook configuration"
    )

    @model_validator(mode="before")
    @classmethod
    def reject_disallowed_fields(cls, values: dict[str, Any]) -> dict[str, Any]:
        if "next" in values:
            raise ValueError("fail state cannot declare a successor; remove `next`")
        if "end" in values:
            raise ValueError("fail state cannot declare `end`; it terminates on entry")
        if "max_iterations" in values:
            raise ValueError(
                "fail state terminates on entry; `max_iterations` is meaningless"
            )
        return values


State = Annotated[
    TaskState
    | ChoiceState
    | ParallelState
    | PassState
    | WaitState
    | MapState
    | FailState,
    Field(discriminator="type"),
]


class Flow(BaseModel):
    """Top-level workflow definition."""

    name: str = Field(..., description="Flow name")
    description: str = Field(..., min_length=1, description="Flow description")
    start_at: str = Field(..., description="Initial state name")
    states: dict[str, State] = Field(..., description="State definitions keyed by name")
    version: str | None = Field(default=None, description="Flow version")
    max_loop: int = Field(default=10, description="Max loop iterations")
    providers: dict[str, dict[str, Any]] | None = Field(
        default=None, description="Workflow-level provider configurations keyed by name"
    )
    hooks: HookConfig | None = Field(
        default=None, description="Flow-level hook configuration"
    )
    profiles: dict[str, dict[str, Any]] | None = Field(
        default=None, description="Workflow-level profile definitions"
    )
    extraction_fallback: ExtractionFallback | Literal[False] | None = Field(
        default=None,
        description="Global extraction fallback override. false=disable, None=inherit from config.",
    )
    retry_escalation: EscalationConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_task_splitter(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Reject task_splitter field and provide migration guidance."""
        if "task_splitter" in values:
            raise ValueError(
                "task_splitter has been removed from Flow model. "
                "Configure task splitting in your fdsx config file instead. "
                "See: https://fdsx.dev/docs/config#task-splitter"
            )
        return values

    @model_validator(mode="after")
    def validate_start_at_exists(self) -> "Flow":
        if self.start_at not in self.states:
            raise ValueError(f"start_at '{self.start_at}' does not exist in states")
        return self

    @model_validator(mode="after")
    def validate_all_next_references(self) -> "Flow":
        from fdsx.core.graph_utils import get_next_states

        all_references: set[str] = set()
        for _state_name, state in self.states.items():
            all_references.update(get_next_states(state))

        for ref in all_references:
            if ref not in self.states:
                raise ValueError(f"next reference '{ref}' does not exist in states")

        return self

    @model_validator(mode="after")
    def validate_termination(self) -> "Flow":
        from fdsx.core.graph_utils import get_next_states

        def reaches_termination(start: str, visited: set[str]) -> bool:
            stack = [start]
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                if current == "$END":
                    return True
                visited.add(current)
                state = self.states.get(current)
                if state is None:
                    continue
                if isinstance(state, FailState):
                    return True
                next_states = get_next_states(state, include_end_sentinel=True)
                stack.extend(next_states - visited)
            return False

        if not reaches_termination(self.start_at, set()):
            raise ValueError(
                "flow must have at least one path to termination (end: true)"
            )

        return self

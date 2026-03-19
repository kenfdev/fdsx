from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


def _parse_path_segments(path: str) -> list[str | int]:
    """Parse a JSONPath-like string into typed segments.

    Handles dot notation (a.b) and bracket notation (a[0], a["key"]).
    Note: duplicated from variables._parse_jsonpath to avoid circular import.
    """
    parts: list[str | int] = []
    current = ""
    i = 0
    while i < len(path):
        char = path[i]
        if char == ".":
            if current:
                parts.append(current)
                current = ""
        elif char == "[":
            if current:
                parts.append(current)
                current = ""
            i += 1
            bracket = ""
            while i < len(path) and path[i] != "]":
                bracket += path[i]
                i += 1
            if bracket:
                try:
                    parts.append(int(bracket))
                except ValueError:
                    parts.append(bracket.strip("\"'"))
        else:
            current += char
        i += 1
    if current:
        parts.append(current)
    return parts


class LLMClassifyFallback(BaseModel):
    """LLM-based classification fallback."""

    type: Literal["llm_classify"] = "llm_classify"
    provider: str = Field(..., description="LLM provider")
    prompt: str = Field(..., description="Classification prompt")

    @model_validator(mode="after")
    def validate_provider(self) -> "LLMClassifyFallback":
        valid_llm_providers = {"claude", "opencode", "codex"}
        if self.provider not in valid_llm_providers:
            raise ValueError(
                f"LLM classify fallback provider must be one of "
                f"{', '.join(sorted(valid_llm_providers))}, got '{self.provider}'"
            )
        return self


class TaskSplitter(BaseModel):
    """Configuration for batch task splitting."""

    provider: str = Field(..., description="Provider name (claude/opencode/codex)")
    model: str = Field(..., description="Model name")


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


class NotifyConfig(BaseModel):
    """Notification configuration."""

    webhook: WebhookConfig = Field(..., description="Webhook configuration")


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
    valid_providers = {"claude", "opencode", "codex", "system"}
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
        parts = _parse_path_segments(ep)
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
    result_path: str = Field(..., description="JSONPath for result")
    extract: ExtractRule | None = Field(default=None, description="Output extraction")
    retry: int = Field(default=3, description="Retry count")
    timeout_seconds: int | None = Field(default=None, description="Timeout in seconds")
    next: str | None = Field(
        default=None, description="Next state (exclusive with end)"
    )
    end: bool | None = Field(default=None, description="End flow (exclusive with next)")

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
        if self.extract is None:
            return self
        rp = self.result_path
        if rp.startswith("$."):
            rp = rp[2:]
        ep = self.extract.result_path
        if ep.startswith("$."):
            ep = ep[2:]
        rp_parts = _parse_path_segments(rp)
        ep_parts = _parse_path_segments(ep)
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


class ParallelState(BaseModel):
    """Parallel state - executes multiple branches concurrently."""

    type: Literal["parallel"] = "parallel"
    branches: list[Branch] = Field(..., description="Parallel branch definitions")
    result_path: str = Field(..., description="JSONPath for results array")
    min_success: int | None = Field(default=None, description="Min successful branches")
    next: str | None = Field(
        default=None, description="Next state (exclusive with end)"
    )
    end: bool | None = Field(default=None, description="End flow (exclusive with next)")

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
    choices: list[str] = Field(..., description="User selection options")
    result_path: str = Field(..., description="JSONPath for selection result")
    notify: NotifyConfig | None = Field(
        default=None, description="Webhook notification"
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


State = Annotated[
    Union[TaskState, ChoiceState, ParallelState, PassState, WaitState],
    Field(discriminator="type"),
]


class Flow(BaseModel):
    """Top-level workflow definition."""

    name: str = Field(..., description="Flow name")
    start_at: str = Field(..., description="Initial state name")
    states: dict[str, State] = Field(..., description="State definitions keyed by name")
    comment: str | None = Field(default=None, description="Flow description")
    version: str | None = Field(default=None, description="Flow version")
    task_splitter: TaskSplitter | None = Field(
        default=None, description="Batch task splitting config"
    )
    max_loop: int = Field(default=10, description="Max loop iterations")

    @model_validator(mode="after")
    def validate_start_at_exists(self) -> "Flow":
        if self.start_at not in self.states:
            raise ValueError(f"start_at '{self.start_at}' does not exist in states")
        return self

    @model_validator(mode="after")
    def validate_all_next_references(self) -> "Flow":
        def get_next_states(state: State) -> set[str]:
            result = set()
            if isinstance(state, TaskState):
                if state.next:
                    result.add(state.next)
            elif isinstance(state, ChoiceState):
                for choice in state.choices:
                    result.add(choice.next)
                if state.default:
                    result.add(state.default)
            elif isinstance(state, ParallelState):
                if state.next:
                    result.add(state.next)
            elif isinstance(state, PassState):
                if state.next:
                    result.add(state.next)
            elif isinstance(state, WaitState):
                if state.next:
                    result.add(state.next)
            return result

        all_references: set[str] = set()
        for state_name, state in self.states.items():
            all_references.update(get_next_states(state))

        for ref in all_references:
            if ref not in self.states:
                raise ValueError(f"next reference '{ref}' does not exist in states")

        return self

    @model_validator(mode="after")
    def validate_termination(self) -> "Flow":
        def get_next_states(state: State) -> set[str]:
            result = set()
            if isinstance(state, TaskState):
                if state.next:
                    result.add(state.next)
                if state.end:
                    result.add("$END")
            elif isinstance(state, ChoiceState):
                for choice in state.choices:
                    result.add(choice.next)
                if state.default:
                    result.add(state.default)
                if state.default is None:
                    result.add("$END")
            elif isinstance(state, ParallelState):
                if state.next:
                    result.add(state.next)
                if state.end:
                    result.add("$END")
            elif isinstance(state, PassState):
                if state.next:
                    result.add(state.next)
                if state.end:
                    result.add("$END")
            elif isinstance(state, WaitState):
                if state.next:
                    result.add(state.next)
                if state.end:
                    result.add("$END")
            return result

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
                next_states = get_next_states(state)
                stack.extend(next_states - visited)
            return False

        if not reaches_termination(self.start_at, set()):
            raise ValueError(
                "flow must have at least one path to termination (end: true)"
            )

        return self

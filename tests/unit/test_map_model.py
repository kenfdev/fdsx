import pytest
from pydantic import ValidationError

from fdsx.models.flow import (
    ExtractRule,
    IteratorDef,
    IteratorTaskState,
    MapState,
)


class TestIteratorTaskState:
    def test_valid_task_state(self):
        state = IteratorTaskState(
            name="read_content",
            provider="claude",
            model="claude-3-5-sonnet-20241022",
            prompt_template="Read {{ item.url }}",
            result_path="$.content",
        )
        assert state.name == "read_content"
        assert state.type == "task"
        assert state.provider == "claude"

    def test_system_provider_with_command(self):
        state = IteratorTaskState(
            name="run_cmd",
            provider="system",
            command="echo hello",
            result_path="$.output",
        )
        assert state.provider == "system"
        assert state.command == "echo hello"

    def test_prompt_file_exclusive_with_prompt_template(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorTaskState(
                name="bad",
                provider="claude",
                model="claude-3-5-sonnet-20241022",
                prompt_template="hello",
                prompt_file="prompt.txt",
                result_path="$.out",
            )
        assert "prompt_template and prompt_file are mutually exclusive" in str(
            exc_info.value
        )

    def test_provider_system_forbids_prompt(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorTaskState(
                name="bad",
                provider="system",
                prompt_template="hello",
                result_path="$.out",
            )
        assert "provider=system forbids prompt_template" in str(exc_info.value)

    def test_provider_system_requires_command(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorTaskState(
                name="bad",
                provider="system",
                result_path="$.out",
            )
        assert "provider=system requires command" in str(exc_info.value)

    def test_provider_non_system_requires_model(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorTaskState(
                name="bad",
                provider="claude",
                prompt_template="hello",
                result_path="$.out",
            )
        assert "provider=claude requires model" in str(exc_info.value)

    def test_extract_path_overlap_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorTaskState(
                name="bad",
                provider="claude",
                model="claude-3-5-sonnet-20241022",
                prompt_template="hello",
                result_path="$.steps.read",
                extract=ExtractRule(
                    strategy=["json"],
                    pattern=".*",
                    result_path="$.steps.read.output",
                ),
            )
        assert "must not overlap" in str(exc_info.value)

    def test_result_file_validation(self):
        state = IteratorTaskState(
            name="save",
            provider="claude",
            model="claude-3-5-sonnet-20241022",
            prompt_template="hello",
            result_path="$.out",
            result_file="$.result_file_path",
        )
        assert state.result_file == "$.result_file_path"

    def test_result_file_must_start_with_dollar(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorTaskState(
                name="bad",
                provider="claude",
                model="claude-3-5-sonnet-20241022",
                prompt_template="hello",
                result_path="$.out",
                result_file="no_dollar",
            )
        assert "must start with '$.'" in str(exc_info.value)


class TestIteratorDef:
    def test_valid_iterator_def(self):
        iterator = IteratorDef(
            states=[
                IteratorTaskState(
                    name="step1",
                    provider="claude",
                    model="claude-3-5-sonnet-20241022",
                    prompt_template="hello",
                    result_path="$.out1",
                ),
                IteratorTaskState(
                    name="step2",
                    provider="claude",
                    model="claude-3-5-sonnet-20241022",
                    prompt_template="world",
                    result_path="$.out2",
                ),
            ]
        )
        assert len(iterator.states) == 2

    def test_empty_states_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorDef(states=[])
        assert "too_short" in str(exc_info.value)

    def test_duplicate_names_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorDef(
                states=[
                    IteratorTaskState(
                        name="step",
                        provider="claude",
                        model="claude-3-5-sonnet-20241022",
                        prompt_template="hello",
                        result_path="$.out1",
                    ),
                    IteratorTaskState(
                        name="step",
                        provider="claude",
                        model="claude-3-5-sonnet-20241022",
                        prompt_template="world",
                        result_path="$.out2",
                    ),
                ]
            )
        assert "duplicate iterator state name 'step'" in str(exc_info.value)

    def test_nested_map_type_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorDef(
                states=[
                    {
                        "name": "nested_map",
                        "type": "map",
                        "items_path": "$.items",
                        "iterator": {"states": []},
                        "result_path": "$.results",
                    }
                ]
            )
        assert "must have type 'task'" in str(exc_info.value)

    def test_nested_choice_type_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorDef(
                states=[
                    {
                        "name": "nested_choice",
                        "type": "choice",
                        "choices": [],
                    }
                ]
            )
        assert "must have type 'task'" in str(exc_info.value)

    def test_nested_parallel_type_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorDef(
                states=[
                    {
                        "name": "nested_parallel",
                        "type": "parallel",
                        "branches": [],
                        "result_path": "$.results",
                    }
                ]
            )
        assert "must have type 'task'" in str(exc_info.value)

    def test_non_list_states_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            IteratorDef(states="not_a_list")  # type: ignore[arg-type]
        assert "list" in str(exc_info.value).lower()


class TestMapState:
    def test_valid_map_state(self):
        state = MapState(
            items_path="$.items",
            iterator=IteratorDef(
                states=[
                    IteratorTaskState(
                        name="process",
                        provider="claude",
                        model="claude-3-5-sonnet-20241022",
                        prompt_template="Process {{ item }}",
                        result_path="$.result",
                    )
                ]
            ),
            result_path="$.results",
        )
        assert state.type == "map"
        assert state.items_path == "$.items"
        assert state.fail_fast is True

    def test_fail_fast_defaults_to_true(self):
        state = MapState(
            items_path="$.items",
            iterator=IteratorDef(
                states=[
                    IteratorTaskState(
                        name="step",
                        provider="claude",
                        model="claude-3-5-sonnet-20241022",
                        prompt_template="hello",
                        result_path="$.out",
                    )
                ]
            ),
            result_path="$.results",
        )
        assert state.fail_fast is True

    def test_fail_fast_can_be_false(self):
        state = MapState(
            items_path="$.items",
            iterator=IteratorDef(
                states=[
                    IteratorTaskState(
                        name="step",
                        provider="claude",
                        model="claude-3-5-sonnet-20241022",
                        prompt_template="hello",
                        result_path="$.out",
                    )
                ]
            ),
            result_path="$.results",
            fail_fast=False,
        )
        assert state.fail_fast is False

    def test_next_and_end_mutually_exclusive(self):
        with pytest.raises(ValidationError) as exc_info:
            MapState(
                items_path="$.items",
                iterator=IteratorDef(
                    states=[
                        IteratorTaskState(
                            name="step",
                            provider="claude",
                            model="claude-3-5-sonnet-20241022",
                            prompt_template="hello",
                            result_path="$.out",
                        )
                    ]
                ),
                result_path="$.results",
                next="next_state",
                end=True,
            )
        assert "next and end are mutually exclusive" in str(exc_info.value)

    def test_next_only(self):
        state = MapState(
            items_path="$.items",
            iterator=IteratorDef(
                states=[
                    IteratorTaskState(
                        name="step",
                        provider="claude",
                        model="claude-3-5-sonnet-20241022",
                        prompt_template="hello",
                        result_path="$.out",
                    )
                ]
            ),
            result_path="$.results",
            next="cleanup",
        )
        assert state.next == "cleanup"

    def test_end_only(self):
        state = MapState(
            items_path="$.items",
            iterator=IteratorDef(
                states=[
                    IteratorTaskState(
                        name="step",
                        provider="claude",
                        model="claude-3-5-sonnet-20241022",
                        prompt_template="hello",
                        result_path="$.out",
                    )
                ]
            ),
            result_path="$.results",
            end=True,
        )
        assert state.end is True

    def test_with_hooks(self):
        from fdsx.models.flow import HookConfig, HookEntry

        state = MapState(
            items_path="$.items",
            iterator=IteratorDef(
                states=[
                    IteratorTaskState(
                        name="step",
                        provider="claude",
                        model="claude-3-5-sonnet-20241022",
                        prompt_template="hello",
                        result_path="$.out",
                    )
                ]
            ),
            result_path="$.results",
            hooks=HookConfig(
                on_start=[HookEntry(command="echo starting")],
                on_complete=[HookEntry(command="echo done")],
            ),
        )
        assert state.hooks is not None
        assert len(state.hooks.on_start) == 1

    def test_with_max_iterations(self):
        state = MapState(
            items_path="$.items",
            iterator=IteratorDef(
                states=[
                    IteratorTaskState(
                        name="step",
                        provider="claude",
                        model="claude-3-5-sonnet-20241022",
                        prompt_template="hello",
                        result_path="$.out",
                    )
                ]
            ),
            result_path="$.results",
            max_iterations=5,
        )
        assert state.max_iterations == 5

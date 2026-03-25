from fdsx.core.graph_utils import END_SENTINEL, get_next_states
from fdsx.models.flow import (
    ChoiceRule,
    ChoiceState,
    ParallelState,
    PassState,
    TaskState,
    WaitState,
)


def make_task_state(next: str | None = None, end: bool | None = None) -> TaskState:
    return TaskState(
        provider="claude",
        model="claude-3-5-sonnet-20241022",
        prompt_template="hello",
        result_path="$.out",
        next=next,
        end=end,
    )


def make_pass_state(next: str | None = None, end: bool | None = None) -> PassState:
    return PassState(next=next, end=end)


def make_wait_state(next: str | None = None, end: bool | None = None) -> WaitState:
    return WaitState(
        mode="prompt",
        message="Choose",
        choices=["yes", "no"],
        result_path="$.choice",
        next=next,
        end=end,
    )


def make_parallel_state(
    next: str | None = None, end: bool | None = None
) -> ParallelState:
    from fdsx.models.flow import Branch

    return ParallelState(
        branches=[
            Branch(
                provider="claude",
                model="claude-3-5-sonnet-20241022",
                prompt_template="hello",
            )
        ],
        result_path="$.results",
        next=next,
        end=end,
    )


def make_choice_state(
    choices: list[tuple[str, str, str]], default: str | None = None
) -> ChoiceState:
    rules = [
        ChoiceRule(variable=v, operator="equals", value=val, next=nxt)
        for v, val, nxt in choices
    ]
    return ChoiceState(choices=rules, default=default)


class TestGetNextStatesTaskState:
    def test_next_only(self):
        state = make_task_state(next="B")
        assert get_next_states(state) == {"B"}

    def test_end_true_without_sentinel(self):
        state = make_task_state(end=True)
        assert get_next_states(state) == set()

    def test_end_true_with_sentinel(self):
        state = make_task_state(end=True)
        assert get_next_states(state, include_end_sentinel=True) == {END_SENTINEL}

    def test_next_with_sentinel_no_end(self):
        state = make_task_state(next="B")
        assert get_next_states(state, include_end_sentinel=True) == {"B"}

    def test_no_next_no_end(self):
        # TaskState with neither next nor end — result is empty
        state = TaskState(
            provider="claude",
            model="claude-3-5-sonnet-20241022",
            prompt_template="hello",
            result_path="$.out",
        )
        assert get_next_states(state) == set()
        assert get_next_states(state, include_end_sentinel=True) == set()


class TestGetNextStatesChoiceState:
    def test_choices_only(self):
        state = make_choice_state([("$.x", "yes", "A"), ("$.x", "no", "B")])
        assert get_next_states(state) == {"A", "B"}

    def test_default_included(self):
        state = make_choice_state([("$.x", "yes", "A")], default="C")
        assert get_next_states(state) == {"A", "C"}

    def test_no_default_without_sentinel(self):
        state = make_choice_state([("$.x", "yes", "A")])
        assert get_next_states(state) == {"A"}

    def test_no_default_with_sentinel(self):
        state = make_choice_state([("$.x", "yes", "A")])
        assert get_next_states(state, include_end_sentinel=True) == {"A", END_SENTINEL}

    def test_with_default_sentinel_not_added(self):
        state = make_choice_state([("$.x", "yes", "A")], default="C")
        # default is present, so $END is NOT added
        assert get_next_states(state, include_end_sentinel=True) == {"A", "C"}


class TestGetNextStatesParallelState:
    def test_next_only(self):
        state = make_parallel_state(next="Done")
        assert get_next_states(state) == {"Done"}

    def test_end_true_without_sentinel(self):
        state = make_parallel_state(end=True)
        assert get_next_states(state) == set()

    def test_end_true_with_sentinel(self):
        state = make_parallel_state(end=True)
        assert get_next_states(state, include_end_sentinel=True) == {END_SENTINEL}


class TestGetNextStatesPassState:
    def test_next_only(self):
        state = make_pass_state(next="Next")
        assert get_next_states(state) == {"Next"}

    def test_end_true_without_sentinel(self):
        state = make_pass_state(end=True)
        assert get_next_states(state) == set()

    def test_end_true_with_sentinel(self):
        state = make_pass_state(end=True)
        assert get_next_states(state, include_end_sentinel=True) == {END_SENTINEL}


class TestGetNextStatesWaitState:
    def test_next_only(self):
        state = make_wait_state(next="After")
        assert get_next_states(state) == {"After"}

    def test_end_true_without_sentinel(self):
        state = make_wait_state(end=True)
        assert get_next_states(state) == set()

    def test_end_true_with_sentinel(self):
        state = make_wait_state(end=True)
        assert get_next_states(state, include_end_sentinel=True) == {END_SENTINEL}

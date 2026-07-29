"""Unit tests for recovery eligibility and prerequisite rules."""

import pytest

from fdsx.core.config import FdsxConfig
from fdsx.core.engine.recovery import (
    RecoveryValidationError,
    validate_recovery_request,
)
from fdsx.models.flow import ChoiceState, Flow, TaskState


def test_recovery_prerequisites_include_merged_provider_options() -> None:
    flow = Flow(
        name="provider-options",
        description="Provider option references must be validated",
        start_at="review",
        states={
            "review": TaskState(
                provider="claude",
                model="sonnet",
                prompt_template="Review the change",
                end=True,
            )
        },
    )
    config = FdsxConfig(
        providers={
            "claude": {
                "system_prompt": "Use the context in {review_context}",
            }
        }
    )
    run_log = {
        "status": "error",
        "states": [{"name": "review", "status": "error"}],
    }

    with pytest.raises(
        RecoveryValidationError,
        match=r"state 'review'.*review_context",
    ):
        validate_recovery_request(
            flow,
            run_log,
            "review",
            {},
            config,
        )


def test_recovery_prerequisites_accept_existing_null_value() -> None:
    flow = Flow(
        name="nullable-choice",
        description="Null is a present value, not a missing path",
        start_at="route",
        states={
            "route": ChoiceState(
                choices=[
                    {
                        "variable": "$.decision",
                        "operator": "equals",
                        "value": None,
                        "next": "done",
                    }
                ],
                default="done",
            ),
            "done": TaskState(
                provider="system",
                command="echo done",
                end=True,
            ),
        },
    )
    run_log = {
        "status": "max_loop_reached",
        "states": [{"name": "route", "status": "completed"}],
    }

    validate_recovery_request(
        flow,
        run_log,
        "route",
        {"decision": None},
    )


def test_item_is_not_a_builtin_outside_map_iterator() -> None:
    flow = Flow(
        name="task-item",
        description="Task targets cannot assume an iterator-local item",
        start_at="review",
        states={
            "review": TaskState(
                provider="system",
                command="echo {item}",
                end=True,
            )
        },
    )
    run_log = {
        "status": "error",
        "states": [{"name": "review", "status": "error"}],
    }

    with pytest.raises(RecoveryValidationError, match=r"state 'review'.*item"):
        validate_recovery_request(
            flow,
            run_log,
            "review",
            {},
        )

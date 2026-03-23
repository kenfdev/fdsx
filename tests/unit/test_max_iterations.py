"""Unit tests for per-state max_iterations (T007).

Tests verify:
- Pydantic model accepts max_iterations: 3 on all state types
- max_iterations: 0 raises ValidationError
- max_iterations: -1 raises ValidationError
- max_iterations: None (default) is accepted
- _check_max_iterations raises RuntimeError with correct message when exceeded
- _check_max_iterations allows execution within limit
- _check_max_iterations skips check when max_iterations is None
"""

import pytest
from pydantic import ValidationError

from fdsx.core.compiler import _check_max_iterations
from fdsx.models.flow import ChoiceState, ParallelState, PassState, TaskState, WaitState


class TestMaxIterationsModelValidation:
    """Tests that Pydantic model accepts and rejects max_iterations values."""

    def test_max_iterations_accepted_on_task_state(self):
        """TaskState accepts max_iterations: 3."""
        state = TaskState(
            provider="system",
            command="echo hi",
            result_path="$.out",
            max_iterations=3,
            end=True,
        )
        assert state.max_iterations == 3

    def test_max_iterations_accepted_on_choice_state(self):
        """ChoiceState accepts max_iterations: 3."""
        state = ChoiceState(
            choices=[
                {"variable": "$.x", "operator": "equals", "value": "y", "next": "done"}
            ],
            max_iterations=3,
        )
        assert state.max_iterations == 3

    def test_max_iterations_accepted_on_parallel_state(self):
        """ParallelState accepts max_iterations: 3."""
        state = ParallelState(
            branches=[
                {
                    "name": "b1",
                    "provider": "system",
                    "command": "echo hi",
                    "result_path": "$.out",
                }
            ],
            result_path="$.results",
            max_iterations=3,
            end=True,
        )
        assert state.max_iterations == 3

    def test_max_iterations_accepted_on_pass_state(self):
        """PassState accepts max_iterations: 3."""
        state = PassState(max_iterations=3, end=True)
        assert state.max_iterations == 3

    def test_max_iterations_accepted_on_wait_state(self):
        """WaitState accepts max_iterations: 3."""
        state = WaitState(
            mode="prompt",
            message="Choose:",
            choices=["yes", "no"],
            result_path="$.choice",
            max_iterations=3,
            end=True,
        )
        assert state.max_iterations == 3

    def test_max_iterations_zero_raises_validation_error(self):
        """max_iterations: 0 raises ValidationError (ge=1 constraint)."""
        with pytest.raises(ValidationError):
            TaskState(
                provider="system",
                command="echo hi",
                result_path="$.out",
                max_iterations=0,
                end=True,
            )

    def test_max_iterations_negative_raises_validation_error(self):
        """max_iterations: -1 raises ValidationError (ge=1 constraint)."""
        with pytest.raises(ValidationError):
            TaskState(
                provider="system",
                command="echo hi",
                result_path="$.out",
                max_iterations=-1,
                end=True,
            )

    def test_max_iterations_none_default(self):
        """Default value for max_iterations is None (no limit)."""
        state = TaskState(
            provider="system",
            command="echo hi",
            result_path="$.out",
            end=True,
        )
        assert state.max_iterations is None

    def test_max_iterations_none_explicit(self):
        """Explicitly passing max_iterations=None is accepted."""
        state = PassState(max_iterations=None, end=True)
        assert state.max_iterations is None


class TestCheckMaxIterations:
    """Tests for the _check_max_iterations helper function."""

    def test_raises_when_exceeded(self):
        """Raises RuntimeError with correct message when iteration > max_iterations."""
        state = TaskState(
            provider="system",
            command="echo hi",
            result_path="$.out",
            max_iterations=3,
            end=True,
        )
        with pytest.raises(
            RuntimeError,
            match="State 'plan' reached max_iterations limit \\(3\\)",
        ):
            _check_max_iterations("plan", state, 4)

    def test_raises_exactly_at_limit_plus_one(self):
        """Raises when iteration == max_iterations + 1 (strictly exceeds)."""
        state = PassState(max_iterations=2, end=True)
        with pytest.raises(
            RuntimeError,
            match="State 'review' reached max_iterations limit \\(2\\)",
        ):
            _check_max_iterations("review", state, 3)

    def test_allows_at_limit(self):
        """Does NOT raise when iteration == max_iterations (exactly at limit)."""
        state = TaskState(
            provider="system",
            command="echo hi",
            result_path="$.out",
            max_iterations=3,
            end=True,
        )
        # Should not raise
        _check_max_iterations("plan", state, 3)

    def test_allows_within_limit(self):
        """Does NOT raise when iteration < max_iterations."""
        state = TaskState(
            provider="system",
            command="echo hi",
            result_path="$.out",
            max_iterations=5,
            end=True,
        )
        # Should not raise
        _check_max_iterations("plan", state, 2)

    def test_skips_when_none(self):
        """Does nothing when max_iterations is None (default)."""
        state = TaskState(
            provider="system",
            command="echo hi",
            result_path="$.out",
            end=True,
        )
        # Should not raise even for large iteration count
        _check_max_iterations("plan", state, 999)

    def test_error_message_includes_state_name_and_limit(self):
        """RuntimeError message includes state name and limit value."""
        state = ChoiceState(
            choices=[
                {"variable": "$.x", "operator": "equals", "value": "y", "next": "done"}
            ],
            max_iterations=1,
        )
        with pytest.raises(RuntimeError) as exc_info:
            _check_max_iterations("decide", state, 2)
        assert "decide" in str(exc_info.value)
        assert "1" in str(exc_info.value)

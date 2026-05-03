import pytest
from pydantic import ValidationError

from fdsx.models.flow import FailState, Flow, TaskState


class TestFailStateModelParsing:
    def test_valid_minimal_fail_state_parses(self):
        state = FailState(
            type="fail", error="InternalError", cause="something went wrong"
        )
        assert state.type == "fail"
        assert state.error == "InternalError"
        assert state.cause == "something went wrong"

    def test_missing_error_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            FailState(type="fail", cause="something went wrong")
        assert "error" in str(exc_info.value)

    def test_missing_cause_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            FailState(type="fail", error="InternalError")
        assert "cause" in str(exc_info.value)

    def test_empty_error_raises_validation_error(self):
        with pytest.raises(ValidationError):
            FailState(type="fail", error="", cause="something went wrong")

    def test_empty_cause_raises_validation_error(self):
        with pytest.raises(ValidationError):
            FailState(type="fail", error="InternalError", cause="")

    def test_next_field_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            FailState(
                type="fail", error="InternalError", cause="bad", next="some_state"
            )
        assert any(
            phrase in str(exc_info.value).lower()
            for phrase in ["successor", "cannot declare"]
        )

    def test_end_field_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            FailState(type="fail", error="InternalError", cause="bad", end=True)
        assert "end" in str(exc_info.value).lower()

    def test_max_iterations_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            FailState(type="fail", error="InternalError", cause="bad", max_iterations=1)
        assert "max_iterations" in str(exc_info.value)


class TestFailStateInFlowUnion:
    def test_flow_with_fail_state_parses_successfully(self):
        flow = Flow(
            name="test-fail-flow",
            description="Flow with a fail state terminal",
            start_at="start",
            states={
                "start": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    next="end_state",
                ),
                "end_state": FailState(
                    type="fail",
                    error="InternalError",
                    cause="something went wrong",
                ),
            },
        )
        assert "end_state" in flow.states
        assert isinstance(flow.states["end_state"], FailState)

    def test_validate_termination_accepts_fail_only_terminal(self):
        """validate_termination must pass when the only reachable terminal is a FailState."""
        flow = Flow(
            name="fail-terminal-flow",
            description="Flow that terminates only via FailState",
            start_at="step1",
            states={
                "step1": TaskState(
                    type="task",
                    provider="system",
                    command="echo hello",
                    next="fail_terminal",
                ),
                "fail_terminal": FailState(
                    type="fail",
                    error="StepFailed",
                    cause="step1 produced an error",
                ),
            },
        )
        # If we reach here, validate_termination accepted the FailState as a terminal.
        assert flow.start_at == "step1"

"""Unit tests for _build_state_schema always returning TypedDict (Phase 2)."""

import tempfile
from pathlib import Path

from fdsx.core.compiler.helpers import _build_state_schema
from fdsx.core.loader import load_flow


def _load(yaml_text: str):
    """Load a Flow from a YAML string, asserting no errors."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_text)
        path = Path(f.name)
    try:
        flow, errors = load_flow(path)
        assert flow is not None, f"load_flow failed: {errors}"
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        return flow
    finally:
        path.unlink(missing_ok=True)


LINEAR_FLOW_YAML = """\
name: Linear Flow
description: Simple linear flow for testing
start_at: step1
version: '1.0'
states:
  step1:
    type: task
    provider: system
    command: "echo hello"
    result_path: $.output1
    next: step2
  step2:
    type: task
    provider: system
    command: "echo world"
    result_path: $.output2
    end: true
"""

PARALLEL_FLOW_YAML = """\
name: Parallel Flow
description: Flow with parallel state for testing
start_at: par
version: '1.0'
states:
  par:
    type: parallel
    result_path: $.par_result
    branches:
      - provider: system
        command: "echo branch1"
    next: done
  done:
    type: task
    provider: system
    command: "echo done"
    result_path: $.final
    end: true
"""

INPUT_KEYS_FLOW_YAML = """\
name: Input Keys Flow
description: Flow with input keys for testing
start_at: step1
version: '1.0'
states:
  step1:
    type: task
    provider: system
    command: "echo ok"
    result_path: $.result
    end: true
"""


class TestBuildStateSchemaAlwaysTypedDict:
    def test_linear_flow_returns_typeddict(self):
        """Non-parallel flow must return TypedDict, never object."""
        flow = _load(LINEAR_FLOW_YAML)
        schema = _build_state_schema(flow)

        assert schema is not object, "_build_state_schema must never return object"
        assert hasattr(schema, "__annotations__"), (
            "Return value must be a TypedDict class with __annotations__"
        )

    def test_linear_flow_result_keys_in_annotations(self):
        """result_path top-level keys must appear in schema annotations."""
        flow = _load(LINEAR_FLOW_YAML)
        schema = _build_state_schema(flow)

        annotations = schema.__annotations__
        assert "output1" in annotations
        assert "output2" in annotations

    def test_linear_flow_remaining_steps_in_annotations(self):
        """remaining_steps managed channel must be present for all flows."""
        flow = _load(LINEAR_FLOW_YAML)
        schema = _build_state_schema(flow)

        assert "remaining_steps" in schema.__annotations__, (
            "remaining_steps must be in schema for loop control (Phase 4)"
        )

    def test_parallel_flow_returns_typeddict_with_reducer_keys(self):
        """Parallel flow must return TypedDict with _br_ reducer channels."""
        flow = _load(PARALLEL_FLOW_YAML)
        schema = _build_state_schema(flow)

        assert schema is not object
        assert hasattr(schema, "__annotations__")
        annotations = schema.__annotations__
        assert "_br_par" in annotations, (
            "Parallel state 'par' must have _br_par channel"
        )

    def test_parallel_flow_remaining_steps_in_annotations(self):
        """remaining_steps must also be present for flows with ParallelState."""
        flow = _load(PARALLEL_FLOW_YAML)
        schema = _build_state_schema(flow)

        assert "remaining_steps" in schema.__annotations__

    def test_input_keys_appear_in_annotations(self):
        """Explicitly provided input_keys must appear in schema annotations."""
        flow = _load(INPUT_KEYS_FLOW_YAML)
        schema = _build_state_schema(flow, input_keys={"task", "context"})

        annotations = schema.__annotations__
        assert "task" in annotations
        assert "context" in annotations

    def test_internal_keys_always_present(self):
        """_meta and _state_iterations internal keys must always be present."""
        flow = _load(LINEAR_FLOW_YAML)
        schema = _build_state_schema(flow)

        annotations = schema.__annotations__
        assert "_meta" in annotations
        assert "_state_iterations" in annotations

    def test_schema_name_is_flow_state(self):
        """TypedDict name must be 'FlowState'."""
        flow = _load(LINEAR_FLOW_YAML)
        schema = _build_state_schema(flow)

        assert schema.__name__ == "FlowState"

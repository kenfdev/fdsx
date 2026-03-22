"""TDD tests for result_file variable passing.

Phase 1:
  T001: Tests for write_result_to_file() helper
  T003: Tests for result_file model field validation

Phase 2:
  T006: Tests for _meta.run_dir propagation in run_flow()
  T008: Tests for static analysis recognizing result_file variables
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from fdsx.core.variables import write_result_to_file
from fdsx.models.flow import ParallelState, TaskState


# ---------------------------------------------------------------------------
# T001: write_result_to_file() helper
# ---------------------------------------------------------------------------


class TestWriteResultToFile:
    """T001: Tests for the write_result_to_file() helper function."""

    def test_string_value_creates_md_file(self, tmp_path: Path) -> None:
        """String value → creates <varname>.md with string content."""
        result = write_result_to_file("plan", "Hello world", tmp_path)
        expected = tmp_path / "data" / "plan.md"
        assert expected.exists()
        assert expected.read_text(encoding="utf-8") == "Hello world"
        assert result == str(expected.resolve())

    def test_dict_value_creates_json_file(self, tmp_path: Path) -> None:
        """Dict value → creates <varname>.json with JSON content."""
        value = {"key": "value", "count": 42}
        result = write_result_to_file("metadata", value, tmp_path)
        expected = tmp_path / "data" / "metadata.json"
        assert expected.exists()
        loaded = json.loads(expected.read_text(encoding="utf-8"))
        assert loaded == value
        assert result == str(expected.resolve())

    def test_list_value_creates_json_file(self, tmp_path: Path) -> None:
        """List value → creates <varname>.json with JSON content."""
        value = ["item1", "item2", {"nested": True}]
        result = write_result_to_file("reviews", value, tmp_path)
        expected = tmp_path / "data" / "reviews.json"
        assert expected.exists()
        loaded = json.loads(expected.read_text(encoding="utf-8"))
        assert loaded == value
        assert result == str(expected.resolve())

    def test_data_directory_created_automatically(self, tmp_path: Path) -> None:
        """data/ directory created automatically if missing."""
        data_dir = tmp_path / "data"
        assert not data_dir.exists()
        write_result_to_file("output", "content", tmp_path)
        assert data_dir.exists()
        assert data_dir.is_dir()

    def test_file_overwritten_on_second_call(self, tmp_path: Path) -> None:
        """File overwritten on second call with same varname."""
        write_result_to_file("output", "first content", tmp_path)
        write_result_to_file("output", "second content", tmp_path)
        expected = tmp_path / "data" / "output.md"
        assert expected.read_text(encoding="utf-8") == "second content"

    def test_returns_absolute_file_path(self, tmp_path: Path) -> None:
        """Returns absolute file path as string."""
        result = write_result_to_file("result", "content", tmp_path)
        assert Path(result).is_absolute()

    def test_utf8_encoding_for_non_ascii(self, tmp_path: Path) -> None:
        """UTF-8 encoding for non-ASCII content."""
        value = "日本語テスト: 🚀 émojis"
        write_result_to_file("unicode_result", value, tmp_path)
        expected = tmp_path / "data" / "unicode_result.md"
        assert expected.read_text(encoding="utf-8") == value

    def test_json_content_is_valid_json(self, tmp_path: Path) -> None:
        """JSON files must be parseable."""
        value = {"nested": {"deep": [1, 2, 3]}}
        write_result_to_file("complex", value, tmp_path)
        file_path = tmp_path / "data" / "complex.json"
        content = file_path.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert parsed == value


# ---------------------------------------------------------------------------
# T003: result_file model field validation
# ---------------------------------------------------------------------------


class TestTaskStateResultFile:
    """T003: Tests for result_file field on TaskState."""

    def _base_task(self, **kwargs) -> TaskState:
        return TaskState(
            type="task",
            provider="system",
            command="echo test",
            result_path="$.result",
            end=True,
            **kwargs,
        )

    def test_accepts_valid_result_file(self) -> None:
        """TaskState accepts result_file: '$.plan_ref'."""
        state = self._base_task(result_file="$.plan_ref")
        assert state.result_file == "$.plan_ref"

    def test_defaults_to_none_when_not_set(self) -> None:
        """result_file defaults to None when not set."""
        state = self._base_task()
        assert state.result_file is None

    def test_rejects_missing_dollar_prefix(self) -> None:
        """Rejects result_file without '$.' prefix."""
        with pytest.raises(ValidationError, match=r"\$\."):
            self._base_task(result_file="plan_ref")

    def test_rejects_nested_path(self) -> None:
        """Rejects nested path like '$.foo.bar'."""
        with pytest.raises(ValidationError, match="nested"):
            self._base_task(result_file="$.foo.bar")

    def test_rejects_nested_bracket_path(self) -> None:
        """Rejects bracket-notation nested path like '$.foo[0]'."""
        with pytest.raises(ValidationError, match="nested"):
            self._base_task(result_file="$.foo[0]")

    def test_result_file_and_result_path_coexist(self) -> None:
        """result_file and result_path can coexist with different variable names."""
        state = self._base_task(result_file="$.plan_ref")
        assert state.result_path == "$.result"
        assert state.result_file == "$.plan_ref"

    def test_rejects_empty_varname(self) -> None:
        """Rejects '$.' with no variable name after the prefix."""
        with pytest.raises(ValidationError, match="variable name"):
            self._base_task(result_file="$.")

    def test_accepts_various_valid_varnames(self) -> None:
        """Various valid top-level variable names are accepted."""
        for varname in ("$.output_ref", "$.my_var", "$.x"):
            state = self._base_task(result_file=varname)
            assert state.result_file == varname


class TestParallelStateResultFile:
    """T003: Tests for result_file field on ParallelState."""

    def _base_parallel(self, **kwargs) -> ParallelState:
        return ParallelState(
            type="parallel",
            branches=[],
            result_path="$.results",
            end=True,
            **kwargs,
        )

    def test_accepts_valid_result_file(self) -> None:
        """ParallelState accepts result_file: '$.reviews_ref'."""
        state = self._base_parallel(result_file="$.reviews_ref")
        assert state.result_file == "$.reviews_ref"

    def test_defaults_to_none_when_not_set(self) -> None:
        """result_file defaults to None when not set."""
        state = self._base_parallel()
        assert state.result_file is None

    def test_rejects_missing_dollar_prefix(self) -> None:
        """Rejects result_file without '$.' prefix."""
        with pytest.raises(ValidationError, match=r"\$\."):
            self._base_parallel(result_file="reviews_ref")

    def test_rejects_nested_path(self) -> None:
        """Rejects nested path like '$.foo.bar'."""
        with pytest.raises(ValidationError, match="nested"):
            self._base_parallel(result_file="$.foo.bar")

    def test_rejects_nested_bracket_path(self) -> None:
        """Rejects bracket-notation nested path like '$.results[0]'."""
        with pytest.raises(ValidationError, match="nested"):
            self._base_parallel(result_file="$.results[0]")

    def test_result_file_and_result_path_coexist(self) -> None:
        """result_file and result_path can coexist with different variable names."""
        state = self._base_parallel(result_file="$.reviews_ref")
        assert state.result_path == "$.results"
        assert state.result_file == "$.reviews_ref"

    def test_rejects_empty_varname(self) -> None:
        """Rejects '$.' with no variable name after the prefix."""
        with pytest.raises(ValidationError, match="variable name"):
            self._base_parallel(result_file="$.")

    def test_accepts_various_valid_varnames(self) -> None:
        """Various valid top-level variable names are accepted."""
        for varname in ("$.reviews_ref", "$.summary_ref", "$.r"):
            state = self._base_parallel(result_file=varname)
            assert state.result_file == varname


# ---------------------------------------------------------------------------
# T006: _meta.run_dir propagation in run_flow()
# ---------------------------------------------------------------------------


class TestMetaRunDir:
    """T006: Tests that run_flow() sets _meta.run_dir in initial_state."""

    def _make_flow_yaml(self, path: Path) -> Path:
        """Create a minimal single-state flow YAML for testing."""
        flow_path = path / "test_flow.yaml"
        flow_path.write_text(
            "name: test\n"
            "description: test flow\n"
            "start_at: start\n"
            "states:\n"
            "  start:\n"
            "    type: task\n"
            "    provider: system\n"
            "    command: echo test\n"
            "    result_path: $.output\n"
            "    end: true\n"
        )
        return flow_path

    def _run_flow_with_capture(self, flow_path: Path, thread_id: str) -> dict:
        """Run flow with mocked compile_flow and return captured initial_state."""
        from unittest.mock import MagicMock, patch

        from fdsx.core.engine import run_flow

        captured: list[dict] = []
        mock_graph = MagicMock()
        mock_graph.stream.side_effect = lambda state, **kw: (
            captured.append(state) or []
        )
        mock_graph.get_state.return_value = MagicMock(tasks=[], values={})
        mock_compiled = MagicMock()
        mock_compiled.graph = mock_graph
        mock_compiled.result_paths = []

        with (
            patch("fdsx.core.engine.compile_flow", return_value=mock_compiled),
            patch("fdsx.core.engine.RunRecorder") as mock_recorder_cls,
            patch("fdsx.core.engine.display_completion_summary"),
            patch("fdsx.core.engine.load_config"),
        ):
            mock_recorder_instance = MagicMock()
            mock_recorder_instance.started_at = "2026-01-01T00:00:00+00:00"
            mock_recorder_instance.completed_at = None
            mock_recorder_instance.states = []
            mock_recorder_instance.flow_name = "test"
            mock_recorder_cls.return_value = mock_recorder_instance
            run_flow(flow_path, thread_id=thread_id)

        assert len(captured) == 1, "compile_flow.graph.stream was not called"
        return captured[0]

    def test_run_flow_sets_meta_run_dir(self, tmp_path: Path) -> None:
        """initial_state['_meta']['run_dir'] is set in run_flow."""
        flow_path = self._make_flow_yaml(tmp_path)
        initial_state = self._run_flow_with_capture(flow_path, "test-thread-t006a")
        assert "_meta" in initial_state
        assert "run_dir" in initial_state["_meta"], (
            f"Expected 'run_dir' in _meta, got: {initial_state['_meta']}"
        )

    def test_run_dir_value_matches_runs_base_runs_thread_id(
        self, tmp_path: Path
    ) -> None:
        """run_dir value matches <_runs_base>/runs/<thread_id>."""
        from fdsx.logging.recorder import RUNS_DIR_NAME

        flow_path = self._make_flow_yaml(tmp_path)
        thread_id = "test-thread-t006b"
        initial_state = self._run_flow_with_capture(flow_path, thread_id)
        run_dir = initial_state["_meta"]["run_dir"]
        expected_suffix = f"{RUNS_DIR_NAME}/{thread_id}"
        assert run_dir.endswith(expected_suffix), (
            f"Expected run_dir to end with {expected_suffix!r}, got {run_dir!r}"
        )


# ---------------------------------------------------------------------------
# T008: Static analysis recognizes result_file variables
# ---------------------------------------------------------------------------


class TestStaticAnalysisResultFile:
    """T008: Tests that analyze_variable_references() does not flag result_file
    variables as undefined when they are set by an upstream state."""

    def test_task_result_file_variable_not_flagged_downstream(self) -> None:
        """analyze_variable_references() does not flag a variable set via
        TaskState result_file when it is referenced in a downstream state."""
        from fdsx.core.variables import analyze_variable_references
        from fdsx.models.flow import Flow

        flow = Flow(
            name="result_file flow",
            description="Test result_file static analysis",
            start_at="produce",
            states={
                "produce": TaskState(
                    type="task",
                    provider="system",
                    command="echo content",
                    result_path="$.output",
                    result_file="$.plan_ref",
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {plan_ref}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_parallel_result_file_variable_not_flagged_downstream(self) -> None:
        """analyze_variable_references() does not flag a variable set via
        ParallelState result_file when it is referenced downstream."""
        from fdsx.core.variables import analyze_variable_references
        from fdsx.models.flow import Branch, Flow

        flow = Flow(
            name="parallel result_file flow",
            description="Test parallel result_file static analysis",
            start_at="par",
            states={
                "par": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(provider="system", command="echo branch1"),
                        Branch(provider="system", command="echo branch2"),
                    ],
                    result_path="$.par_output",
                    result_file="$.reviews_ref",
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {reviews_ref}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_result_file_variable_undefined_still_flagged(self) -> None:
        """A variable referenced downstream that has NOT been set via result_file
        or result_path is still flagged as undefined."""
        from fdsx.core.variables import analyze_variable_references
        from fdsx.models.flow import Flow

        flow = Flow(
            name="undefined ref flow",
            description="Test that undefined var is still flagged",
            start_at="produce",
            states={
                "produce": TaskState(
                    type="task",
                    provider="system",
                    command="echo content",
                    result_path="$.output",
                    result_file="$.plan_ref",
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {not_set_anywhere}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 1
        assert "not_set_anywhere" in errors[0]

    def test_result_file_and_result_path_both_recognized(self) -> None:
        """Both result_file and result_path on the same state are recognized."""
        from fdsx.core.variables import analyze_variable_references
        from fdsx.models.flow import Flow

        flow = Flow(
            name="both fields flow",
            description="Test both result_file and result_path",
            start_at="produce",
            states={
                "produce": TaskState(
                    type="task",
                    provider="system",
                    command="echo content",
                    result_path="$.raw_result",
                    result_file="$.file_ref",
                    next="consume",
                ),
                "consume": TaskState(
                    type="task",
                    provider="system",
                    command="echo {raw_result} {file_ref}",
                    result_path="$.final",
                    end=True,
                ),
            },
        )
        errors = analyze_variable_references(flow)
        assert len(errors) == 0, f"Unexpected errors: {errors}"

"""Phase 1 TDD tests: write_result_to_file() helper and result_file model field.

T001: Tests for write_result_to_file() helper
T003: Tests for result_file model field validation
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

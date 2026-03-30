"""Unit tests for _format_tool_input_summary helper."""

from fdsx.providers.claude import _format_tool_input_summary


class TestFormatToolInputSummary:
    """Unit tests for _format_tool_input_summary helper."""

    def test_command_key_returns_value(self) -> None:
        assert (
            _format_tool_input_summary("Bash", {"command": "ls /workspace"})
            == "ls /workspace"
        )

    def test_file_path_for_edit(self) -> None:
        assert (
            _format_tool_input_summary("Edit", {"file_path": "/tmp/test.txt"})
            == "/tmp/test.txt"
        )

    def test_file_path_for_write(self) -> None:
        assert (
            _format_tool_input_summary("Write", {"file_path": "/tmp/out.txt"})
            == "/tmp/out.txt"
        )

    def test_priority_command_over_file_path(self) -> None:
        input_json: dict[str, object] = {
            "command": "ls",
            "file_path": "/tmp",
            "description": "desc",
        }
        assert _format_tool_input_summary("Bash", input_json) == "ls"

    def test_priority_file_path_over_description(self) -> None:
        input_json: dict[str, object] = {"file_path": "/tmp", "description": "desc"}
        assert _format_tool_input_summary("Read", input_json) == "/tmp"

    def test_truncation_over_120(self) -> None:
        long_value = "x" * 200
        result = _format_tool_input_summary("Bash", {"command": long_value})
        assert len(result) == 121
        assert result.endswith("\u2026")
        assert result == "x" * 120 + "\u2026"

    def test_exactly_120_no_truncation(self) -> None:
        value_120 = "x" * 120
        result = _format_tool_input_summary("Bash", {"command": value_120})
        assert result == value_120
        assert not result.endswith("\u2026")

    def test_empty_dict(self) -> None:
        assert _format_tool_input_summary("Bash", {}) == ""

    def test_no_recognized_keys(self) -> None:
        assert _format_tool_input_summary("Bash", {"unknown": "value"}) == ""

    def test_empty_string_value_skipped(self) -> None:
        assert _format_tool_input_summary("Bash", {"command": ""}) == ""

    def test_non_string_value_skipped(self) -> None:
        assert _format_tool_input_summary("Bash", {"command": 123}) == ""

    def test_none_value_skipped(self) -> None:
        assert _format_tool_input_summary("Bash", {"command": None}) == ""

"""Integration tests for result_file variable passing (T018, T019).

T018: End-to-end tests with system provider verifying result_file creates files
      and stores absolute paths in state variables.
T019: Regression test verifying that flows without result_file do not create
      the data/ directory.
"""

import json
from pathlib import Path

import pytest

from fdsx.core import engine
from fdsx.core.engine import FlowResult
from fdsx.core.variables import RESULT_FILE_DATA_DIR
from fdsx.logging.recorder import RUNS_DIR_NAME
from tests import FIXTURES_DIR


class TestTaskResultFileIntegration:
    """T018: Task state result_file end-to-end integration test."""

    def test_task_result_file_creates_md_and_stores_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two-state workflow: echo → cat (downstream file read).

        Asserts:
        - File exists at <run_dir>/data/output_ref.md
        - File content equals the echo output
        - result["output_ref"] is the absolute path to that file
        - Downstream state reads file contents via the stored path
        """
        monkeypatch.chdir(tmp_path)

        flow_yaml = tmp_path / "task_result_file_flow.yaml"
        flow_yaml.write_text(
            "name: Task Result File Flow\n"
            "description: Test result_file on task state\n"
            "start_at: echo_state\n"
            "states:\n"
            "  echo_state:\n"
            "    type: task\n"
            "    provider: system\n"
            "    command: \"echo 'Hello World'\"\n"
            "    result_path: $.raw_output\n"
            "    result_file: $.output_ref\n"
            "    next: consume_state\n"
            "  consume_state:\n"
            "    type: task\n"
            "    provider: system\n"
            '    command: "cat {output_ref}"\n'
            "    result_path: $.final\n"
            "    end: true\n",
            encoding="utf-8",
        )

        thread_id = "test-task-result-file"
        result = engine.run_flow(flow_yaml, thread_id=thread_id, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        run_dir = tmp_path / RUNS_DIR_NAME / thread_id
        expected_file = run_dir / RESULT_FILE_DATA_DIR / "output_ref.md"

        assert "output_ref" in result.results, (
            f"result_file variable missing from result: {result.results}"
        )
        assert Path(result.results["output_ref"]).is_absolute(), (
            f"output_ref should be absolute path, got: {result.results['output_ref']}"
        )
        assert expected_file.exists(), f"Expected file not found: {expected_file}"

        content = expected_file.read_text(encoding="utf-8")
        assert "Hello World" in content, (
            f"File content mismatch. Expected 'Hello World', got: {content!r}"
        )
        assert result.results["output_ref"] == str(expected_file.resolve()), (
            f"output_ref path mismatch: {result.results['output_ref']} != {expected_file.resolve()}"
        )
        assert "final" in result.results, (
            f"Downstream state result missing: {result.results}"
        )
        assert "Hello World" in result.results["final"], (
            f"Downstream state should read file contents, got: {result.results['final']!r}"
        )


class TestParallelResultFileIntegration:
    """T018: Parallel state result_file end-to-end integration test."""

    def test_parallel_result_file_creates_json_and_stores_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Parallel state with two branches and result_file.

        Asserts:
        - File exists at <run_dir>/data/reviews_ref.json
        - File contains valid JSON (list of branch results)
        - result["reviews_ref"] is the absolute path to that file
        """
        monkeypatch.chdir(tmp_path)

        flow_yaml = tmp_path / "parallel_result_file_flow.yaml"
        flow_yaml.write_text(
            "name: Parallel Result File Flow\n"
            "description: Test result_file on parallel state\n"
            "start_at: review_parallel\n"
            "states:\n"
            "  review_parallel:\n"
            "    type: parallel\n"
            "    branches:\n"
            "      - provider: system\n"
            "        command: \"echo 'Branch 1 review'\"\n"
            "        retry: 0\n"
            "      - provider: system\n"
            "        command: \"echo 'Branch 2 review'\"\n"
            "        retry: 0\n"
            "    result_path: $.reviews\n"
            "    result_file: $.reviews_ref\n"
            "    end: true\n",
            encoding="utf-8",
        )

        thread_id = "test-parallel-result-file"
        result = engine.run_flow(flow_yaml, thread_id=thread_id, base_dir=tmp_path)

        run_dir = tmp_path / RUNS_DIR_NAME / thread_id
        expected_file = run_dir / RESULT_FILE_DATA_DIR / "reviews_ref.json"

        assert "reviews_ref" in result.results, (
            f"result_file variable missing from result: {result.results}"
        )
        assert Path(result.results["reviews_ref"]).is_absolute(), (
            f"reviews_ref should be absolute path, got: {result.results['reviews_ref']}"
        )
        assert expected_file.exists(), f"Expected JSON file not found: {expected_file}"

        content = expected_file.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert isinstance(parsed, list), (
            f"Expected JSON list in {expected_file}, got: {type(parsed)}"
        )
        assert len(parsed) == 2, f"Expected 2 branch results, got: {len(parsed)}"
        for entry in parsed:
            assert "output" in entry, f"Missing 'output' key in branch result: {entry}"
            assert "exit_code" in entry, (
                f"Missing 'exit_code' key in branch result: {entry}"
            )
        assert result.results["reviews_ref"] == str(expected_file.resolve()), (
            f"reviews_ref path mismatch: {result.results['reviews_ref']} != {expected_file.resolve()}"
        )


class TestResultFileWithoutResultPath:
    """Regression: result_file without result_path must still propagate the path to downstream states."""

    def test_result_file_only_propagates_to_downstream(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """result_file without result_path: file path variable must be available downstream.

        Previously, _build_state_schema only registered result_file keys when result_path
        was also present on the same state, causing LangGraph to silently drop the channel
        and leaving the variable unresolved (literal {var}) in downstream commands.
        """
        monkeypatch.chdir(tmp_path)

        flow_yaml = tmp_path / "result_file_only_flow.yaml"
        flow_yaml.write_text(
            "name: Result File Only Flow\n"
            "description: result_file without result_path must be accessible downstream\n"
            "start_at: write_state\n"
            "states:\n"
            "  write_state:\n"
            "    type: task\n"
            "    provider: system\n"
            "    command: \"echo 'hello from file'\"\n"
            "    result_file: $.written_file\n"
            "    next: read_state\n"
            "  read_state:\n"
            "    type: task\n"
            "    provider: system\n"
            '    command: "cat {written_file}"\n'
            "    result_path: $.content\n"
            "    end: true\n",
            encoding="utf-8",
        )

        thread_id = "test-result-file-only"
        result = engine.run_flow(flow_yaml, thread_id=thread_id, base_dir=tmp_path)

        assert isinstance(result, FlowResult)
        assert "written_file" in result.results, (
            f"result_file variable missing: {result.results}"
        )
        assert Path(result.results["written_file"]).is_absolute()
        assert "content" in result.results, (
            f"downstream state result missing: {result.results}"
        )
        assert "hello from file" in result.results["content"]


class TestResultFileRegression:
    """T019: Regression test — no data/ directory when result_file is not used."""

    def test_no_data_dir_without_result_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Run simple_flow.yaml (no result_file) and verify data/ dir is not created."""
        path = FIXTURES_DIR / "simple_flow.yaml"

        thread_id = "test-no-result-file"
        result = engine.run_flow(path, thread_id=thread_id, base_dir=tmp_path)

        run_dir = tmp_path / RUNS_DIR_NAME / thread_id
        data_dir = run_dir / RESULT_FILE_DATA_DIR

        assert not data_dir.exists(), (
            f"data/ directory should not be created when result_file is not used, "
            f"but found: {data_dir}"
        )
        assert "plan" in result.results, (
            f"Expected 'plan' in result.results: {result.results}"
        )
        assert "implementation" in result.results, (
            f"Expected 'implementation' in result.results: {result.results}"
        )
        assert "review" in result.results, (
            f"Expected 'review' in result.results: {result.results}"
        )

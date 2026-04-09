"""Integration tests for Map state checkpointing and resume."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from fdsx.core import engine
from fdsx.providers.system import SystemProvider


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


def write_flow_to_file(flow_obj, path: Path):
    """Serialize a Flow Pydantic model to a YAML file."""
    data = flow_obj.model_dump(mode="json")
    with path.open("w") as f:
        yaml.safe_dump(data, f)


class TestMapCheckpoint:
    def test_map_progress_file_written_per_iteration(self, temp_dir):
        """Progress file is written after each successful iteration."""
        from fdsx.models.flow import (
            Flow,
            IteratorDef,
            IteratorTaskState,
            MapState,
            PassState,
        )

        flow = Flow(
            name="Map Progress Test",
            description="Test progress file is written",
            start_at="setup",
            states={
                "setup": PassState(
                    type="pass",
                    parameters={"$.items": ["item1", "item2", "item3"]},
                    next="map_state",
                ),
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo_item",
                                provider="system",
                                command='echo "result-{item}"',
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    fail_fast=True,
                    next="after_map",
                ),
                "after_map": PassState(
                    type="pass",
                    parameters={"$.after_result": "done"},
                    end=True,
                ),
            },
        )

        flow_path = temp_dir / "map_progress_test.yaml"
        write_flow_to_file(flow, flow_path)

        base_dir = temp_dir / ".fdsx"
        thread_id = "test-map-progress"

        call_count = 0
        original_execute = SystemProvider().execute

        def stop_after_third_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise Exception("simulated interruption after 2 items")
            return original_execute(*args, **kwargs)

        with (
            pytest.raises(RuntimeError, match="simulated interruption"),
            patch.object(SystemProvider, "execute", side_effect=stop_after_third_call),
        ):
            engine.run_flow(
                flow_path,
                thread_id=thread_id,
                base_dir=base_dir,
            )

        progress_file = base_dir / "runs" / thread_id / "map_state" / "progress.json"
        assert progress_file.exists(), f"Progress file not found at {progress_file}"

        with progress_file.open() as f:
            progress = json.load(f)

        assert progress["completed_iterations"] == 2
        assert len(progress["results"]) == 2
        assert progress["results"][0] == "result-item1"
        assert progress["results"][1] == "result-item2"

    def test_map_resume_skips_completed_iterations(self, temp_dir):
        """Resume re-runs only incomplete iterations, not already-completed ones."""
        from fdsx.models.flow import (
            Flow,
            IteratorDef,
            IteratorTaskState,
            MapState,
            PassState,
        )

        flow = Flow(
            name="Map Resume Test",
            description="Test resume skips completed",
            start_at="setup",
            states={
                "setup": PassState(
                    type="pass",
                    parameters={"$.items": ["item1", "item2", "item3"]},
                    next="map_state",
                ),
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo_item",
                                provider="system",
                                command='echo "result-{item}"',
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    fail_fast=True,
                    next="after_map",
                ),
                "after_map": PassState(
                    type="pass",
                    parameters={"$.after_result": "done"},
                    end=True,
                ),
            },
        )

        flow_path = temp_dir / "map_resume_test.yaml"
        write_flow_to_file(flow, flow_path)

        base_dir = temp_dir / ".fdsx"
        thread_id = "test-map-resume"

        call_count = 0
        original_execute = SystemProvider().execute

        def stop_after_third_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise Exception("simulated interruption after 2 items")
            return original_execute(*args, **kwargs)

        with (
            pytest.raises(RuntimeError, match="simulated interruption"),
            patch.object(SystemProvider, "execute", side_effect=stop_after_third_call),
        ):
            engine.run_flow(
                flow_path,
                thread_id=thread_id,
                base_dir=base_dir,
            )

        resume_call_count = 0
        original_execute_2 = SystemProvider().execute

        def count_resume_calls(*args, **kwargs):
            nonlocal resume_call_count
            resume_call_count += 1
            return original_execute_2(*args, **kwargs)

        with patch.object(SystemProvider, "execute", side_effect=count_resume_calls):
            result = engine.resume_flow(
                thread_id,
                base_dir,
                flow_path,
            )

        assert result["results"] is not None
        assert len(result["results"]) == 3
        assert result["results"][0] == "result-item1"
        assert result["results"][1] == "result-item2"
        assert result["results"][2] == "result-item3"

        progress_file = base_dir / "runs" / thread_id / "map_state" / "progress.json"
        assert progress_file.exists()
        with progress_file.open() as f:
            progress = json.load(f)
        assert progress["completed_iterations"] == 3, (
            "After resume, progress should show all 3 iterations completed"
        )

        assert resume_call_count == 1, (
            f"Expected only 1 provider call on resume (item3), got {resume_call_count}"
        )

    def test_map_resume_merges_results_correctly(self, temp_dir):
        """Resume produces correct final results combining completed and new."""
        from fdsx.models.flow import (
            Flow,
            IteratorDef,
            IteratorTaskState,
            MapState,
            PassState,
        )

        flow = Flow(
            name="Map Merge Test",
            description="Test results merge on resume",
            start_at="setup",
            states={
                "setup": PassState(
                    type="pass",
                    parameters={"$.items": ["a", "b", "c", "d"]},
                    next="map_state",
                ),
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="process",
                                provider="system",
                                command='echo "out-{item}"',
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    fail_fast=True,
                    end=True,
                ),
            },
        )

        flow_path = temp_dir / "map_merge_test.yaml"
        write_flow_to_file(flow, flow_path)

        base_dir = temp_dir / ".fdsx"
        thread_id = "test-map-merge"

        call_count = 0
        original_execute = SystemProvider().execute

        def stop_after_fourth_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 4:
                raise Exception("simulated interruption after 3 items")
            return original_execute(*args, **kwargs)

        with (
            pytest.raises(RuntimeError, match="simulated interruption"),
            patch.object(SystemProvider, "execute", side_effect=stop_after_fourth_call),
        ):
            engine.run_flow(
                flow_path,
                thread_id=thread_id,
                base_dir=base_dir,
            )

        result = engine.resume_flow(
            thread_id,
            base_dir,
            flow_path,
        )

        assert result["results"] is not None
        assert len(result["results"]) == 4
        assert result["results"][0] == "out-a"
        assert result["results"][1] == "out-b"
        assert result["results"][2] == "out-c"
        assert result["results"][3] == "out-d"

    def test_map_progress_file_not_written_when_run_dir_empty(self, temp_dir):
        """When run_dir is empty (direct invoke), no progress file is written."""
        from fdsx.core.compiler import compile_flow
        from fdsx.models.flow import (
            Flow,
            IteratorDef,
            IteratorTaskState,
            MapState,
        )

        flow = Flow(
            name="Direct Invoke Test",
            description="Test without run_dir",
            start_at="map_state",
            states={
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo",
                                provider="system",
                                command='echo "result-{item}"',
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    end=True,
                ),
            },
        )

        compiled = compile_flow(flow)
        initial_state = {
            "items": ["x", "y"],
            "_meta": {
                "thread_id": "test",
                "flow_path": "",
                "flow_name": "test",
                "run_dir": "",
            },
            "_state_iterations": {},
        }

        result = compiled.graph.invoke(initial_state)

        assert result["results"] is not None
        assert len(result["results"]) == 2
        assert result["results"][0] == "result-x"
        assert result["results"][1] == "result-y"

    def test_map_progress_file_ignored_when_items_length_mismatch(self, temp_dir):
        """Progress file is ignored when saved results length exceeds current items."""
        from fdsx.models.flow import (
            Flow,
            IteratorDef,
            IteratorTaskState,
            MapState,
            PassState,
        )

        flow = Flow(
            name="Length Mismatch Test",
            description="Test progress ignored when items changed",
            start_at="setup",
            states={
                "setup": PassState(
                    type="pass",
                    parameters={"$.items": ["a", "b"]},
                    next="map_state",
                ),
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo",
                                provider="system",
                                command='echo "result-{item}"',
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    fail_fast=True,
                    end=True,
                ),
            },
        )

        flow_path = temp_dir / "map_length_test.yaml"
        write_flow_to_file(flow, flow_path)

        base_dir = temp_dir / ".fdsx"
        thread_id = "test-map-length-mismatch"

        engine.run_flow(
            flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
        )

        progress_file = base_dir / "runs" / thread_id / "map_state" / "progress.json"
        assert progress_file.exists()

        with progress_file.open() as f:
            progress = json.load(f)
        assert progress["completed_iterations"] == 2

        with progress_file.open("w") as f:
            json.dump(
                {
                    "completed_iterations": 5,
                    "results": ["old-a", "old-b", "old-c", "old-d", "old-e"],
                },
                f,
            )

        from fdsx.models.flow import PassState as PassStateModel

        flow_changed_items = Flow(
            name="Length Mismatch Test Changed",
            description="Test progress ignored when items changed",
            start_at="setup",
            states={
                "setup": PassStateModel(
                    type="pass",
                    parameters={"$.items": ["a", "b", "c"]},
                    next="map_state",
                ),
                "map_state": MapState(
                    type="map",
                    items_path="$.items",
                    iterator=IteratorDef(
                        states=[
                            IteratorTaskState(
                                name="echo",
                                provider="system",
                                command='echo "result-{item}"',
                                result_path="$.result",
                            )
                        ]
                    ),
                    result_path="$.results",
                    fail_fast=True,
                    end=True,
                ),
            },
        )

        write_flow_to_file(flow_changed_items, flow_path)

        result = engine.run_flow(
            flow_path,
            thread_id=thread_id,
            base_dir=base_dir,
        )

        assert result["results"] is not None
        assert len(result["results"]) == 3
        assert result["results"][0] == "result-a"
        assert result["results"][1] == "result-b"
        assert result["results"][2] == "result-c"

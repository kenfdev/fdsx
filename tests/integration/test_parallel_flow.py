import pytest

from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow
from fdsx.logging.recorder import LOGS_DIR_NAME, RUNS_DIR_NAME
from tests import FIXTURES_DIR


class TestParallelFlow:
    def test_parallel_review_majority_aggregation(self, tmp_path):
        """Test parallel review with majority aggregation and choice routing."""
        path = FIXTURES_DIR / "parallel_review.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "reviews" in result
        assert len(result["reviews"]) == 3

        for review in result["reviews"]:
            assert "output" in review
            assert "exit_code" in review

        assert "decision" in result
        assert result["decision"] == "APPROVED"

    def test_parallel_branch_results_have_output_field(self, tmp_path):
        """Verify branch results array contains output field."""
        path = FIXTURES_DIR / "parallel_review.yaml"

        result = run_flow(path, base_dir=tmp_path)

        assert "reviews" in result
        assert len(result["reviews"]) == 3
        for review in result["reviews"]:
            assert "output" in review


class TestParallelMinSuccess:
    def test_min_success_tolerates_partial_failure(self, tmp_path):
        """Test that min_success allows flow to continue with partial branch failures."""
        path = FIXTURES_DIR / "parallel_min_success.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"

        result = run_flow(path, base_dir=tmp_path)

        assert "results" in result
        assert len(result["results"]) == 3

        successful = sum(1 for r in result["results"] if r.get("exit_code") == 0)
        assert successful == 2

        assert "success_check" in result
        assert result["success_check"] == "Flow continued after partial failure"

    def test_min_success_failure_raises_error(self):
        """Test that when too many branches fail, flow raises error."""
        from fdsx.models.flow import Flow, ParallelState, Branch

        flow = Flow(
            name="Parallel All Fail",
            description="Test flow for min_success failure",
            start_at="parallel_state",
            states={
                "parallel_state": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(
                            provider="system",
                            command="exit 1",
                            retry=0,
                        ),
                        Branch(
                            provider="system",
                            command="exit 1",
                            retry=0,
                        ),
                        Branch(
                            provider="system",
                            command="exit 1",
                            retry=0,
                        ),
                    ],
                    result_path="$.results",
                    min_success=2,
                    end=True,
                ),
            },
        )

        from fdsx.core.compiler import compile_flow

        compiled = compile_flow(flow)

        with pytest.raises(RuntimeError, match="only .* branches succeeded"):
            compiled.graph.invoke({})


class TestParallelBranchLabeling:
    """T012: Verify parallel branch StreamLogger labeling (FR-2.2, FR-2.3, FR-2.4)."""

    def test_parallel_branches_use_state_name_label(self, tmp_path, capsys):
        """Each branch's terminal output is labeled with the parallel state name.

        FR-2.2: prefix is '[state_name]', no branch index suffix.
        FR-2.3: output from all branches is labeled with the same state name.
        """
        base_dir = tmp_path / ".fdsx"
        run_flow(
            flow_path=FIXTURES_DIR / "parallel_review.yaml",
            base_dir=base_dir,
        )

        captured = capsys.readouterr()
        # verify no branch-index suffixed labels appear
        for line in captured.err.splitlines():
            if line.startswith("["):
                label_end = line.find("]")
                label = line[1:label_end]
                # Labels must not contain numeric suffixes like "review_parallel-0"
                assert not label.endswith(("-0", "-1", "-2")), (
                    f"Branch index suffix found in label: {label!r}"
                )

    def test_parallel_log_files_created_per_state(self, tmp_path):
        """Each parallel branch produces a log file named with branch label (FR-2.4).

        Verifies that .fdsx/runs/<thread_id>/logs/<state_name>_branch<N>_<iteration>.log
        is created per branch. No old-style dash-index suffix in the filename.
        """
        from fdsx.models.flow import Branch, Flow, ParallelState

        flow = Flow(
            name="Log File Test",
            description="Parallel log file test",
            start_at="review_parallel",
            states={
                "review_parallel": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(provider="system", command="echo alpha", retry=0),
                        Branch(provider="system", command="echo beta", retry=0),
                    ],
                    result_path="$.results",
                    end=True,
                ),
            },
        )

        from fdsx.core.compiler import compile_flow

        base_dir = tmp_path / ".fdsx"
        thread_id = "test-parallel-log"
        log_dir = base_dir / RUNS_DIR_NAME / thread_id / LOGS_DIR_NAME

        from fdsx.logging.recorder import RunRecorder

        recorder = RunRecorder(thread_id=thread_id, flow_name="Log File Test")
        compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
        compiled.graph.invoke({})

        # Log files for each branch should exist
        branch1_log = log_dir / "review_parallel_branch1_1.log"
        branch2_log = log_dir / "review_parallel_branch2_1.log"
        assert branch1_log.exists(), f"Expected log file at {branch1_log}"
        assert branch2_log.exists(), f"Expected log file at {branch2_log}"

        content1 = branch1_log.read_text(encoding="utf-8")
        content2 = branch2_log.read_text(encoding="utf-8")
        assert "alpha" in content1
        assert "beta" in content2

        # No old-style dash-index-suffixed log files should exist
        assert not (log_dir / "review_parallel-0_1.log").exists()
        assert not (log_dir / "review_parallel-1_1.log").exists()

    def test_no_log_file_for_empty_output_branch(self, tmp_path):
        """No log file is created when a branch produces no output (FR-2.6)."""
        from fdsx.models.flow import Branch, Flow, ParallelState

        flow = Flow(
            name="Empty Log Test",
            description="Branch with no output",
            start_at="parallel_state",
            states={
                "parallel_state": ParallelState(
                    type="parallel",
                    branches=[
                        Branch(provider="system", command="true", retry=0),
                    ],
                    result_path="$.results",
                    end=True,
                ),
            },
        )

        from fdsx.core.compiler import compile_flow

        base_dir = tmp_path / ".fdsx"
        thread_id = "test-empty-log"
        log_dir = base_dir / RUNS_DIR_NAME / thread_id / LOGS_DIR_NAME

        from fdsx.logging.recorder import RunRecorder

        recorder = RunRecorder(thread_id=thread_id, flow_name="Empty Log Test")
        compiled = compile_flow(flow, recorder=recorder, log_dir=log_dir)
        compiled.graph.invoke({})

        log_file = log_dir / "parallel_state_branch1_1.log"
        assert not log_file.exists(), (
            f"Log file should not exist for branch with no output: {log_file}"
        )

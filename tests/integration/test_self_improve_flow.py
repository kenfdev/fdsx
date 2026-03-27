from pathlib import Path
from unittest.mock import patch

from fdsx.core.engine import run_flow
from fdsx.core.loader import load_flow
from fdsx.providers.base import ProviderResult
from tests import FIXTURES_DIR


def _make_mock_subprocess(stdout, stderr=""):
    """Create a mock that returns the same output for all calls."""
    return ProviderResult(exit_code=0, stdout=stdout, stderr=stderr)


class TestSelfImproveWorkflow:
    def test_self_improve_workflow_loads(self):
        """Test that the self-improve workflow YAML is valid."""
        path = FIXTURES_DIR / "self_improve_flow" / "workflow.yaml"

        flow, errors = load_flow(path)
        assert flow is not None, f"Failed to load: {errors}"
        assert flow.name == "Self-Improve Flow"

    def test_collect_data_extracts_has_runs_keyword(self, tmp_path):
        """Test that collect_data extracts HAS_RUNS when runs exist."""
        path = FIXTURES_DIR / "self_improve_flow" / "workflow.yaml"

        runs_dir = tmp_path / ".fdsx" / "runs"
        runs_dir.mkdir(parents=True)

        run_dir = runs_dir / "2026-03-27-000000-test"
        run_dir.mkdir()
        run_json = run_dir / "run.json"
        run_json.write_text(
            '{"thread_id":"2026-03-27-000000-test","flow_name":"test","flow_version":"1.0",'
            '"started_at":"2026-03-27T00:00:00","status":"completed","states":[]}'
        )

        logs_dir = run_dir / "logs"
        logs_dir.mkdir()
        (logs_dir / "test_1.log").write_text("test log")

        fake = ProviderResult(
            exit_code=0,
            stdout="run_dir|flow|completed|state|task|10|success|0\nHAS_RUNS",
            stderr="",
        )
        with patch("fdsx.providers.system._run_subprocess", return_value=fake):
            result = run_flow(path, base_dir=tmp_path)

        assert "collect_decision" in result
        assert result["collect_decision"] == "HAS_RUNS"

    def test_collect_data_extracts_no_runs_keyword(self, tmp_path):
        """Test that collect_data extracts NO_RUNS when no runs exist."""
        path = FIXTURES_DIR / "self_improve_flow" / "workflow.yaml"

        fake = ProviderResult(exit_code=0, stdout="NO_RUNS", stderr="")
        with patch("fdsx.providers.system._run_subprocess", return_value=fake):
            result = run_flow(path, base_dir=tmp_path)

        assert "collect_decision" in result
        assert result["collect_decision"] == "NO_RUNS"

    def test_choice_routes_to_clean_path_when_no_runs(self, tmp_path):
        """Test that choice routes to update_timestamp_clean when NO_RUNS."""
        path = FIXTURES_DIR / "self_improve_flow" / "workflow.yaml"

        call_count = [0]

        def fake_subprocess(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return ProviderResult(exit_code=0, stdout="NO_RUNS", stderr="")
            return ProviderResult(
                exit_code=0,
                stdout="mv: not found\nNo new runs",
                stderr="",
            )

        with patch(
            "fdsx.providers.system._run_subprocess", side_effect=fake_subprocess
        ):
            result = run_flow(path, base_dir=tmp_path)

        assert "timestamp_update" in result
        assert call_count[0] >= 2

    def test_choice_routes_to_write_lessons_when_has_runs(self, tmp_path):
        """Test that choice routes to write_lessons when HAS_RUNS."""
        path = FIXTURES_DIR / "self_improve_flow" / "workflow.yaml"

        call_count = [0]

        def fake_subprocess(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return ProviderResult(
                    exit_code=0,
                    stdout="run_dir|flow|completed|state|task|10|success|0\nHAS_RUNS",
                    stderr="",
                )
            return ProviderResult(exit_code=0, stdout="Lessons written", stderr="")

        with patch(
            "fdsx.providers.system._run_subprocess", side_effect=fake_subprocess
        ):
            result = run_flow(path, base_dir=tmp_path)

        assert "collect_decision" in result
        assert result["collect_decision"] == "HAS_RUNS"
        assert "lessons" in result


class TestCollectDataScript:
    def test_script_outputs_no_runs_when_no_runs_dir(self, tmp_path):
        """Test collect_data.sh outputs NO_RUNS when runs directory doesn't exist."""
        script = (
            Path(__file__).parent.parent
            / "fixtures"
            / "self_improve_flow"
            / "collect_data.sh"
        )

        import subprocess

        result = subprocess.run(
            [str(script)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "NO_RUNS" in result.stdout

    def test_script_outputs_has_runs_when_runs_exist(self, tmp_path):
        """Test collect_data.sh outputs HAS_RUNS when run directories exist."""
        script = (
            Path(__file__).parent.parent
            / "fixtures"
            / "self_improve_flow"
            / "collect_data.sh"
        )

        runs_dir = tmp_path / ".fdsx" / "runs"
        runs_dir.mkdir(parents=True)
        run_dir = runs_dir / "2026-03-27-000000-test"
        run_dir.mkdir()
        run_json = run_dir / "run.json"
        run_json.write_text(
            '{"thread_id":"2026-03-27-000000-test","flow_name":"test","flow_version":"1.0",'
            '"started_at":"2026-03-27T00:00:00","status":"completed","states":[]}'
        )

        import subprocess

        result = subprocess.run(
            [str(script)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "HAS_RUNS" in result.stdout

    def test_script_filters_runs_after_last_run_file(self, tmp_path):
        """Test that collect_data.sh filters runs based on last-run file."""
        script = (
            Path(__file__).parent.parent
            / "fixtures"
            / "self_improve_flow"
            / "collect_data.sh"
        )

        runs_dir = tmp_path / ".fdsx" / "runs"
        runs_dir.mkdir(parents=True)

        old_run = runs_dir / "2026-03-26-000000-old"
        old_run.mkdir()
        (old_run / "run.json").write_text(
            '{"thread_id":"2026-03-26-000000-old","flow_name":"old","flow_version":"1.0",'
            '"started_at":"2026-03-26T00:00:00","status":"completed","states":[]}'
        )

        new_run = runs_dir / "2026-03-27-000000-new"
        new_run.mkdir()
        (new_run / "run.json").write_text(
            '{"thread_id":"2026-03-27-000000-new","flow_name":"new","flow_version":"1.0",'
            '"started_at":"2026-03-27T00:00:00","status":"completed","states":[]}'
        )

        last_run_file = tmp_path / ".fdsx" / "self-improve-last-run"
        last_run_file.write_text("2026-03-26-000000-old")

        import subprocess

        result = subprocess.run(
            [str(script)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "HAS_RUNS" in result.stdout
        assert "2026-03-27-000000-new" in result.stdout
        assert "2026-03-26-000000-old" not in result.stdout

    def test_script_outputs_no_runs_when_all_runs_already_seen(self, tmp_path):
        """Test collect_data.sh outputs NO_RUNS when all runs are already processed."""
        script = (
            Path(__file__).parent.parent
            / "fixtures"
            / "self_improve_flow"
            / "collect_data.sh"
        )

        runs_dir = tmp_path / ".fdsx" / "runs"
        runs_dir.mkdir(parents=True)

        old_run = runs_dir / "2026-03-26-000000-old"
        old_run.mkdir()
        (old_run / "run.json").write_text(
            '{"thread_id":"2026-03-26-000000-old","flow_name":"old","flow_version":"1.0",'
            '"started_at":"2026-03-26T00:00:00","status":"completed","states":[]}'
        )

        last_run_file = tmp_path / ".fdsx" / "self-improve-last-run"
        last_run_file.write_text("2026-03-26-000000-old")

        import subprocess

        result = subprocess.run(
            [str(script)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "NO_RUNS" in result.stdout
        assert "HAS_RUNS" not in result.stdout

    def test_script_writes_pending_file_when_has_runs(self, tmp_path):
        """Test collect_data.sh writes .pending file with newest run dir."""
        script = (
            Path(__file__).parent.parent
            / "fixtures"
            / "self_improve_flow"
            / "collect_data.sh"
        )

        runs_dir = tmp_path / ".fdsx" / "runs"
        runs_dir.mkdir(parents=True)
        run_dir = runs_dir / "2026-03-27-000000-test"
        run_dir.mkdir()
        (run_dir / "run.json").write_text(
            '{"thread_id":"2026-03-27-000000-test","flow_name":"test","flow_version":"1.0",'
            '"started_at":"2026-03-27T00:00:00","status":"completed","states":[]}'
        )

        import subprocess

        result = subprocess.run(
            [str(script)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        pending = tmp_path / ".fdsx" / "self-improve-last-run.pending"
        assert pending.exists()
        assert pending.read_text() == "2026-03-27-000000-test"

"""Integration tests for thread ID format in run directory and resume."""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.e2e.cli_test_utils import fixture_path


def run_fdsx_run(cwd: str | Path) -> subprocess.CompletedProcess[str]:
    """Run fdsx run command in specified directory."""
    command = [
        sys.executable,
        "-m",
        "fdsx.cli.main",
        "run",
        fixture_path("simple_flow.yaml"),
    ]
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestThreadIdFormatIntegration:
    """FR-1.4: fdsx run produces run directory with new format (not UUID)."""

    def test_run_creates_run_dir_with_new_format(self) -> None:
        """fdsx run must create .fdsx/runs/<thread_id>/run.json with new format ID."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_fdsx_run(tmp_dir)
            assert result.returncode == 0, f"fdsx run failed: {result.stderr}"

            runs_dir = Path(tmp_dir) / ".fdsx" / "runs"
            assert runs_dir.exists(), ".fdsx/runs directory not created"

            thread_ids = [d.name for d in runs_dir.iterdir() if d.is_dir()]
            assert len(thread_ids) >= 1, "No run directory created"

            thread_id = thread_ids[0]
            thread_id_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}-[a-f0-9]{6}$")
            assert thread_id_pattern.match(thread_id), (
                f"Run directory has UUID format instead of new format: {thread_id}"
            )

            thread_dir = runs_dir / thread_id
            run_json = thread_dir / "run.json"
            assert run_json.exists(), f"run.json not found in {thread_dir}"

    def test_run_id_not_uuid_format(self) -> None:
        """Run directory name must NOT be UUID format (no 8-4-4-4-12 pattern)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = run_fdsx_run(tmp_dir)
            assert result.returncode == 0, f"fdsx run failed: {result.stderr}"

            runs_dir = Path(tmp_dir) / ".fdsx" / "runs"
            thread_ids = [d.name for d in runs_dir.iterdir() if d.is_dir()]
            assert len(thread_ids) >= 1, "No run directory created"

            thread_id = thread_ids[0]
            uuid_pattern = re.compile(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
            )
            assert not uuid_pattern.match(thread_id), (
                f"Run directory has UUID format: {thread_id}"
            )


class TestResumeWithNewFormat:
    """FR-1.5: fdsx resume --thread-id <new-format-id> resolves correctly."""

    def test_resume_with_new_format_thread_id(self) -> None:
        """resume --thread-id with new format ID must find and resume the run."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            run_result = run_fdsx_run(tmp_dir)
            assert run_result.returncode == 0, f"fdsx run failed: {run_result.stderr}"

            runs_dir = Path(tmp_dir) / ".fdsx" / "runs"
            thread_ids = [d.name for d in runs_dir.iterdir() if d.is_dir()]
            assert len(thread_ids) >= 1, "No run directory found"
            thread_id = thread_ids[0]

            resume_command = [
                sys.executable,
                "-m",
                "fdsx.cli.main",
                "resume",
                "--thread-id",
                thread_id,
            ]
            resume_result = subprocess.run(
                resume_command,
                cwd=tmp_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )

            assert resume_result.returncode == 0, (
                f"resume failed with thread_id {thread_id}: {resume_result.stderr}"
            )

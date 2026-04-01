import tempfile
from pathlib import Path

from tests.e2e.cli_test_utils import run_fdsx


class TestAutoInit:
    def test_first_run_scaffolding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            result = run_fdsx(["--interactive", "run"], cwd=tmp_path)

            assert result.returncode == 0
            assert result.stdout == ""

            fdsx_dir = tmp_path / ".fdsx"
            assert not fdsx_dir.exists()

            assert "No .fdsx/ directory found" in result.stderr
            assert "Run 'fdsx init'" in result.stderr
            assert "Initialized .fdsx/" not in result.stderr
            assert "Created:" not in result.stderr

    def test_noop_when_fdsx_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / ".fdsx").mkdir()

            result = run_fdsx(
                ["--interactive", "run", "some-workflow.yaml"], cwd=tmp_path
            )

            assert "Initialized .fdsx/" not in result.stderr
            assert "Created:" not in result.stderr
            assert "Next steps:" not in result.stderr

    def test_ci_flag_skips_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            result = run_fdsx(["--ci", "run"], cwd=tmp_path)

            assert not (tmp_path / ".fdsx").exists()

            assert "Initialized .fdsx/" not in result.stderr
            assert "No .fdsx/ directory found" in result.stderr
            assert "Run 'fdsx init'" in result.stderr

            assert result.returncode == 0

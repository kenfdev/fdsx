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
            assert fdsx_dir.is_dir()

            config_path = fdsx_dir / "config.yaml"
            assert config_path.is_file()

            workflows_dir = fdsx_dir / "workflows"
            assert workflows_dir.is_dir()

            workflow_dir = workflows_dir / "plan-implement-review"
            assert workflow_dir.is_dir()
            assert (workflow_dir / "workflow.yaml").is_file()
            assert (workflow_dir / "plan-prompt.txt").is_file()
            assert (workflow_dir / "implement-prompt.txt").is_file()

            stderr = result.stderr
            assert "Initialized .fdsx/" in stderr
            assert "Created:" in stderr
            assert "Next steps:" in stderr
            assert "config.yaml" in stderr

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def fixture_path(*parts: str) -> str:
    """Return an absolute path under tests/fixtures/."""
    return str(FIXTURES_DIR.joinpath(*parts).resolve())


def run_fdsx(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    input: str | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the CLI from an isolated working directory unless one is supplied."""
    command = [sys.executable, "-m", "fdsx.cli.main", *args]
    if cwd is not None:
        return subprocess.run(
            command,
            cwd=cwd,
            input=input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        return subprocess.run(
            command,
            cwd=tmp_dir,
            input=input,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

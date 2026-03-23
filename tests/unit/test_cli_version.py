import re

from typer.testing import CliRunner

from fdsx.cli.main import app


class TestCliVersion:
    def test_version_flag_outputs_version_and_exits(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["--version"])

        assert result.exit_code == 0, (
            f"Expected exit 0, got {result.exit_code}. output: {result.output}"
        )
        assert re.fullmatch(r"fdsx \d+\.\d+\.\d+[^\n]*\n", result.output), (
            f"Expected output matching 'fdsx X.Y.Z', got: {result.output!r}"
        )

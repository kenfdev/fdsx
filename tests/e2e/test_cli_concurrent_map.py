"""Real local processes exercise the same repeatable smoke driver as maintainers."""

import pytest
from scripts.verify_concurrent_map import verify


@pytest.mark.parametrize("form", ["legacy", "local"])
@pytest.mark.parametrize("scenario", ["success", "failure", "interrupt"])
def test_cli_concurrency_resume_and_interrupt(tmp_path, form, scenario):
    assert verify(form, scenario, tmp_path / "case")["result"] == "passed"

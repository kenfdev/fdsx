import pytest


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Prevent tests from writing .fdsx/ artifacts into the project root."""
    monkeypatch.chdir(tmp_path)

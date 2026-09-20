import os

import pytest


def pytest_xdist_auto_num_workers(config):
    """Use at most half the available CPUs; keep serial runs available via -n 0."""
    if hasattr(os, "sched_getaffinity"):
        available = len(os.sched_getaffinity(0))
    else:
        available = os.cpu_count() or 1
    return max(1, available // 2)


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """Isolate project and user-level fdsx state from the developer machine."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

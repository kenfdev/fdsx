"""Integration test fixtures for cursor provider."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_cursor_agent_binary(request):
    """Auto-mock 'agent' binary presence for cursor provider integration tests.

    Tests that patch _run_subprocess do not also patch shutil.which, so in a CI
    environment where the 'agent' binary is not installed the pre-flight binary
    check in CursorProvider.execute() would raise CursorProviderError before the
    _run_subprocess mock ever fires.

    This fixture makes shutil.which("agent") return a fake path for all tests in
    test_cursor_provider.py.  Tests that explicitly re-patch shutil.which (e.g.
    test_missing_binary_raises_domain_error) override this fixture with their own
    inner patch, so the binary-absent path is still exercised correctly.

    The patch targets fdsx.providers.cursor.shutil specifically, so no other
    integration tests are affected.
    """
    if "test_cursor_provider" not in str(request.node.fspath):
        yield
        return

    with patch("fdsx.providers.cursor.shutil.which", return_value="/usr/bin/agent"):
        yield

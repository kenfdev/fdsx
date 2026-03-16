import pytest
from pathlib import Path

from fdsx.core.engine import run_flow


class TestLoopEnforcement:
    def test_max_loop_prevents_infinite_cycle(self):
        """R2-F5: flow.max_loop must bound execution via LangGraph recursion_limit."""
        path = Path("tests/fixtures/loop_flow.yaml")
        with pytest.raises(RuntimeError, match="(?i)(recursion|loop|limit|GraphRecursion)"):
            run_flow(path)

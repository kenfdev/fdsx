"""E2E smoke test for fallback visibility stderr output (T005)."""

from unittest.mock import patch

from fdsx.core.compiler import compile_flow
from fdsx.core.config import FdsxConfig
from fdsx.models.flow import ExtractionFallback, ExtractRule, Flow, TaskState
from fdsx.providers.base import ProviderResult

_MODEL = "claude-sonnet-4-5"


class TestFallbackVisibilitySmoke:
    def test_global_fallback_recovered_prints_stderr_line(self, capsys):
        """Running a workflow whose extraction misses but global fallback recovers
        exits cleanly and stderr contains ↩ fallback(global) → recovered:"""
        flow = Flow(
            name="fallback_smoke",
            description="E2E smoke test for fallback visibility",
            start_at="classify",
            states={
                "classify": TaskState(
                    type="task",
                    provider="claude",
                    model=_MODEL,
                    prompt_template="classify this output",
                    result_path="$.task_result",
                    extract=ExtractRule(
                        strategy=["keyword"],
                        pattern="APPROVED|REJECTED",
                        result_path="$.decision",
                    ),
                    retry=0,
                    end=True,
                )
            },
        )
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        # Call 1: main task — output misses keyword
        # Call 2: global fallback — returns APPROVED
        responses = [
            ProviderResult(exit_code=0, stdout="processing complete", stderr=""),
            ProviderResult(exit_code=0, stdout="APPROVED", stderr=""),
        ]

        with patch("fdsx.providers.claude._run_subprocess", side_effect=responses):
            compiled = compile_flow(flow, config=config)
            result = compiled.graph.invoke({})

        assert result.get("decision") == "APPROVED"

        captured = capsys.readouterr()
        assert "↩ fallback(global) → recovered:" in captured.err, (
            f"Expected fallback visibility line in stderr, got: {captured.err!r}"
        )

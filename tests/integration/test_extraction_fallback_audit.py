"""Integration tests for extraction fallback audit records and stderr output (T005).

Each test runs a compiled flow with mocked providers, then asserts on:
  - recorder.states: whether fallback_invocations key is present and correct
  - capsys.readouterr().err: whether ↩ fallback(...) → ... line appears
"""

from unittest.mock import MagicMock, patch

import pytest

from fdsx.core.compiler import compile_flow
from fdsx.core.config import FdsxConfig
from fdsx.logging.recorder import RunRecorder
from fdsx.models.flow import (
    Branch,
    ExtractionFallback,
    ExtractRule,
    Flow,
    IteratorDef,
    IteratorTaskState,
    LLMClassifyFallback,
    MapState,
    ParallelState,
    TaskState,
)
from fdsx.providers.base import ProviderResult

_MODEL = "claude-sonnet-4-5"


def _make_recorder() -> RunRecorder:
    return RunRecorder(thread_id="audit-test-abc", flow_name="audit-test")


def _keyword_rule() -> ExtractRule:
    return ExtractRule(
        strategy=["keyword"],
        pattern="APPROVED|REJECTED",
        result_path="$.decision",
    )


def _claude_task(state_name: str = "classify", end: bool = True) -> TaskState:
    return TaskState(
        type="task",
        provider="claude",
        model=_MODEL,
        prompt_template="classify",
        result_path="$.task_result",
        extract=_keyword_rule(),
        retry=0,
        end=end,
    )


def _single_task_flow(
    state_name: str = "classify",
    extract_rule: ExtractRule | None = None,
    extraction_fallback: ExtractionFallback | bool | None = None,
) -> Flow:
    rule = extract_rule or _keyword_rule()
    task = TaskState(
        type="task",
        provider="claude",
        model=_MODEL,
        prompt_template="classify",
        result_path="$.task_result",
        extract=rule,
        retry=0,
        end=True,
    )
    kwargs: dict = dict(
        name="audit_test",
        description="audit test",
        start_at=state_name,
        states={state_name: task},
    )
    if extraction_fallback is not None:
        kwargs["extraction_fallback"] = extraction_fallback
    return Flow(**kwargs)


class TestGlobalFallbackAudit:
    def test_global_fallback_recovered_record_has_source_global(self, capsys):
        """run.json state has source=global, outcome=recovered, value_preview set;
        stderr contains ↩ fallback(global) → recovered:"""
        recorder = _make_recorder()
        flow = _single_task_flow()
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        responses = [
            ProviderResult(exit_code=0, stdout="processing complete", stderr=""),
            ProviderResult(exit_code=0, stdout="APPROVED", stderr=""),
        ]
        with patch("fdsx.providers.claude._run_subprocess", side_effect=responses):
            compiled = compile_flow(flow, config=config, recorder=recorder)
            compiled.graph.invoke({})

        state = recorder._find_state_by_name("classify")
        assert "fallback_invocations" in state, (
            f"Expected fallback_invocations in state keys, got: {list(state.keys())}"
        )
        records = state["fallback_invocations"]
        assert len(records) == 1
        record = records[0]
        assert record["source"] == "global"
        assert record["outcome"] == "recovered"
        assert record.get("value_preview"), (
            "value_preview should be non-empty on recovered"
        )

        captured = capsys.readouterr()
        assert "↩ fallback(global) → recovered:" in captured.err, (
            f"Expected fallback line in stderr, got: {captured.err!r}"
        )


class TestWorkflowOverrideAudit:
    def test_workflow_override_record_has_source_workflow(self):
        """When extraction_fallback is set on the flow (workflow level), record has source=workflow."""
        recorder = _make_recorder()
        flow = _single_task_flow(
            extraction_fallback=ExtractionFallback(provider="claude")
        )
        # No global config fallback; flow-level override fires
        config = FdsxConfig()

        responses = [
            ProviderResult(exit_code=0, stdout="missed", stderr=""),
            ProviderResult(exit_code=0, stdout="APPROVED", stderr=""),
        ]
        with patch("fdsx.providers.claude._run_subprocess", side_effect=responses):
            compiled = compile_flow(flow, config=config, recorder=recorder)
            compiled.graph.invoke({})

        state = recorder._find_state_by_name("classify")
        assert "fallback_invocations" in state
        assert state["fallback_invocations"][0]["source"] == "workflow"


class TestPerRuleFallbackAudit:
    def test_per_rule_fallback_record_has_source_rule(self):
        """Per-rule LLMClassifyFallback fires: record has source=rule."""
        recorder = _make_recorder()
        rule_with_fallback = ExtractRule(
            strategy=["keyword"],
            pattern="APPROVED|REJECTED",
            result_path="$.decision",
            fallback=LLMClassifyFallback(
                provider="claude",
                prompt="Please classify the following output as APPROVED or REJECTED.",
            ),
        )
        flow = _single_task_flow(extract_rule=rule_with_fallback)
        config = FdsxConfig()

        responses = [
            ProviderResult(exit_code=0, stdout="missed", stderr=""),
            ProviderResult(exit_code=0, stdout="APPROVED", stderr=""),
        ]
        with patch("fdsx.providers.claude._run_subprocess", side_effect=responses):
            compiled = compile_flow(flow, config=config, recorder=recorder)
            compiled.graph.invoke({})

        state = recorder._find_state_by_name("classify")
        assert "fallback_invocations" in state
        assert state["fallback_invocations"][0]["source"] == "rule"


class TestRejectedOutcomeAudit:
    def test_rejected_outcome_has_no_error_kind(self):
        """When fallback returns out-of-set value, outcome=rejected and no error_kind key."""
        recorder = _make_recorder()
        flow = _single_task_flow()
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        responses = [
            ProviderResult(exit_code=0, stdout="missed", stderr=""),
            ProviderResult(exit_code=0, stdout="MAYBE", stderr=""),
        ]
        with patch("fdsx.providers.claude._run_subprocess", side_effect=responses):
            compiled = compile_flow(flow, config=config, recorder=recorder)
            with pytest.raises(RuntimeError):
                compiled.graph.invoke({})

        state = recorder._find_state_by_name("classify")
        assert "fallback_invocations" in state
        record = state["fallback_invocations"][0]
        assert record["outcome"] == "rejected"
        assert "error_kind" not in record


class TestTimeoutFallbackAudit:
    def test_timeout_outcome_has_error_kind_timeout(self):
        """When fallback provider times out, outcome=error, error_kind=timeout."""
        recorder = _make_recorder()
        flow = _single_task_flow()
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        stub_provider = MagicMock()
        stub_provider.execute.side_effect = [
            # First call: main task misses keyword (returned via normal subprocess mock)
        ]

        main_response = ProviderResult(exit_code=0, stdout="missed", stderr="")

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return main_response
            raise TimeoutError("timed out")

        with patch("fdsx.providers.claude._run_subprocess", side_effect=side_effect):
            compiled = compile_flow(flow, config=config, recorder=recorder)
            with pytest.raises(RuntimeError):
                compiled.graph.invoke({})

        state = recorder._find_state_by_name("classify")
        assert "fallback_invocations" in state
        record = state["fallback_invocations"][0]
        assert record["outcome"] == "error"
        assert record["error_kind"] == "timeout"


class TestFallbackDisabledAudit:
    def test_extraction_fallback_false_no_fallback_invocations(self):
        """extraction_fallback: false on the flow disables fallback; key absent from state."""
        recorder = _make_recorder()
        flow = _single_task_flow(extraction_fallback=False)
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        responses = [
            ProviderResult(exit_code=0, stdout="missed", stderr=""),
        ]
        with patch("fdsx.providers.claude._run_subprocess", side_effect=responses):
            compiled = compile_flow(flow, config=config, recorder=recorder)
            with pytest.raises(RuntimeError):
                compiled.graph.invoke({})

        state = recorder._find_state_by_name("classify")
        assert "fallback_invocations" not in state, (
            f"Expected no fallback_invocations when disabled, got: {state.get('fallback_invocations')}"
        )


class TestAllStrategiesHitAudit:
    def test_strategies_succeed_no_fallback_invocations_key(self, capsys):
        """When extraction succeeds on first try, fallback_invocations absent and no ↩ line in stderr."""
        recorder = _make_recorder()
        flow = _single_task_flow()
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="codex"))

        main_response = ProviderResult(
            exit_code=0, stdout="The decision is APPROVED", stderr=""
        )
        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=main_response),
            patch("fdsx.providers.codex._run_subprocess") as mock_codex,
        ):
            compiled = compile_flow(flow, config=config, recorder=recorder)
            result = compiled.graph.invoke({})

        mock_codex.assert_not_called()
        assert result.get("decision") == "APPROVED"

        state = recorder._find_state_by_name("classify")
        assert "fallback_invocations" not in state

        captured = capsys.readouterr()
        assert "↩ fallback" not in captured.err


class TestSystemProviderMissAudit:
    def test_system_provider_miss_no_record_and_no_stderr(self, capsys):
        """System-provider state whose strategies miss: no fallback_invocations, no ↩ line."""
        recorder = _make_recorder()
        system_task = TaskState(
            type="task",
            provider="system",
            command="echo 'unrelated output'",
            result_path="$.task_result",
            extract=_keyword_rule(),
            end=True,
        )
        flow = Flow(
            name="system_miss_test",
            description="system provider guard test",
            start_at="classify",
            states={"classify": system_task},
        )
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        with patch("fdsx.providers.claude._run_subprocess") as mock_claude:
            compiled = compile_flow(flow, config=config, recorder=recorder)
            with pytest.raises(RuntimeError):
                compiled.graph.invoke({})

        mock_claude.assert_not_called()
        state = recorder._find_state_by_name("classify")
        assert "fallback_invocations" not in state

        captured = capsys.readouterr()
        assert "↩ fallback" not in captured.err


class TestMapStateFallbackAudit:
    def test_map_iteration_fallback_record_has_iter_index(self):
        """Map state where one iteration triggers fallback: record has iter_index set."""
        recorder = _make_recorder()
        iterator = IteratorDef(
            states=[
                IteratorTaskState(
                    name="classify_item",
                    provider="claude",
                    model=_MODEL,
                    prompt_template="classify item: {$.item}",
                    result_path="$.item_decision",
                    extract=_keyword_rule(),
                    retry=0,
                )
            ]
        )
        map_state = MapState(
            type="map",
            items_path="$.items",
            iterator=iterator,
            result_path="$.map_results",
            end=True,
        )
        flow = Flow(
            name="map_fallback_test",
            description="map fallback audit",
            start_at="map_classify",
            states={"map_classify": map_state},
        )
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="claude"))

        # item 0: main task matches → no fallback
        # item 1: main task misses → fallback fires → REJECTED recovered
        responses = [
            ProviderResult(exit_code=0, stdout="APPROVED", stderr=""),
            ProviderResult(exit_code=0, stdout="no keyword here", stderr=""),
            ProviderResult(exit_code=0, stdout="REJECTED", stderr=""),
        ]

        with patch("fdsx.providers.claude._run_subprocess", side_effect=responses):
            compiled = compile_flow(flow, config=config, recorder=recorder)
            compiled.graph.invoke({"items": ["item_a", "item_b"]})

        state = recorder._find_state_by_name("map_classify")
        assert "fallback_invocations" in state, (
            f"Expected fallback_invocations for map state, got: {list(state.keys())}"
        )
        records = state["fallback_invocations"]
        assert len(records) == 1
        assert "iter_index" in records[0], (
            f"Expected iter_index in record, got: {records[0]}"
        )
        assert records[0]["iter_index"] == 1


class TestParallelBranchFallbackAudit:
    def test_parallel_two_branches_fallback_records_have_distinct_branch_index(self):
        """Parallel state where two branches trigger fallbacks: two records with distinct branch_index."""
        recorder = _make_recorder()
        parallel_state = ParallelState(
            type="parallel",
            branches=[
                Branch(
                    provider="claude",
                    model=_MODEL,
                    prompt_template="classify branch A",
                    extract=ExtractRule(
                        strategy=["keyword"],
                        pattern="APPROVED|REJECTED",
                        result_path="$.branch_a",
                    ),
                    retry=0,
                ),
                Branch(
                    provider="claude",
                    model=_MODEL,
                    prompt_template="classify branch B",
                    extract=ExtractRule(
                        strategy=["keyword"],
                        pattern="APPROVED|REJECTED",
                        result_path="$.branch_b",
                    ),
                    retry=0,
                ),
            ],
            result_path="$.parallel_results",
            min_success=0,
            end=True,
        )
        flow = Flow(
            name="parallel_fallback_test",
            description="parallel fallback audit",
            start_at="parallel_classify",
            states={"parallel_classify": parallel_state},
        )
        config = FdsxConfig(extraction_fallback=ExtractionFallback(provider="codex"))

        missed = ProviderResult(exit_code=0, stdout="no keyword here", stderr="")
        recovered = ProviderResult(exit_code=0, stdout="APPROVED", stderr="")

        with (
            patch("fdsx.providers.claude._run_subprocess", return_value=missed),
            patch("fdsx.providers.codex._run_subprocess", return_value=recovered),
        ):
            compiled = compile_flow(flow, config=config, recorder=recorder)
            compiled.graph.invoke({})

        state = recorder._find_state_by_name("parallel_classify")
        assert "fallback_invocations" in state, (
            f"Expected fallback_invocations in parallel state, got: {list(state.keys())}"
        )
        records = state["fallback_invocations"]
        assert len(records) == 2

        branch_indices = {r.get("branch_index") for r in records}
        assert len(branch_indices) == 2, (
            f"Expected 2 distinct branch_index values, got: {branch_indices}"
        )
        assert None not in branch_indices, (
            f"branch_index must be set on all records, got: {records}"
        )

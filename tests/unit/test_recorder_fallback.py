"""Unit tests for RunRecorder.record_fallback_invocation (T005 red phase)."""

import threading

from fdsx.logging.recorder import RunRecorder


def _make_recorder() -> RunRecorder:
    r = RunRecorder(thread_id="test-thread", flow_name="test-flow")
    r.record_state_start("step1", "task")
    return r


class TestRecordFallbackInvocation:
    def test_zero_invocations_no_key_in_state(self):
        r = _make_recorder()
        state = r._find_state_by_name("step1")
        assert state is not None
        assert "fallback_invocations" not in state

    def test_one_invocation_creates_list_with_one_entry(self):
        r = _make_recorder()
        r.record_fallback_invocation(
            state_name="step1",
            source="global",
            outcome="recovered",
            pattern="APPROVED|REJECTED",
            value_preview="APPROVED",
        )
        state = r._find_state_by_name("step1")
        assert "fallback_invocations" in state
        assert len(state["fallback_invocations"]) == 1
        entry = state["fallback_invocations"][0]
        assert entry["source"] == "global"
        assert entry["outcome"] == "recovered"
        assert entry["state_name"] == "step1"
        assert entry["value_preview"] == "APPROVED"

    def test_value_preview_truncated_to_200_chars(self):
        r = _make_recorder()
        long_value = "x" * 300
        r.record_fallback_invocation(
            state_name="step1",
            source="global",
            outcome="recovered",
            pattern=".*",
            value_preview=long_value,
        )
        state = r._find_state_by_name("step1")
        entry = state["fallback_invocations"][0]
        assert len(entry["value_preview"]) == 200

    def test_no_branch_or_iter_index_when_not_supplied(self):
        r = _make_recorder()
        r.record_fallback_invocation(
            state_name="step1",
            source="global",
            outcome="recovered",
            pattern=".*",
        )
        state = r._find_state_by_name("step1")
        entry = state["fallback_invocations"][0]
        assert "branch_index" not in entry
        assert "iter_index" not in entry

    def test_no_error_kind_on_recovered_outcome_without_error(self):
        r = _make_recorder()
        r.record_fallback_invocation(
            state_name="step1",
            source="global",
            outcome="recovered",
            pattern="APPROVED|REJECTED",
        )
        state = r._find_state_by_name("step1")
        entry = state["fallback_invocations"][0]
        assert "error_kind" not in entry

    def test_concurrent_calls_both_land_in_list(self):
        r = _make_recorder()
        errors: list[Exception] = []

        def call_invocation(source: str) -> None:
            try:
                r.record_fallback_invocation(
                    state_name="step1",
                    source=source,
                    outcome="recovered",
                    pattern=".*",
                )
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=call_invocation, args=(f"source-{i}",))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        state = r._find_state_by_name("step1")
        assert len(state["fallback_invocations"]) == 10

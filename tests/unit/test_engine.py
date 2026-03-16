from fdsx.core.engine import _extract_results


class TestExtractResults:
    """F4 regression: _extract_results must preserve nested result paths."""

    def test_single_level_path(self):
        """Single-level paths work the same as before."""
        state = {"result": "hello", "_meta": {"thread_id": "abc"}}
        result = _extract_results(state, ["$.result"])
        assert result == {"result": "hello"}

    def test_nested_path_preserved(self):
        """F4: nested result path must not be flattened to root key."""
        state = {"review": {"summary": "good", "decision": "approve"}}
        result = _extract_results(state, ["$.review.summary"])
        assert result == {"review": {"summary": "good"}}

    def test_multiple_nested_paths_same_root(self):
        """F4: two sub-paths under same root must not overwrite each other."""
        state = {"review": {"summary": "good", "decision": "approve"}}
        result = _extract_results(state, ["$.review.summary", "$.review.decision"])
        assert result["review"]["summary"] == "good"
        assert result["review"]["decision"] == "approve"

    def test_none_value_skipped(self):
        """Missing paths in state produce no entry in results."""
        state = {}
        result = _extract_results(state, ["$.missing"])
        assert result == {}

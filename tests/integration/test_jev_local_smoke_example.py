"""Keep the copy-paste live experiment executable without contacting Jev."""

import json
from collections import Counter
from pathlib import Path

import httpx2
import pytest

from fdsx.core.engine import run_flow
from fdsx.core.engine.errors import FlowExecutionError

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/jev-local-smoke/workflow.yaml"


@pytest.mark.parametrize("approve_wrong", [False, True])
def test_live_example_exercises_repair_or_rejects_false_success(
    tmp_path, monkeypatch, approve_wrong
):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-key")
    materials = []

    def request(client, method, url, **kwargs):
        body = json.loads(kwargs["content"])
        equation = json.loads(body["state"])["equation"]
        materials.append(equation)
        action = "approved" if approve_wrong or equation == "2 + 2 = 4" else "fix"
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "usage": {},
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": action,
                        "confidence": 1.0,
                        "probabilities": {
                            "approved": float(action == "approved"),
                            "fix": float(action == "fix"),
                        },
                    }
                },
            },
            request=httpx2.Request(method, url),
        )

    monkeypatch.setattr(httpx2.Client, "request", request)
    if approve_wrong:
        # A collected local failure must not turn into a successful experiment.
        try:
            result = run_flow(EXAMPLE, base_dir=tmp_path / ".fdsx")
        except FlowExecutionError:
            pass
        else:
            assert result.status != "completed"
        assert materials == ["2 + 2 = 5", "2 + 2 = 5"]
        return

    result = run_flow(EXAMPLE, base_dir=tmp_path / ".fdsx")
    assert result.status == "completed"
    assert Counter(materials) == {"2 + 2 = 5": 4, "2 + 2 = 4": 4}
    for key in ("map_results", "parallel_results"):
        assert len(result.results[key]) == 2
        for item in result.results[key]:
            assert item["exit_code"] == 0
            assert item["output"] == {
                "equation": "2 + 2 = 4",
                "action": "approved",
            }

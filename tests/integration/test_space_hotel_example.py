"""Exercise the example's three classifier-driven routes without external calls."""

import json
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest

from fdsx.core.engine import run_flow
from fdsx.providers.base import ProviderResult

EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "src/fdsx/examples/workflows/evaluation-task/space-hotel.yaml"
)


@pytest.mark.parametrize(
    ("department", "name"),
    [
        ("emergency", "緊急対応チーム"),
        ("maintenance", "設備サポート"),
        ("billing", "会計担当"),
    ],
)
def test_jev_classification_selects_reply_policy(
    tmp_path, monkeypatch, department, name
):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-key")
    requests = []
    payload = {
        "model": "jev-1.13.0",
        "usage": {},
        "answers": {
            "department": {
                "type": "choice",
                "choice": department,
                "confidence": 0.9,
                "probabilities": {
                    candidate: 0.95 if candidate == department else 0.025
                    for candidate in ("emergency", "maintenance", "billing")
                },
            }
        },
    }

    def request(client, method, url, **kwargs):
        requests.append(json.loads(kwargs["content"]))
        return httpx2.Response(200, json=payload, request=httpx2.Request(method, url))

    monkeypatch.setattr(httpx2.Client, "request", request)
    # The same starting selection intentionally yields three different mocked
    # classifications: routing must follow Jev, not the user's scenario label.
    with (
        patch(
            "fdsx.core.engine.interrupts.display_wait_prompt",
            side_effect=["返金相談", "対応を見る", "終了"],
        ) as prompt,
        patch(
            "fdsx.providers.claude._run_subprocess",
            side_effect=[
                ProviderResult(0, "宇宙ホテルからの架空の苦情です。", ""),
                ProviderResult(0, "担当者からの返信案です。", ""),
            ],
        ) as llm,
    ):
        result = run_flow(EXAMPLE, base_dir=tmp_path / ".fdsx")

    assert result.status == "completed"
    assert result.results["classification"] == {
        "department": department,
        "confidence": 0.9,
    }
    assert result.results["department_name"] == name
    assert llm.call_count == 2
    assert len(requests) == 1
    material = json.loads(requests[0]["state"])
    assert "宇宙ホテルからの架空の苦情です。" in material["prompt"]
    reply_args = llm.call_args_list[1].kwargs
    assert name in str(reply_args)
    assert result.results["response_policy"] in str(reply_args)
    assert prompt.call_count == 3
    assert department in prompt.call_args_list[1].args[1]
    assert "担当者からの返信案です。" in prompt.call_args_list[2].args[1]

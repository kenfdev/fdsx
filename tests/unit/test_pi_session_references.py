"""Native JSONL parsing and completion-prefix integrity."""

import json
import sys

import pytest

from fdsx.providers.base import ProviderSessionError
from fdsx.providers.pi_sessions import capture_reference, select_source


def entries():
    return [
        {
            "type": "session",
            "version": 3,
            "id": "11111111-1111-4111-8111-111111111111",
            "timestamp": "2026-09-16T00:00:00Z",
            "cwd": "/fixture",
        },
        {
            "type": "message",
            "id": "00000001",
            "parentId": None,
            "message": {"role": "user", "content": "plan", "timestamp": 0},
        },
        {
            "type": "message",
            "id": "00000002",
            "parentId": "00000001",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "fixture"}],
                "stopReason": "stop",
                "api": "fixture",
                "provider": "fixture",
                "model": "fixture",
                "timestamp": 1,
            },
        },
    ]


def save(tmp_path, data):
    path = tmp_path / "native.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in data) + "\n")
    return path


def test_trailing_label_selects_conversation_endpoint(tmp_path):
    data = entries()
    data.append(
        {
            "type": "label",
            "id": "00000003",
            "parentId": "00000002",
            "targetId": "00000002",
            "label": "complete",
        }
    )
    path = save(tmp_path, data)
    reference = capture_reference(path, "plan")
    assert reference["endpoint"] == "00000002"
    assert select_source(reference, "review") == path


@pytest.mark.parametrize(
    "damage",
    [
        "parent",
        "duplicate",
        "version",
        "header",
        "scalar",
        "no_assistant",
        "invalid_utf8",
        "invalid_json",
        "deep_json",
    ],
)
def test_corruption_is_a_sanitized_domain_error(tmp_path, damage):
    data = entries()
    if damage == "parent":
        data[2]["parentId"] = "missing"
    elif damage == "duplicate":
        data[2]["id"] = data[1]["id"]
    elif damage == "version":
        data[0]["version"] = 999
    elif damage == "header":
        data.pop(0)
    elif damage == "scalar":
        data.append("PRIVATE")
    elif damage == "no_assistant":
        data.pop()
    path = save(tmp_path, data)
    if damage == "invalid_utf8":
        path.write_bytes(b"\xffPRIVATE")
    elif damage == "invalid_json":
        path.write_text("PRIVATE{")
    elif damage == "deep_json":
        depth = sys.getrecursionlimit() + 100
        path.write_text("[" * depth + '"PRIVATE"' + "]" * depth)
    with pytest.raises(ProviderSessionError, match="State 'review'") as error:
        capture_reference(path, "review")
    assert "PRIVATE" not in str(error.value)
    assert "restore the original Pi history or rerun its source" in str(error.value)


def test_corrupt_append_is_not_silently_repaired(tmp_path):
    path = save(tmp_path, entries())
    reference = capture_reference(path, "plan")
    with path.open("a") as file:
        file.write('{"broken"')
    with pytest.raises(ProviderSessionError, match="corrupt"):
        select_source(reference, "review")


@pytest.mark.parametrize(
    "change",
    [
        {"size": "no"},
        {"provider": "claude"},
        {"path": "relative"},
        {"id": []},
        {"extra": "value"},
        {"size": "0"},
    ],
)
def test_invalid_metadata_never_selects_a_source(tmp_path, change):
    path = save(tmp_path, entries())
    reference = {**capture_reference(path, "plan"), **change}
    with pytest.raises(ProviderSessionError):
        select_source(reference, "review")

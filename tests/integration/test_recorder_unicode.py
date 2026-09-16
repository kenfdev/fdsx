"""Run logs preserve readable Japanese text on disk."""

import json

from fdsx.logging.recorder import RunRecorder


def test_save_preserves_japanese_text(tmp_path):
    recorder = RunRecorder(thread_id="unicode-test", flow_name="日本語のフロー")
    recorder.record_state_start("計画", "task")
    recorder.record_state_complete("計画", "success", "日本語の出力", ["$.plan"])
    recorder.finalize({"plan": "日本語の計画"}, "completed")

    file_path = recorder.save(base_dir=tmp_path)
    text = file_path.read_text(encoding="utf-8")

    assert "日本語のフロー" in text
    assert "日本語の出力" in text
    assert "日本語の計画" in text
    assert "\\u" not in text
    assert json.loads(text) == recorder.to_dict()

import base64
import json
import os
import time

from app.maintenance import codex_rollout_compact as compact

PIXEL = "data:image/png;base64," + base64.b64encode(os.urandom(200_000)).decode()


def rollout(directory, name="rollout-2026-10-04T00-00-00-abc.jsonl", old=True):
    path = directory / "sessions" / "2026" / "10" / "04" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"type": "session_meta", "payload": {"id": "abc"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "keep KIG-7"}, {"type": "input_image", "image_url": PIXEL}]}},
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "output": [{"type": "input_image", "image_url": PIXEL}]}},
        {"type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "imageGeneration", "id": "i", "status": "completed",
                                                                            "result": "A" * 100_000, "savedPath": "/x.png"}}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Pink"}]}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    if old:
        past = time.time() - 3 * 86400
        os.utime(path, (past, past))
    return path


def test_images_are_dropped_and_the_conversation_is_kept(tmp_path):
    path = rollout(tmp_path)
    before = path.stat().st_size
    report = compact.run(tmp_path, 24, apply=True)
    assert report["compacted"] == 1 and report["bytes_after"] < before / 20
    text = path.read_text(encoding="utf-8")
    assert "data:image" not in text and "A" * 1000 not in text
    records = [json.loads(line) for line in text.splitlines()]
    assert len(records) == 5
    assert records[1]["payload"]["content"][0]["text"] == "keep KIG-7"
    assert records[1]["payload"]["content"][1] == {"type": "input_text", "text": compact.NOTE}
    assert records[2]["payload"]["output"] == [{"type": "input_text", "text": compact.NOTE}]
    assert records[3]["payload"]["item"]["result"] == "" and records[3]["payload"]["item"]["savedPath"] == "/x.png"
    assert records[4]["payload"]["content"][0]["text"] == "Pink"


def test_second_run_changes_nothing(tmp_path):
    path = rollout(tmp_path)
    compact.run(tmp_path, 24, apply=True)
    snapshot = path.read_bytes()
    assert compact.run(tmp_path, 24, apply=True)["compacted"] == 0
    assert path.read_bytes() == snapshot


def test_recent_sessions_and_dry_runs_are_left_alone(tmp_path):
    path = rollout(tmp_path, old=False)
    before = path.read_bytes()
    assert compact.run(tmp_path, 24, apply=True)["skipped_recent"] == 1
    old = rollout(tmp_path, name="rollout-2026-10-03T00-00-00-def.jsonl")
    report = compact.run(tmp_path, 24, apply=False)
    assert report["candidates"] == 1 and report["compacted"] == 0
    assert path.read_bytes() == before and "data:image" in old.read_text(encoding="utf-8")


def test_history_index_is_reset_only_after_a_rewrite(tmp_path):
    index = tmp_path / "thread_history_1.sqlite"
    index.write_bytes(b"x")
    compact.run(tmp_path, 24, apply=True)
    assert index.exists()
    rollout(tmp_path)
    assert compact.run(tmp_path, 24, apply=True)["history_index_reset"] is True
    assert not index.exists()


def test_refuses_while_the_history_index_is_open(tmp_path, monkeypatch):
    path = rollout(tmp_path)
    index = tmp_path / "thread_history_1.sqlite"
    index.write_bytes(b"x")
    info = index.stat()
    monkeypatch.setattr(compact, "_open_files", lambda: {(info.st_dev, info.st_ino)})
    assert compact.run(tmp_path, 24, apply=True) == {"refused": "history_index_in_use"}
    assert "data:image" in path.read_text(encoding="utf-8") and index.exists()

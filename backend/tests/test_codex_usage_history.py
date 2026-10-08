import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.generation import codex_usage_history as history
from app.generation.codex_app_server import CodexRateLimitWindow, CodexUsageSnapshot


def snapshot(at, remaining=80.0, secondary=None, available=True):
    return CodexUsageSnapshot(
        checked_at=at, available=available, plan_type="prolite", source="app_server",
        primary=CodexRateLimitWindow(used_percent=100 - remaining, remaining_percent=remaining, window_duration_mins=10080),
        secondary=None if secondary is None else CodexRateLimitWindow(
            used_percent=100 - secondary, remaining_percent=secondary, window_duration_mins=300),
    )


def settings(tmp_path):
    return SimpleNamespace(v2_runtime_dir=str(tmp_path))


def test_readings_are_recorded_and_read_back(tmp_path):
    now = datetime.now(UTC)
    config = settings(tmp_path)
    history.record(config, snapshot(now - timedelta(minutes=10), 90, secondary=70))
    history.record(config, snapshot(now, 85))
    points = history.read(config, 24)
    assert [item["p"] for item in points] == [90, 85]
    assert points[0]["s"] == 70 and points[0]["sw"] == 300 and points[0]["pw"] == 10080 and "s" not in points[1]


def test_unavailable_readings_and_rapid_repeats_are_not_stored(tmp_path):
    now = datetime.now(UTC)
    config = settings(tmp_path)
    history.record(config, snapshot(now, available=False))
    history.record(config, snapshot(now, 80))
    history.record(config, snapshot(now + timedelta(seconds=20), 79))
    assert len(history.read(config, 24)) == 1


def test_window_and_thinning(tmp_path):
    now = datetime.now(UTC)
    config = settings(tmp_path)
    path = tmp_path / history.FILE_NAME
    rows = [{"at": (now - timedelta(minutes=index)).isoformat(timespec="seconds"), "p": 50, "plan": "x"} for index in range(2000, 0, -1)]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\nnot json\n", encoding="utf-8")
    assert len(history.read(config, 1)) == 59 or len(history.read(config, 1)) == 60
    thinned = history.read(config, 24 * 30, max_points=100)
    assert len(thinned) == 101 and thinned[-1] == rows[-1]


def test_missing_file_gives_an_empty_chart(tmp_path):
    assert history.read(settings(tmp_path / "nowhere"), 24) == []

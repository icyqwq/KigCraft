from app.generation.codex_app_server import CodexRateLimitWindow, CodexUsageSnapshot
from app.generation.message_codes import codex_usage_limit_from_snapshot


def test_codex_usage_limit_from_snapshot_uses_reset_at(monkeypatch):
    from datetime import UTC, datetime

    from app.generation import codex_usage

    snapshot = CodexUsageSnapshot(
        checked_at=datetime.now(UTC),
        available=True,
        primary=CodexRateLimitWindow(
            used_percent=100,
            remaining_percent=0,
            reset_at=datetime(2026, 7, 8, 14, 30, tzinfo=UTC),
            reset_after_seconds=1800,
            window_duration_mins=300,
        ),
        source="test",
    )

    monkeypatch.setattr(codex_usage, "get_cached_codex_usage_snapshot", lambda _settings=None: snapshot)

    message = codex_usage_limit_from_snapshot()

    assert message.startswith("codex_usage_limit:retry_at=")
    assert "2026-07-08 22:30" in message

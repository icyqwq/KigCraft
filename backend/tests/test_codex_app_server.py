from datetime import UTC, datetime

from app.generation.codex_app_server import parse_codex_rate_limits_payload


def test_parse_codex_rate_limits_payload_reads_primary_and_secondary():
    payload = {
        "planType": "plus",
        "primary": {
            "usedPercent": 25,
            "windowDurationMins": 300,
            "resetsAt": 1_773_320_436,
        },
        "secondary": {
            "usedPercent": 7,
            "windowDurationMins": 10080,
            "resetsAt": 1_773_879_554,
        },
    }

    snapshot = parse_codex_rate_limits_payload(payload)

    assert snapshot is not None
    assert snapshot.available is True
    assert snapshot.plan_type == "plus"
    assert snapshot.primary is not None
    assert snapshot.primary.used_percent == 25
    assert snapshot.primary.remaining_percent == 75
    assert snapshot.primary.window_duration_mins == 300
    assert snapshot.primary.reset_at == datetime.fromtimestamp(1_773_320_436, tz=UTC)
    assert snapshot.secondary is not None
    assert snapshot.secondary.used_percent == 7
    assert snapshot.secondary.remaining_percent == 93


def test_parse_codex_rate_limits_payload_reads_nested_rate_limits():
    payload = {
        "rateLimits": {
            "primary": {"usedPercent": 49, "resetsAt": 1_780_350_290},
            "secondary": {"usedPercent": 23, "resetsAt": 1_780_845_815},
        }
    }

    snapshot = parse_codex_rate_limits_payload(payload)

    assert snapshot is not None
    assert snapshot.primary is not None
    assert snapshot.primary.remaining_percent == 51
    assert snapshot.secondary is not None
    assert snapshot.secondary.remaining_percent == 77


def test_parse_codex_rate_limits_payload_supports_legacy_flat_payload():
    payload = {
        "remaining_percent": 12,
        "reset_after_seconds": 3600,
        "reset_at": "2026-07-08T14:30:00+00:00",
    }

    snapshot = parse_codex_rate_limits_payload(payload)

    assert snapshot is not None
    assert snapshot.primary is not None
    assert snapshot.primary.remaining_percent == 12
    assert snapshot.primary.reset_after_seconds == 3600

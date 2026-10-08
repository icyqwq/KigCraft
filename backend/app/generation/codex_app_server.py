import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("uvicorn.error")

HANDSHAKE_DELAY_SECONDS = 0.5


@dataclass(frozen=True)
class CodexRateLimitWindow:
    used_percent: float
    remaining_percent: float
    reset_at: datetime | None = None
    reset_after_seconds: int | None = None
    window_duration_mins: int | None = None

    @property
    def wait_seconds(self) -> int | None:
        if self.reset_after_seconds is not None:
            return max(0, self.reset_after_seconds)
        if self.reset_at is None:
            return None
        return max(0, int((self.reset_at - datetime.now(UTC)).total_seconds()))


@dataclass(frozen=True)
class CodexUsageSnapshot:
    checked_at: datetime
    available: bool
    plan_type: str | None = None
    primary: CodexRateLimitWindow | None = None
    secondary: CodexRateLimitWindow | None = None
    source: str = "unavailable"
    error: str | None = None

    @property
    def five_hour(self) -> CodexRateLimitWindow | None:
        return self.primary

    @property
    def weekly(self) -> CodexRateLimitWindow | None:
        return self.secondary


def parse_codex_rate_limits_payload(payload: Any) -> CodexUsageSnapshot | None:
    if not isinstance(payload, dict):
        return None

    rate_limits = payload.get("rateLimits")
    if isinstance(rate_limits, dict):
        payload = {**payload, **rate_limits}

    primary = _parse_rate_limit_window(
        _first_dict(payload, "primary", "five_hour", "fiveHour", "burst"),
        default_window_mins=300,
    )
    secondary = _parse_rate_limit_window(
        _first_dict(payload, "secondary", "weekly", "seven_day", "sevenDay"),
        default_window_mins=10080,
    )
    if primary is None and secondary is None:
        legacy = _parse_legacy_flat_payload(payload)
        if legacy is None:
            return None
        return legacy

    plan_type = _string_from_keys(payload, "planType", "plan_type", "plan")
    return CodexUsageSnapshot(
        checked_at=datetime.now(UTC),
        available=True,
        plan_type=plan_type,
        primary=primary,
        secondary=secondary,
        source="parsed",
    )


def snapshot_to_dict(snapshot: CodexUsageSnapshot) -> dict[str, Any]:
    return {
        "available": snapshot.available,
        "checked_at": snapshot.checked_at.isoformat(),
        "plan_type": snapshot.plan_type,
        "source": snapshot.source,
        "error": snapshot.error,
        "five_hour": _window_to_dict(snapshot.primary),
        "weekly": _window_to_dict(snapshot.secondary),
    }


async def fetch_codex_rate_limits(
    codex_path: str,
    *,
    timeout_seconds: float = 12,
    env: dict[str, str] | None = None,
) -> CodexUsageSnapshot:
    try:
        payload = await asyncio.to_thread(
            _fetch_codex_rate_limits_sync,
            codex_path,
            timeout_seconds=timeout_seconds,
            env=env,
        )
    except Exception as exc:
        logger.warning("Codex app-server rate limit fetch failed: %s", exc)
        return CodexUsageSnapshot(
            checked_at=datetime.now(UTC),
            available=False,
            source="app_server",
            error=str(exc),
        )

    if payload is None:
        return CodexUsageSnapshot(
            checked_at=datetime.now(UTC),
            available=False,
            source="app_server",
            error="Codex app-server returned no rate limit data",
        )

    parsed = parse_codex_rate_limits_payload(payload)
    if parsed is None:
        return CodexUsageSnapshot(
            checked_at=datetime.now(UTC),
            available=False,
            source="app_server",
            error="Codex app-server response could not be parsed",
        )
    return CodexUsageSnapshot(
        checked_at=parsed.checked_at,
        available=True,
        plan_type=parsed.plan_type,
        primary=parsed.primary,
        secondary=parsed.secondary,
        source="app_server",
    )


def _fetch_codex_rate_limits_sync(
    codex_path: str,
    *,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    import subprocess

    process = subprocess.Popen(
        [codex_path, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="ignore",
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    deadline = time.monotonic() + timeout_seconds
    try:
        _write_json_rpc(process.stdin, {"method": "initialize", "id": 0, "params": _initialize_params()})
        _read_json_rpc_result(process.stdout, request_id=0, deadline=deadline)
        remaining = max(0.0, deadline - time.monotonic())
        time.sleep(min(HANDSHAKE_DELAY_SECONDS, remaining))
        _write_json_rpc(
            process.stdin,
            {"method": "account/rateLimits/read", "id": 1, "params": {}},
        )
        return _read_json_rpc_result(process.stdout, request_id=1, deadline=deadline)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def _initialize_params() -> dict[str, Any]:
    return {
        "clientInfo": {
            "name": "kig-preview",
            "title": "Kig Preview",
            "version": "1.0.0",
        }
    }


def _write_json_rpc(stream: Any, message: dict[str, Any]) -> None:
    stream.write(json.dumps(message, ensure_ascii=False) + "\n")
    stream.flush()


def _read_json_rpc_result(stream: Any, *, request_id: int, deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        line = stream.readline()
        if not line:
            break
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if "error" in message:
            error = message["error"]
            if isinstance(error, dict):
                raise RuntimeError(str(error.get("message") or error))
            raise RuntimeError(str(error))
        result = message.get("result")
        return result if isinstance(result, dict) else None
    return None


def _parse_legacy_flat_payload(payload: dict[str, Any]) -> CodexUsageSnapshot | None:
    remaining_percent = _float_from_keys(
        payload,
        "remaining_percent",
        "remainingPercentage",
        "five_hour_remaining_percent",
        "fiveHourRemainingPercent",
    )
    if remaining_percent is None:
        remaining = _float_from_keys(payload, "remaining", "five_hour_remaining")
        limit = _float_from_keys(payload, "limit", "five_hour_limit")
        if remaining is not None and limit and limit > 0:
            remaining_percent = (remaining / limit) * 100
    if remaining_percent is None:
        return None

    reset_after_seconds = _int_from_keys(payload, "reset_after_seconds", "resetAfterSeconds")
    reset_at = _datetime_from_keys(payload, "reset_at", "resetAt", "resets_at", "resetsAt")
    primary = CodexRateLimitWindow(
        used_percent=max(0.0, min(100.0, 100.0 - remaining_percent)),
        remaining_percent=max(0.0, min(100.0, remaining_percent)),
        reset_at=reset_at,
        reset_after_seconds=reset_after_seconds,
        window_duration_mins=300,
    )
    return CodexUsageSnapshot(
        checked_at=datetime.now(UTC),
        available=True,
        primary=primary,
        source="legacy",
    )


def _parse_rate_limit_window(
    payload: dict[str, Any] | None,
    *,
    default_window_mins: int,
) -> CodexRateLimitWindow | None:
    if not isinstance(payload, dict):
        return None

    used_percent = _float_from_keys(payload, "usedPercent", "used_percent", "utilization")
    remaining_percent = _float_from_keys(
        payload,
        "remainingPercent",
        "remaining_percent",
        "percent_remaining",
        "percentRemaining",
    )
    if used_percent is None and remaining_percent is not None:
        used_percent = max(0.0, min(100.0, 100.0 - remaining_percent))
    if remaining_percent is None and used_percent is not None:
        remaining_percent = max(0.0, min(100.0, 100.0 - used_percent))
    if used_percent is None or remaining_percent is None:
        return None

    reset_at = _datetime_from_keys(payload, "resetsAt", "reset_at", "resetAt", "resets_at")
    reset_after_seconds = _int_from_keys(
        payload,
        "reset_after_seconds",
        "resetAfterSeconds",
        "resets_in_seconds",
        "resetsInSeconds",
    )
    if reset_after_seconds is None and reset_at is not None:
        reset_after_seconds = max(0, int((reset_at - datetime.now(UTC)).total_seconds()))

    window_duration_mins = _int_from_keys(
        payload,
        "windowDurationMins",
        "window_duration_mins",
        "window_duration_minutes",
    )
    return CodexRateLimitWindow(
        used_percent=max(0.0, min(100.0, used_percent)),
        remaining_percent=max(0.0, min(100.0, remaining_percent)),
        reset_at=reset_at,
        reset_after_seconds=reset_after_seconds,
        window_duration_mins=window_duration_mins or default_window_mins,
    )


def _window_to_dict(window: CodexRateLimitWindow | None) -> dict[str, Any] | None:
    if window is None:
        return None
    return {
        "used_percent": round(window.used_percent, 2),
        "remaining_percent": round(window.remaining_percent, 2),
        "reset_at": window.reset_at.isoformat() if window.reset_at else None,
        "reset_after_seconds": window.reset_after_seconds,
        "window_duration_mins": window.window_duration_mins,
    }


def _first_dict(payload: dict[str, Any], *keys: str) -> dict[str, Any] | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return None


def _string_from_keys(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _float_from_keys(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip().rstrip("%"))
            except ValueError:
                continue
    return None


def _int_from_keys(payload: dict[str, Any], *keys: str) -> int | None:
    value = _float_from_keys(payload, *keys)
    return int(value) if value is not None and value >= 0 else None


def _datetime_from_keys(payload: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=UTC)
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    return None

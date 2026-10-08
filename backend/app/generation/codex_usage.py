import asyncio
import json
import logging
import os
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import HTTPException

from app.codex_profiles.service import active_codex_profile_env
from app.core.config import Settings
from app.generation.codex_app_server import (
    CodexRateLimitWindow,
    CodexUsageSnapshot,
    fetch_codex_rate_limits,
    parse_codex_rate_limits_payload,
    snapshot_to_dict,
)

logger = logging.getLogger("uvicorn.error")

CACHE_TTL_SECONDS = 30
_cached_snapshot: CodexUsageSnapshot | None = None
_cached_snapshot_at: datetime | None = None
_cached_snapshot_key: str | None = None


@dataclass(frozen=True)
class CodexUsageStatus:
    remaining_percent: float
    reset_at: datetime | None = None
    reset_after_seconds: int | None = None

    @property
    def wait_seconds(self) -> int | None:
        if self.reset_after_seconds is not None:
            return max(0, self.reset_after_seconds)
        if self.reset_at is None:
            return None
        return max(0, int((self.reset_at - datetime.now(UTC)).total_seconds()))


async def ensure_codex_usage_allows_generation(settings: Settings) -> None:
    if not settings.codex_usage_check_enabled:
        return

    snapshot = await get_codex_usage_snapshot(settings)
    status = _status_from_snapshot(snapshot)
    if status is None:
        logger.warning("Codex usage gate skipped because usage status is unavailable")
        return

    min_remaining = float(_get_min_remaining_percent(settings))
    if status.remaining_percent >= min_remaining:
        return

    wait_seconds = status.wait_seconds
    if wait_seconds is None:
        wait_seconds = max(1, int(settings.quota_window_hours * 3600))
    wait_text = format_wait_time(wait_seconds)
    detail = {
        "code": "codex_usage_low",
        "message": f"负载高，请等 {wait_text} 后再重试。",
        "remaining_percent": round(status.remaining_percent, 2),
        "reset_wait_seconds": wait_seconds,
        "reset_at": status.reset_at.isoformat() if status.reset_at else None,
    }
    logger.warning(
        "Codex usage gate blocked generation remaining_percent=%.2f min_remaining=%.2f wait_seconds=%s",
        status.remaining_percent,
        min_remaining,
        wait_seconds,
    )
    raise HTTPException(status_code=429, detail=detail)


def format_codex_usage_limit_message(settings: Settings | None = None) -> str:
    from app.generation.message_codes import codex_usage_limit_from_snapshot

    return codex_usage_limit_from_snapshot()


def get_cached_codex_usage_snapshot(settings: Settings | None = None) -> CodexUsageSnapshot | None:
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()

    cache_key = _usage_cache_key(settings)
    cached = _get_fresh_cached_snapshot(cache_key)
    if cached is not None:
        return cached

    resolved_path = _resolve_codex_path(settings)
    if not resolved_path:
        return None

    from app.generation.codex_app_server import _fetch_codex_rate_limits_sync

    try:
        payload = _fetch_codex_rate_limits_sync(
            resolved_path,
            timeout_seconds=float(settings.codex_usage_check_timeout_seconds),
            env=_codex_usage_env(settings),
        )
    except Exception as exc:
        logger.warning("Codex usage sync fetch failed: %s", exc)
        return None

    if payload is None:
        return None

    parsed = parse_codex_rate_limits_payload(payload)
    if parsed is None:
        return None

    snapshot = CodexUsageSnapshot(
        checked_at=datetime.now(UTC),
        available=True,
        plan_type=parsed.plan_type,
        primary=parsed.primary,
        secondary=parsed.secondary,
        source="app_server",
    )
    _store_cached_snapshot(snapshot, cache_key)
    return snapshot


async def get_codex_usage_snapshot(settings: Settings) -> CodexUsageSnapshot:
    cache_key = _usage_cache_key(settings)
    cached = _get_fresh_cached_snapshot(cache_key)
    if cached is not None:
        return cached

    snapshot = await _fetch_codex_usage_snapshot(settings)
    _store_cached_snapshot(snapshot, cache_key)
    from app.generation.codex_usage_history import record

    record(settings, snapshot)
    return snapshot


async def get_codex_usage_status(settings: Settings) -> CodexUsageStatus | None:
    return _status_from_snapshot(await get_codex_usage_snapshot(settings))


async def _fetch_codex_usage_snapshot(settings: Settings) -> CodexUsageSnapshot:
    codex_path = _resolve_codex_path(settings)
    if codex_path:
        snapshot = await fetch_codex_rate_limits(
            codex_path,
            timeout_seconds=float(settings.codex_usage_check_timeout_seconds),
            env=_codex_usage_env(settings),
        )
        if snapshot.available:
            return snapshot

    bridge_snapshot = await _get_bridge_usage_snapshot(settings)
    if bridge_snapshot is not None:
        return bridge_snapshot

    command_snapshot = await _get_command_usage_snapshot(settings)
    if command_snapshot is not None:
        return command_snapshot

    if codex_path:
        return await fetch_codex_rate_limits(
            codex_path,
            timeout_seconds=float(settings.codex_usage_check_timeout_seconds),
            env=_codex_usage_env(settings),
        )

    return CodexUsageSnapshot(
        checked_at=datetime.now(UTC),
        available=False,
        source="unavailable",
        error="Codex usage status is unavailable",
    )


async def _get_bridge_usage_snapshot(settings: Settings) -> CodexUsageSnapshot | None:
    if not settings.codex_bridge_url or not settings.codex_bridge_token:
        return None
    url = f"{settings.codex_bridge_url.rstrip('/')}/usage"
    try:
        async with httpx.AsyncClient(timeout=settings.codex_usage_check_timeout_seconds) as client:
            response = await client.get(url, headers={"X-Codex-Bridge-Token": settings.codex_bridge_token})
        if response.status_code == 404:
            return None
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Codex bridge usage check failed: %s", exc)
        return None
    return snapshot_from_payload(response.json(), source="bridge")


async def _get_command_usage_snapshot(settings: Settings) -> CodexUsageSnapshot | None:
    if not settings.codex_usage_command.strip():
        return None
    command = shlex.split(settings.codex_usage_command)
    if not command:
        return None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_codex_usage_env(settings),
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=settings.codex_usage_check_timeout_seconds,
        )
    except Exception as exc:
        logger.warning("Codex usage command failed to run: %s", exc)
        return None
    if process.returncode != 0:
        logger.warning(
            "Codex usage command returned non-zero exit_code=%s stderr=%s",
            process.returncode,
            stderr.decode("utf-8", errors="ignore")[:500],
        )
        return None
    text = stdout.decode("utf-8", errors="ignore").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Codex usage command did not return JSON")
        return None
    return snapshot_from_payload(payload, source="command")


def snapshot_from_payload(payload: Any, *, source: str) -> CodexUsageSnapshot | None:
    parsed = parse_codex_rate_limits_payload(payload)
    if parsed is not None:
        return CodexUsageSnapshot(
            checked_at=datetime.now(UTC),
            available=True,
            plan_type=parsed.plan_type,
            primary=parsed.primary,
            secondary=parsed.secondary,
            source=source,
        )

    legacy = parse_codex_usage_payload(payload)
    if legacy is None:
        return None
    return CodexUsageSnapshot(
        checked_at=datetime.now(UTC),
        available=True,
        primary=CodexRateLimitWindow(
            used_percent=max(0.0, 100.0 - legacy.remaining_percent),
            remaining_percent=legacy.remaining_percent,
            reset_at=legacy.reset_at,
            reset_after_seconds=legacy.reset_after_seconds,
            window_duration_mins=300,
        ),
        source=source,
    )


def parse_codex_usage_payload(payload: Any) -> CodexUsageStatus | None:
    snapshot = parse_codex_rate_limits_payload(payload)
    if snapshot and snapshot.primary is not None:
        return _status_from_window(snapshot.primary)
    if not isinstance(payload, dict):
        return None

    remaining_percent = _float_from_keys(
        payload,
        "remaining_percent",
        "remainingPercentage",
        "percent_remaining",
        "percentRemaining",
        "five_hour_remaining_percent",
        "fiveHourRemainingPercent",
    )
    if remaining_percent is None:
        remaining = _float_from_keys(payload, "remaining", "remaining_quota", "five_hour_remaining")
        limit = _float_from_keys(payload, "limit", "quota", "five_hour_limit")
        if remaining is not None and limit and limit > 0:
            remaining_percent = (remaining / limit) * 100
    if remaining_percent is None:
        return None

    reset_after_seconds = _int_from_keys(
        payload,
        "reset_after_seconds",
        "resetAfterSeconds",
        "resets_in_seconds",
        "resetsInSeconds",
    )
    reset_at = _datetime_from_keys(payload, "reset_at", "resetAt", "resets_at", "resetsAt")
    return CodexUsageStatus(
        remaining_percent=max(0.0, min(100.0, remaining_percent)),
        reset_after_seconds=reset_after_seconds,
        reset_at=reset_at,
    )


def usage_payload_from_env(value: str | None) -> CodexUsageSnapshot | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return snapshot_from_payload(payload, source="env")


def format_wait_time(wait_seconds: int | None) -> str:
    if wait_seconds is None:
        return "一段时间"
    minutes = max(1, int((wait_seconds + 59) / 60))
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    rest_minutes = minutes % 60
    if rest_minutes == 0:
        return f"{hours} 小时"
    return f"{hours} 小时 {rest_minutes} 分钟"


SHANGHAI_TZ = timedelta(hours=8)


def format_reset_at(reset_at: datetime) -> str:
    local = (reset_at.astimezone(UTC) + SHANGHAI_TZ).replace(tzinfo=None)
    return local.strftime("%Y-%m-%d %H:%M")


def export_codex_quota_summary(snapshot: CodexUsageSnapshot) -> dict[str, Any]:
    return snapshot_to_dict(snapshot)


def _get_min_remaining_percent(settings: Settings) -> int:
    try:
        from app.audit.router import get_codex_usage_min_remaining_percent

        return get_codex_usage_min_remaining_percent()
    except Exception:
        return settings.codex_usage_min_remaining_percent


def _resolve_codex_path(settings: Settings) -> str | None:
    codex_path = settings.codex_path.strip()
    if not codex_path:
        return None
    return codex_path


def _codex_usage_env(settings: Settings) -> dict[str, str]:
    env = os.environ.copy()
    env.update(active_codex_profile_env(settings))
    return env


def _usage_cache_key(settings: Settings) -> str:
    profile_env = active_codex_profile_env(settings)
    return json.dumps(
        {
            "codex_home": profile_env.get("CODEX_HOME", ""),
            "codex_path": _resolve_codex_path(settings) or "",
        },
        sort_keys=True,
    )


def _status_from_snapshot(snapshot: CodexUsageSnapshot | None) -> CodexUsageStatus | None:
    if snapshot is None or not snapshot.available or snapshot.primary is None:
        return None
    return _status_from_window(snapshot.primary)


def _status_from_window(window: CodexRateLimitWindow) -> CodexUsageStatus:
    return CodexUsageStatus(
        remaining_percent=window.remaining_percent,
        reset_at=window.reset_at,
        reset_after_seconds=window.reset_after_seconds,
    )


def _get_fresh_cached_snapshot(cache_key: str) -> CodexUsageSnapshot | None:
    global _cached_snapshot, _cached_snapshot_at, _cached_snapshot_key
    if _cached_snapshot is None or _cached_snapshot_at is None:
        return None
    if _cached_snapshot_key != cache_key:
        return None
    age = (datetime.now(UTC) - _cached_snapshot_at).total_seconds()
    if age > CACHE_TTL_SECONDS:
        return None
    return _cached_snapshot


def _store_cached_snapshot(snapshot: CodexUsageSnapshot, cache_key: str) -> None:
    global _cached_snapshot, _cached_snapshot_at, _cached_snapshot_key
    _cached_snapshot = snapshot
    _cached_snapshot_at = datetime.now(UTC)
    _cached_snapshot_key = cache_key


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

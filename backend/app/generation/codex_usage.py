import asyncio
import json
import logging
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import Settings

logger = logging.getLogger("uvicorn.error")


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

    status = await get_codex_usage_status(settings)
    if status is None:
        logger.warning("Codex usage gate skipped because usage status is unavailable")
        return

    min_remaining = float(settings.codex_usage_min_remaining_percent)
    if status.remaining_percent >= min_remaining:
        return

    wait_seconds = status.wait_seconds
    if wait_seconds is None:
        wait_seconds = max(1, int(settings.quota_window_hours * 3600))
    wait_text = _format_wait_time(wait_seconds)
    detail = {
        "code": "codex_usage_low",
        "message": f"负载高，请等 {wait_text} 后再重试。",
        "remaining_percent": round(status.remaining_percent, 2),
        "reset_wait_seconds": wait_seconds,
    }
    logger.warning(
        "Codex usage gate blocked generation remaining_percent=%.2f min_remaining=%.2f wait_seconds=%s",
        status.remaining_percent,
        min_remaining,
        wait_seconds,
    )
    raise HTTPException(status_code=429, detail=detail)


async def get_codex_usage_status(settings: Settings) -> CodexUsageStatus | None:
    bridge_status = await _get_bridge_usage_status(settings)
    if bridge_status is not None:
        return bridge_status
    return await _get_command_usage_status(settings)


async def _get_bridge_usage_status(settings: Settings) -> CodexUsageStatus | None:
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
    return parse_codex_usage_payload(response.json())


async def _get_command_usage_status(settings: Settings) -> CodexUsageStatus | None:
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
    return parse_codex_usage_payload(payload)


def parse_codex_usage_payload(payload: Any) -> CodexUsageStatus | None:
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


def _format_wait_time(wait_seconds: int | None) -> str:
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


def usage_payload_from_env(value: str | None) -> CodexUsageStatus | None:
    if not value:
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    status = parse_codex_usage_payload(payload)
    if status and status.reset_after_seconds is not None and status.reset_at is None:
        return CodexUsageStatus(
            remaining_percent=status.remaining_percent,
            reset_after_seconds=status.reset_after_seconds,
            reset_at=datetime.now(UTC) + timedelta(seconds=status.reset_after_seconds),
        )
    return status

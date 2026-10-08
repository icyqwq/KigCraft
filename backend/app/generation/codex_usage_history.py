"""History of the Codex account's remaining quota, for the admin usage trend chart.

Every fresh reading is appended to a small JSON-lines file in the runtime directory, at most one per minute, and a
background task in the API takes a reading every few minutes so the curve has no gaps while nobody is watching.
Entries older than the retention window are dropped. A broken or missing file only means an empty chart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.generation.codex_app_server import CodexUsageSnapshot

logger = logging.getLogger("uvicorn.error")

FILE_NAME = "codex-usage-history.jsonl"
RETENTION_DAYS = 90
MIN_GAP_SECONDS = 60
_lock = threading.Lock()


def _path(settings: Settings) -> Path:
    return Path(settings.v2_runtime_dir) / FILE_NAME


def _entry(snapshot: CodexUsageSnapshot) -> dict[str, Any] | None:
    if not snapshot.available or snapshot.primary is None:
        return None
    entry: dict[str, Any] = {
        "at": snapshot.checked_at.astimezone(UTC).isoformat(timespec="seconds"),
        "plan": snapshot.plan_type,
        "p": round(snapshot.primary.remaining_percent, 1),
        "pw": snapshot.primary.window_duration_mins,
    }
    if snapshot.secondary is not None:
        entry["s"] = round(snapshot.secondary.remaining_percent, 1)
        entry["sw"] = snapshot.secondary.window_duration_mins
    return entry


def record(settings: Settings, snapshot: CodexUsageSnapshot) -> None:
    entry = _entry(snapshot)
    if entry is None:
        return
    try:
        path = _path(settings)
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            last = _last_time(path)
            if last is not None and (datetime.fromisoformat(entry["at"]) - last).total_seconds() < MIN_GAP_SECONDS:
                return
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
            if path.stat().st_size > 2_000_000:
                _prune(path)
    except OSError as exc:
        logger.warning("Codex usage history not saved: %s", exc)


def _last_time(path: Path) -> datetime | None:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 512))
            lines = stream.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            return datetime.fromisoformat(json.loads(line)["at"])
        except (ValueError, KeyError, TypeError):
            continue
    return None


def _prune(path: Path) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    kept = [line for line in _lines(path) if _time(line) is not None and _time(line) >= cutoff]
    temporary = path.with_suffix(".tmp")
    temporary.write_text("".join(kept), encoding="utf-8")
    os.replace(temporary, path)


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError:
        return []


def _time(line: str) -> datetime | None:
    try:
        return datetime.fromisoformat(json.loads(line)["at"])
    except (ValueError, KeyError, TypeError):
        return None


def read(settings: Settings, hours: int, *, max_points: int = 600) -> list[dict[str, Any]]:
    """Entries of the last `hours`, thinned evenly to at most `max_points`."""
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    points: list[dict[str, Any]] = []
    with _lock:
        lines = _lines(_path(settings))
    for line in lines:
        try:
            item = json.loads(line)
            moment = datetime.fromisoformat(item["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if moment >= cutoff:
            points.append(item)
    if len(points) > max_points:
        step = len(points) / max_points
        points = [points[int(index * step)] for index in range(max_points)] + [points[-1]]
    return points


async def sample_forever(settings: Settings, interval_seconds: int) -> None:
    from app.generation.codex_usage import get_codex_usage_snapshot

    while True:
        try:
            await get_codex_usage_snapshot(settings)  # a fresh reading is recorded by the call itself
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the chart must never take the API down
            logger.warning("Codex usage sample failed: %s", exc)
        await asyncio.sleep(interval_seconds)

"""Retention for this application's Codex rollout files, never image caches."""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import Settings
from app.core.paths import resolve_repo_path
from app.codex_profiles.service import PROFILE_ID_RE

logger = logging.getLogger("uvicorn.error")


@dataclass
class CleanupResult:
    scanned: int = 0
    deleted: int = 0
    deleted_bytes: int = 0
    skipped_recent: int = 0
    skipped_open: int = 0
    errors: int = 0


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _session_roots(settings: Settings) -> list[Path]:
    # Do not use Path.home() / .codex or an inherited CODEX_HOME: those can be
    # a developer's unrelated personal history. The API mounts runtime/codex-home.
    configured = resolve_repo_path(settings.codex_profiles_dir)
    if _linked(configured) or _linked(configured.parent):
        return []
    runtime = configured.parent.resolve()
    roots = []
    default_home = runtime / "codex-home"
    if default_home.is_dir() and not _linked(default_home):
        roots.append(default_home / "sessions")
    profiles = configured / "profiles"
    if profiles.is_dir() and not _linked(profiles):
        for profile in profiles.iterdir():
            if (
                PROFILE_ID_RE.fullmatch(profile.name)
                and profile.is_dir()
                and not _linked(profile)
                and (profile / "profile.json").is_file()
            ):
                roots.append(profile / "sessions")
    return [
        root
        for root in roots
        if root.is_dir() and not _linked(root) and root.resolve().is_relative_to(runtime)
    ]


def _eligible_files(root: Path):
    for base, directories, files in os.walk(root, followlinks=False):
        directory = Path(base)
        directories[:] = [name for name in directories if not _linked(directory / name)]
        for name in files:
            if name.startswith("rollout-") and name.endswith(".jsonl"):
                yield directory / name


def _open_session_files() -> set[tuple[int, int]]:
    """Linux: preserve open rollouts even if their mtime is old."""
    if sys.platform != "linux":
        return set()
    opened = set()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            for descriptor in (process / "fd").iterdir():
                try:
                    info = descriptor.stat()
                    if stat.S_ISREG(info.st_mode):
                        opened.add((info.st_dev, info.st_ino))
                except FileNotFoundError:
                    pass  # Process exited or closed this descriptor during enumeration.
        except (FileNotFoundError, ProcessLookupError):
            pass
    # Permission errors intentionally propagate: skip the sweep if we cannot
    # establish which files are open rather than risk deleting an active session.
    return opened


def cleanup_codex_sessions(settings: Settings, *, now: float | None = None) -> CleanupResult:
    result = CleanupResult()
    if settings.v2_enabled or not settings.codex_session_cleanup_enabled:
        return result
    retention_days = settings.codex_session_retention_days
    if retention_days < 1:
        raise ValueError("Codex session retention must be at least one day")
    cutoff = (time.time() if now is None else now) - retention_days * 86400
    roots = _session_roots(settings)
    if not roots:
        return result
    opened = _open_session_files()
    for root in roots:
        for candidate in _eligible_files(root):
            try:
                info = candidate.lstat()
                if not stat.S_ISREG(info.st_mode) or _linked(candidate):
                    continue
                result.scanned += 1
                if info.st_mtime >= cutoff:
                    result.skipped_recent += 1
                    continue
                if (info.st_dev, info.st_ino) in opened:
                    result.skipped_open += 1
                    continue
                # Revalidate the actual absolute path and every component just
                # before unlink. Never follow junctions/symlinks outside sessions.
                if not candidate.resolve().is_relative_to(root.resolve()):
                    continue
                if any(_linked(part) for part in (candidate, *candidate.parents)):
                    continue
                latest = candidate.lstat()
                if (latest.st_dev, latest.st_ino, latest.st_size, latest.st_mtime_ns) != (
                    info.st_dev,
                    info.st_ino,
                    info.st_size,
                    info.st_mtime_ns,
                ):
                    continue
                candidate.unlink()
                result.deleted += 1
                result.deleted_bytes += info.st_size
            except FileNotFoundError:
                pass  # Another API process already removed this expired file.
            except OSError:
                result.errors += 1
    return result


async def _run_cleanup(settings: Settings) -> None:
    try:
        result = await asyncio.to_thread(cleanup_codex_sessions, settings)
        logger.info("Codex session cleanup result=%s", asdict(result))
    except Exception as exc:
        # Log only the error class, never credentials, filenames or session content.
        logger.warning("Codex session cleanup skipped error_type=%s", type(exc).__name__)


async def _cleanup_loop(settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.codex_session_cleanup_interval_seconds
            )
        except TimeoutError:
            await _run_cleanup(settings)


@asynccontextmanager
async def session_cleanup_lifespan(settings: Settings):
    if settings.v2_enabled or not settings.codex_session_cleanup_enabled:
        yield
        return
    # Run before database initialization: a full disk can prevent DB startup.
    await _run_cleanup(settings)
    stop = asyncio.Event()
    task = asyncio.create_task(_cleanup_loop(settings, stop), name="codex-session-cleanup")
    try:
        yield
    finally:
        stop.set()
        # Finish an in-flight filesystem sweep instead of abandoning a to_thread task.
        await task

"""Shrink idle Codex session files by dropping the embedded image bytes.

Codex stores every image it saw or produced inside the session file (about 8 MB per generated image, several copies of
the same picture). Old pictures are not needed to continue a conversation: each V2 turn sends the current base and
references again. This tool replaces the embedded image bytes of sessions that have been idle for a while with a short
text note, keeping the conversation itself intact so the thread can still be resumed.

    python -m app.maintenance.codex_rollout_compact --codex-home <dir> --min-idle-hours 24          # report only
    python -m app.maintenance.codex_rollout_compact --codex-home <dir> --min-idle-hours 24 --apply  # rewrite

Files in use, or modified within the idle window, are never touched. Each file is rewritten atomically. The derived
history index (thread_history_1.sqlite) records byte offsets into the session files; it is removed after a rewrite so
Codex rebuilds it from the compacted files, but only when the worker is stopped (--apply refuses otherwise).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

NOTE = "[earlier image omitted to save disk space]"
MIN_FILE_BYTES = 256 * 1024
MIN_BASE64 = 2048


def _open_files() -> set[tuple[int, int]]:
    if sys.platform != "linux":
        return set()
    opened = set()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            for descriptor in (process / "fd").iterdir():
                try:
                    info = os.stat(descriptor)
                except OSError:
                    continue
                opened.add((info.st_dev, info.st_ino))
        except OSError:
            continue
    return opened


def _identity(path: Path):
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_dev, info.st_ino


def _strip(value):
    """Return (new_value, changed). Image items become a short text item; bare image payloads are emptied."""
    changed = False
    if isinstance(value, dict):
        url = value.get("image_url")
        if value.get("type") in {"input_image", "output_image"} and isinstance(url, str) and url.startswith("data:image/"):
            return {"type": "input_text", "text": NOTE}, True
        if value.get("type") == "imageGeneration" and isinstance(value.get("result"), str) and len(value["result"]) > MIN_BASE64:
            value = {**value, "result": ""}
            changed = True
        out = {}
        for key, item in value.items():
            new, did = _strip(item)
            out[key] = new
            changed = changed or did
        return out, changed
    if isinstance(value, list):
        out = []
        for item in value:
            new, did = _strip(item)
            out.append(new)
            changed = changed or did
        return out, changed
    return value, False


def compact_file(path: Path) -> tuple[int, int]:
    """Rewrite one session file; returns (bytes_before, bytes_after). Lines that cannot be parsed are kept verbatim."""
    started = path.stat()
    before = started.st_size
    handle, temporary = tempfile.mkstemp(prefix=".compact-", dir=path.parent)
    changed_any = False
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as target, path.open("r", encoding="utf-8", newline="") as source:
            for line in source:
                if "image" not in line or len(line) < MIN_BASE64:
                    target.write(line)
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    target.write(line)
                    continue
                record, changed = _strip(record)
                if not changed:
                    target.write(line)
                    continue
                changed_any = True
                target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + ("\n" if line.endswith("\n") else ""))
            target.flush()
            os.fsync(target.fileno())
        if not changed_any:
            os.unlink(temporary)
            return before, before
        now = path.stat()
        if (now.st_size, now.st_mtime_ns) != (started.st_size, started.st_mtime_ns):
            # Codex appended while we worked: keep the live file, try again on a later run.
            os.unlink(temporary)
            return before, before
        os.chmod(temporary, now.st_mode & 0o777)
        os.replace(temporary, path)
        return before, path.stat().st_size
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def run(codex_home: Path, min_idle_hours: float, apply: bool) -> dict:
    sessions = codex_home / "sessions"
    now = time.time()
    opened = _open_files()
    index = [codex_home / name for name in ("thread_history_1.sqlite", "thread_history_1.sqlite-wal", "thread_history_1.sqlite-shm")]
    if apply and any(_identity(item) in opened for item in index):
        # The history index is derived from byte offsets in the session files; never change them under a running Codex.
        return {"refused": "history_index_in_use"}
    report = {"scanned": 0, "candidates": 0, "compacted": 0, "bytes_before": 0, "bytes_after": 0, "skipped_recent": 0, "skipped_open": 0}
    rewritten = False
    for path in sorted(sessions.rglob("rollout-*.jsonl")) if sessions.is_dir() else []:
        if path.is_symlink() or not path.is_file():
            continue
        report["scanned"] += 1
        info = path.stat()
        if info.st_size < MIN_FILE_BYTES:
            continue
        if now - info.st_mtime < min_idle_hours * 3600:
            report["skipped_recent"] += 1
            continue
        if (info.st_dev, info.st_ino) in opened:
            report["skipped_open"] += 1
            continue
        report["candidates"] += 1
        if not apply:
            continue
        before, after = compact_file(path)
        report["bytes_before"] += before
        report["bytes_after"] += after
        if after < before:
            report["compacted"] += 1
            rewritten = True
    if apply and rewritten:
        for item in index:
            item.unlink(missing_ok=True)
        report["history_index_reset"] = True
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--min-idle-hours", type=float, default=24)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.codex_home.resolve(), args.min_idle_hours, args.apply), indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.core.config import Settings, get_settings


@dataclass(frozen=True)
class CodexUpdateStatus:
    checked_at: str
    version_before: str | None
    version_after: str | None
    succeeded: bool
    error: str | None


Runner = Callable[..., subprocess.CompletedProcess[str]]
_VERSION_PATTERN = re.compile(r"codex-cli \d+(?:\.\d+){2}")


class _CommandFailure(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _write_status(path_value: str, status: CodexUpdateStatus) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(asdict(status), ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run(
    runner: Runner,
    command: list[str],
    timeout: int,
    stage: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(command, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise _CommandFailure("codex_not_found") from exc
    except subprocess.TimeoutExpired as exc:
        raise _CommandFailure(f"{stage}_timed_out") from exc
    except subprocess.CalledProcessError as exc:
        raise _CommandFailure(f"{stage}_failed_exit_{exc.returncode}") from exc
    except subprocess.SubprocessError as exc:
        raise _CommandFailure(f"{stage}_failed") from exc
    except OSError as exc:
        raise _CommandFailure("codex_os_error") from exc
    if result.returncode:
        raise _CommandFailure(f"{stage}_failed_exit_{result.returncode}")
    return result


def _read_version(runner: Runner, settings: Settings) -> str:
    output = _run(
        runner,
        [settings.codex_path, "--version"],
        settings.codex_update_timeout_seconds,
        "version_check",
    ).stdout.strip()
    if not _VERSION_PATTERN.fullmatch(output):
        raise _CommandFailure("version_check_invalid_output")
    return output


def update_codex_cli(
    settings: Settings | None = None,
    runner: Runner = subprocess.run,
) -> CodexUpdateStatus:
    active_settings = settings or get_settings()
    checked_at = datetime.now(timezone.utc).isoformat()
    version_before: str | None = None
    version_after: str | None = None
    succeeded = True
    error: str | None = None

    if active_settings.v2_enabled:
        # Do not inspect/update a shared host binary or write legacy status files.
        return CodexUpdateStatus(checked_at, None, None, False, "v2_runtime_pinned")

    if active_settings.codex_update_enabled:
        try:
            version_before = _read_version(runner, active_settings)
            _run(
                runner,
                [active_settings.codex_path, "update"],
                active_settings.codex_update_timeout_seconds,
                "update",
            )
            version_after = _read_version(runner, active_settings)
        except _CommandFailure as exc:
            succeeded = False
            error = exc.category[:500]

    status = CodexUpdateStatus(
        checked_at=checked_at,
        version_before=version_before,
        version_after=version_after,
        succeeded=succeeded,
        error=error,
    )
    try:
        _write_status(active_settings.codex_update_status_path, status)
    except OSError:
        pass
    return status


def read_codex_update_status(settings: Settings | None = None) -> CodexUpdateStatus | None:
    active_settings = settings or get_settings()
    try:
        payload = json.loads(
            Path(active_settings.codex_update_status_path).read_text(encoding="utf-8")
        )
        return CodexUpdateStatus(**payload)
    except (OSError, ValueError, TypeError):
        return None


def main() -> int:
    try:
        update_codex_cli()
    except Exception:
        # Container startup must never depend on updater health.
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

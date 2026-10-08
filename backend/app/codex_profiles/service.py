from __future__ import annotations

import base64
import json

import asyncio
import os
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.paths import resolve_repo_path
from app.core.time import utc_now


PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
DEFAULT_PROFILE_ID = "__default__"
DEFAULT_PROFILE_NAME = "默认账号"
DEFAULT_PROFILE_SOURCE = "codex-home"
ALLOWED_CREDENTIAL_FILES = {"auth.json", "config.toml", "credentials.json"}
PROFILE_METADATA_FILE = "profile.json"
ACTIVE_PROFILE_FILE = "active-profile.json"
DEVICE_AUTH_OUTPUT_LIMIT = 8_000
DEVICE_AUTH_TIMEOUT_SECONDS = 300.0


class CodexProfileOut(BaseModel):
    id: str
    name: str
    is_active: bool = False
    is_builtin: bool = False
    credential_files: list[str] = []
    account_email: str | None = None
    created_at: str
    updated_at: str


class CodexDeviceAuthSessionOut(BaseModel):
    id: str
    profile_id: str
    status: str
    output: str = ""
    error: str | None = None
    created_at: str
    completed_at: str | None = None


@dataclass
class _DeviceAuthSession:
    id: str
    profile_id: str
    process: asyncio.subprocess.Process | None
    created_at: datetime
    settings: Settings | None = None
    status: str = "running"
    output: str = ""
    error: str | None = None
    completed_at: datetime | None = None
    task: asyncio.Task | None = field(default=None)


_device_auth_sessions: dict[str, _DeviceAuthSession] = {}


def profile_root(settings: Settings | None = None) -> Path:
    active_settings = settings or get_settings()
    root = resolve_repo_path(
        str(getattr(active_settings, "codex_profiles_dir", "runtime/codex-profiles"))
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _profiles_root(settings: Settings | None = None) -> Path:
    root = profile_root(settings) / "profiles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _active_profile_path(settings: Settings | None = None) -> Path:
    return profile_root(settings) / ACTIVE_PROFILE_FILE


def _validate_profile_id(profile_id: str) -> str:
    normalized = profile_id.strip()
    if not PROFILE_ID_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="codex_profile_id_invalid")
    return normalized


def _profile_dir(profile_id: str, settings: Settings | None = None) -> Path:
    safe_profile_id = _validate_profile_id(profile_id)
    root = _profiles_root(settings).resolve()
    candidate = (root / safe_profile_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="codex_profile_id_invalid") from exc
    return candidate


def _metadata_path(profile_id: str, settings: Settings | None = None) -> Path:
    return _profile_dir(profile_id, settings) / PROFILE_METADATA_FILE


def _datetime_out(value: datetime) -> str:
    return value.isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _credential_files_in(directory: Path) -> list[str]:
    return sorted(file_name for file_name in ALLOWED_CREDENTIAL_FILES if (directory / file_name).is_file())


def default_codex_home_dir(settings: Settings | None = None) -> Path:
    del settings
    env_home = os.environ.get("CODEX_HOME", "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def _default_codex_profile_out(settings: Settings | None = None) -> CodexProfileOut:
    home = default_codex_home_dir(settings)
    credential_files = _credential_files_in(home)
    auth_path = home / "auth.json"
    timestamp = _datetime_out(utc_now())
    if auth_path.is_file():
        with suppress(OSError):
            timestamp = _datetime_out(datetime.fromtimestamp(auth_path.stat().st_mtime, tz=UTC))
    email = account_email(home)
    return CodexProfileOut(
        id=DEFAULT_PROFILE_ID,
        name=email or DEFAULT_PROFILE_NAME,
        account_email=email,
        is_active=_active_profile_id(settings) is None,
        is_builtin=True,
        credential_files=credential_files,
        created_at=timestamp,
        updated_at=timestamp,
    )


GENERIC_PROFILE_NAMES = {"Codex account"}


def account_email(directory: Path) -> str | None:
    """Email claim of the signed-in account, read locally from its own login file.

    The JWT is only decoded, never verified or sent anywhere, and nothing else from the file is returned.
    """
    data = _read_json(directory / "auth.json") or {}
    tokens = data.get("tokens")
    token = tokens.get("id_token") if isinstance(tokens, dict) else None
    if not isinstance(token, str) or token.count(".") != 2:
        return None
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    profile = claims.get("https://api.openai.com/profile")
    email = claims.get("email") or (profile.get("email") if isinstance(profile, dict) else None)
    return email.strip()[:200] if isinstance(email, str) and "@" in email else None


def _credential_files(profile_id: str, settings: Settings | None = None) -> list[str]:
    directory = _profile_dir(profile_id, settings)
    return _credential_files_in(directory)


def _active_profile_id(settings: Settings | None = None) -> str | None:
    data = _read_json(_active_profile_path(settings))
    profile_id = data.get("id") if data else None
    if not isinstance(profile_id, str):
        return None
    try:
        _validate_profile_id(profile_id)
    except HTTPException:
        return None
    if not _metadata_path(profile_id, settings).is_file():
        return None
    return profile_id


def _profile_out(profile_id: str, settings: Settings | None = None) -> CodexProfileOut:
    metadata = _read_json(_metadata_path(profile_id, settings))
    if metadata is None:
        raise HTTPException(status_code=404, detail="codex_profile_not_found")
    now = _datetime_out(utc_now())
    active_id = _active_profile_id(settings)
    email = account_email(_profile_dir(profile_id, settings))
    name = str(metadata.get("name") or profile_id)
    if email and (name in GENERIC_PROFILE_NAMES or name == profile_id):
        # Unnamed accounts are shown by the email they signed in with; an explicit rename always wins.
        name = email
    return CodexProfileOut(
        id=profile_id,
        name=name,
        account_email=email,
        is_active=profile_id == active_id,
        is_builtin=False,
        credential_files=_credential_files(profile_id, settings),
        created_at=str(metadata.get("created_at") or now),
        updated_at=str(metadata.get("updated_at") or now),
    )


def list_codex_profiles(settings: Settings | None = None) -> list[CodexProfileOut]:
    profiles = [_default_codex_profile_out(settings)]
    root = _profiles_root(settings)
    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if child.is_dir() and PROFILE_ID_RE.fullmatch(child.name) and (child / PROFILE_METADATA_FILE).is_file():
            profiles.append(_profile_out(child.name, settings))
    return profiles


def create_codex_profile(profile_id: str, name: str, settings: Settings | None = None) -> CodexProfileOut:
    safe_profile_id = _validate_profile_id(profile_id)
    display_name = name.strip() or safe_profile_id
    directory = _profile_dir(safe_profile_id, settings)
    metadata_path = directory / PROFILE_METADATA_FILE
    now = _datetime_out(utc_now())
    if metadata_path.is_file():
        metadata = _read_json(metadata_path) or {}
        created_at = str(metadata.get("created_at") or now)
    else:
        created_at = now
    _write_json(metadata_path, {"id": safe_profile_id, "name": display_name, "created_at": created_at, "updated_at": now})
    return _profile_out(safe_profile_id, settings)


def _credential_path(profile_id: str, file_name: str, settings: Settings | None = None) -> Path:
    if file_name not in ALLOWED_CREDENTIAL_FILES or "/" in file_name or "\\" in file_name:
        raise HTTPException(status_code=400, detail="codex_profile_file_not_allowed")
    directory = _profile_dir(profile_id, settings).resolve()
    candidate = (directory / file_name).resolve()
    try:
        candidate.relative_to(directory)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="codex_profile_file_not_allowed") from exc
    return candidate


def import_codex_profile_credentials(
    profile_id: str,
    files: dict[str, str],
    settings: Settings | None = None,
) -> CodexProfileOut:
    _profile_out(profile_id, settings)
    if not files:
        raise HTTPException(status_code=400, detail="codex_profile_credentials_required")
    for file_name, contents in files.items():
        path = _credential_path(profile_id, file_name, settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        with suppress(OSError):
            path.chmod(0o600)
    metadata = _read_json(_metadata_path(profile_id, settings)) or {}
    metadata["updated_at"] = _datetime_out(utc_now())
    _write_json(_metadata_path(profile_id, settings), metadata)
    return _profile_out(profile_id, settings)


def rename_codex_profile(profile_id: str, name: str, settings: Settings | None = None) -> CodexProfileOut:
    if profile_id == DEFAULT_PROFILE_ID:
        raise HTTPException(status_code=400, detail="codex_profile_builtin_readonly")
    _profile_out(profile_id, settings)
    display_name = " ".join(name.split())
    if not display_name:
        raise HTTPException(status_code=422, detail="codex_profile_name_required")
    metadata = _read_json(_metadata_path(profile_id, settings)) or {}
    metadata["name"] = display_name[:120]
    metadata["updated_at"] = _datetime_out(utc_now())
    _write_json(_metadata_path(profile_id, settings), metadata)
    return _profile_out(profile_id, settings)


def activate_codex_profile(profile_id: str, settings: Settings | None = None) -> CodexProfileOut:
    if profile_id == DEFAULT_PROFILE_ID:
        deactivate_codex_profile(settings)
        return _default_codex_profile_out(settings)
    profile = _profile_out(profile_id, settings)
    if not profile.credential_files:
        raise HTTPException(status_code=409, detail="codex_profile_credentials_missing")
    _write_json(_active_profile_path(settings), {"id": profile.id, "updated_at": _datetime_out(utc_now())})
    return _profile_out(profile.id, settings)


def deactivate_codex_profile(settings: Settings | None = None) -> None:
    with suppress(FileNotFoundError):
        _active_profile_path(settings).unlink()


def delete_codex_profile(profile_id: str, settings: Settings | None = None) -> None:
    if profile_id == DEFAULT_PROFILE_ID:
        raise HTTPException(status_code=400, detail="codex_profile_builtin_delete_forbidden")
    import shutil

    directory = _profile_dir(profile_id, settings)
    if not directory.exists():
        raise HTTPException(status_code=404, detail="codex_profile_not_found")
    active_id = _active_profile_id(settings)
    shutil.rmtree(directory)
    if active_id == profile_id:
        with suppress(FileNotFoundError):
            _active_profile_path(settings).unlink()


def active_codex_profile_env(settings: Settings | None = None) -> dict[str, str]:
    active_id = _active_profile_id(settings)
    if not active_id:
        return {}
    return {"CODEX_HOME": str(_profile_dir(active_id, settings))}


def _profile_env(profile_id: str, settings: Settings | None = None) -> dict[str, str]:
    return {"CODEX_HOME": str(_profile_dir(profile_id, settings))}


async def start_device_auth(settings: Settings | None = None) -> CodexDeviceAuthSessionOut:
    active_settings = settings or get_settings()
    profile_id = f"codex-{uuid4().hex[:12]}"
    create_codex_profile(profile_id, "Codex account", active_settings)
    return await start_device_auth_for_profile(profile_id, active_settings)


async def start_device_auth_for_profile(
    profile_id: str,
    settings: Settings | None = None,
) -> CodexDeviceAuthSessionOut:
    _profile_out(profile_id, settings)
    active_settings = settings or get_settings()
    command = [active_settings.codex_path, "login", "--device-auth"]
    env = os.environ.copy()
    env.update(_profile_env(profile_id, active_settings))
    session_id = str(uuid4())
    created_at = utc_now()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as exc:
        session = _DeviceAuthSession(
            id=session_id,
            profile_id=profile_id,
            process=None,
            created_at=created_at,
            settings=active_settings,
            status="failed",
            error=str(exc),
            completed_at=utc_now(),
        )
        _device_auth_sessions[session_id] = session
        return _device_auth_out(session)

    session = _DeviceAuthSession(
        id=session_id,
        profile_id=profile_id,
        process=process,
        created_at=created_at,
        settings=active_settings,
    )
    session.task = asyncio.create_task(_collect_device_auth_session(session))
    _device_auth_sessions[session_id] = session
    return _device_auth_out(session)


async def _collect_device_auth_session(session: _DeviceAuthSession) -> None:
    process = session.process
    if process is None:
        return
    readers = [
        asyncio.create_task(_read_device_auth_stream(session, stream))
        for stream in (process.stdout, process.stderr)
    ]
    try:
        await asyncio.wait_for(process.wait(), timeout=DEVICE_AUTH_TIMEOUT_SECONDS)
        await asyncio.gather(*readers)
    except asyncio.TimeoutError:
        process.terminate()
        await process.wait()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        if session.status == "running":
            session.status = "timed_out"
            session.error = "codex_device_auth_timed_out"
            session.completed_at = utc_now()
        return
    except asyncio.CancelledError:
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)
        return

    session.completed_at = utc_now()
    if session.status == "cancelled":
        return
    if process.returncode == 0:
        if (_profile_dir(session.profile_id, session.settings) / "auth.json").is_file():
            activate_codex_profile(session.profile_id, session.settings)
            session.status = "succeeded"
        else:
            session.status = "failed"
            session.error = "codex_device_auth_credentials_missing"
    else:
        session.status = "failed"
        session.error = session.error or "codex_device_auth_failed"


async def _read_device_auth_stream(
    session: _DeviceAuthSession,
    stream: asyncio.StreamReader | None,
) -> None:
    if stream is None:
        return
    while chunk := await stream.read(1024):
        text = chunk.decode("utf-8", errors="ignore")
        session.output = (session.output + text)[-DEVICE_AUTH_OUTPUT_LIMIT:]


def get_device_auth_session(profile_id: str, session_id: str) -> CodexDeviceAuthSessionOut:
    session = _device_auth_sessions.get(session_id)
    if session is None or session.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="codex_device_auth_session_not_found")
    return _device_auth_out(session)


async def cancel_device_auth_session(profile_id: str, session_id: str) -> CodexDeviceAuthSessionOut:
    session = _device_auth_sessions.get(session_id)
    if session is None or session.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="codex_device_auth_session_not_found")
    if session.status == "running" and session.process is not None:
        session.process.terminate()
        session.status = "cancelled"
        session.completed_at = utc_now()
        if session.task is not None:
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
        await session.process.wait()
    return _device_auth_out(session)


def _device_auth_out(session: _DeviceAuthSession) -> CodexDeviceAuthSessionOut:
    return CodexDeviceAuthSessionOut(
        id=session.id,
        profile_id=session.profile_id,
        status=session.status,
        output=session.output,
        error=session.error,
        created_at=_datetime_out(session.created_at),
        completed_at=_datetime_out(session.completed_at) if session.completed_at else None,
    )

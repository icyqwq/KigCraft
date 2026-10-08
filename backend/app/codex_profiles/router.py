from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.admin.dependencies import require_admin_user
from app.codex_update import read_codex_update_status
from app.codex_profiles.service import (
    CodexDeviceAuthSessionOut,
    CodexProfileOut,
    activate_codex_profile,
    cancel_device_auth_session,
    deactivate_codex_profile,
    create_codex_profile,
    delete_codex_profile,
    get_device_auth_session,
    import_codex_profile_credentials,
    list_codex_profiles,
    rename_codex_profile,
    start_device_auth,
    start_device_auth_for_profile,
)
from app.db.models import User


router = APIRouter(prefix="/admin/codex-profiles", tags=["admin"])


class CodexProfileCreateIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)


class CodexProfileRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CodexProfileCredentialsIn(BaseModel):
    files: dict[str, str] = Field(default_factory=dict)


class CodexProfilesOut(BaseModel):
    profiles: list[CodexProfileOut]


class CodexUpdateStatusOut(BaseModel):
    available: bool
    checked_at: str | None = None
    version_before: str | None = None
    version_after: str | None = None
    succeeded: bool | None = None
    error: str | None = None


@router.get("", response_model=CodexProfilesOut)
async def list_admin_codex_profiles(_: User = Depends(require_admin_user)) -> CodexProfilesOut:
    return CodexProfilesOut(profiles=list_codex_profiles())


@router.get("/update-status", response_model=CodexUpdateStatusOut)
async def get_admin_codex_update_status(
    _: User = Depends(require_admin_user),
) -> CodexUpdateStatusOut:
    status = read_codex_update_status()
    if status is None:
        return CodexUpdateStatusOut(available=False)
    return CodexUpdateStatusOut(available=True, **status.__dict__)


@router.post("", response_model=CodexProfileOut)
async def create_admin_codex_profile(
    payload: CodexProfileCreateIn,
    _: User = Depends(require_admin_user),
) -> CodexProfileOut:
    return create_codex_profile(payload.id, payload.name)


@router.patch("/{profile_id}", response_model=CodexProfileOut)
async def rename_admin_codex_profile(
    profile_id: str,
    payload: CodexProfileRenameIn,
    _: User = Depends(require_admin_user),
) -> CodexProfileOut:
    return rename_codex_profile(profile_id, payload.name)


@router.put("/{profile_id}/credentials", response_model=CodexProfileOut)
async def import_admin_codex_profile_credentials(
    profile_id: str,
    payload: CodexProfileCredentialsIn,
    _: User = Depends(require_admin_user),
) -> CodexProfileOut:
    return import_codex_profile_credentials(profile_id, payload.files)


@router.post("/{profile_id}/activate", response_model=CodexProfileOut)
async def activate_admin_codex_profile(
    profile_id: str,
    _: User = Depends(require_admin_user),
) -> CodexProfileOut:
    return activate_codex_profile(profile_id)


@router.post("/deactivate", response_model=dict[str, bool])
async def deactivate_admin_codex_profile(
    _: User = Depends(require_admin_user),
) -> dict[str, bool]:
    deactivate_codex_profile()
    return {"ok": True}


@router.post("/device-auth", response_model=CodexDeviceAuthSessionOut)
async def start_admin_codex_device_auth(
    _: User = Depends(require_admin_user),
) -> CodexDeviceAuthSessionOut:
    return await start_device_auth()


@router.delete("/{profile_id}", response_model=dict[str, bool])
async def delete_admin_codex_profile(
    profile_id: str,
    _: User = Depends(require_admin_user),
) -> dict[str, bool]:
    delete_codex_profile(profile_id)
    return {"ok": True}


@router.post("/{profile_id}/device-auth", response_model=CodexDeviceAuthSessionOut)
async def start_admin_codex_profile_device_auth(
    profile_id: str,
    _: User = Depends(require_admin_user),
) -> CodexDeviceAuthSessionOut:
    return await start_device_auth_for_profile(profile_id)


@router.get("/{profile_id}/device-auth/{session_id}", response_model=CodexDeviceAuthSessionOut)
async def get_admin_codex_profile_device_auth(
    profile_id: str,
    session_id: str,
    _: User = Depends(require_admin_user),
) -> CodexDeviceAuthSessionOut:
    return get_device_auth_session(profile_id, session_id)


@router.post("/{profile_id}/device-auth/{session_id}/cancel", response_model=CodexDeviceAuthSessionOut)
async def cancel_admin_codex_profile_device_auth(
    profile_id: str,
    session_id: str,
    _: User = Depends(require_admin_user),
) -> CodexDeviceAuthSessionOut:
    return await cancel_device_auth_session(profile_id, session_id)

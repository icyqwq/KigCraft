from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.captcha import verify_captcha_token
from app.auth.email_codes import send_verification_code, verify_email_code
from app.auth.login_rate_limit import (
    clear_login_failures,
    enforce_login_failure_limit,
    record_login_failure,
)
from app.auth.send_rate_limit import enforce_verification_code_send_limits
from app.auth.models import CurrentTermsVersionOut, CurrentUserOut, UserRole, VerificationPurpose
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import (
    create_session_response,
    get_current_user,
    get_current_user_and_session,
    revoke_current_session,
)
from app.core.client_ip import resolve_client_ip
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession, User
from app.db.session import get_session
from app.generation.quota import get_generation_quota_state

router = APIRouter(prefix="/auth", tags=["auth"])


class CaptchaRequestMixin(BaseModel):
    captcha_token: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def require_captcha_token_when_enabled(self):
        if get_settings().captcha_enabled and self.captcha_token is None:
            raise ValueError("captcha_required")
        return self


class SendCodeRequest(CaptchaRequestMixin):
    email: EmailStr
    purpose: VerificationPurpose


class RegisterRequest(CaptchaRequestMixin):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    password: str = Field(min_length=8, max_length=256)
    terms_accepted: Literal[True]
    terms_version: str = Field(min_length=1, max_length=32)


class PasswordLoginRequest(CaptchaRequestMixin):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class CodeLoginRequest(CaptchaRequestMixin):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)


class ResetPasswordRequest(CaptchaRequestMixin):
    email: EmailStr
    code: str = Field(min_length=4, max_length=12)
    password: str = Field(min_length=8, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class PrivacyPreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation_quality_improvement_consent: bool


class CaptchaConfigOut(BaseModel):
    enabled: bool
    site_key: str | None = None


def _datetime_out(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _client_ip(request: Request) -> str:
    return resolve_client_ip(request, get_settings().trusted_proxy_hosts)


async def user_to_out(user: User, session: AsyncSession) -> CurrentUserOut:
    quota_state = await get_generation_quota_state(session, user)
    return CurrentUserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        is_banned=user.is_banned,
        daily_generation_remaining=quota_state.remaining,
        daily_generation_reset_at=_datetime_out(quota_state.reset_at),
        terms_version=user.terms_version,
        terms_accepted_at=_datetime_out(user.terms_accepted_at),
        generation_quality_improvement_consent=user.generation_quality_improvement_consent,
        generation_quality_improvement_consent_updated_at=_datetime_out(
            user.generation_quality_improvement_consent_updated_at
        ),
    )


@router.get("/captcha", response_model=CaptchaConfigOut)
async def captcha_config() -> CaptchaConfigOut:
    settings = get_settings()
    return CaptchaConfigOut(
        enabled=settings.captcha_enabled,
        site_key=settings.turnstile_site_key if settings.captcha_enabled else None,
    )


@router.get("/terms-version", response_model=CurrentTermsVersionOut)
async def current_terms_version() -> CurrentTermsVersionOut:
    return CurrentTermsVersionOut(version=get_settings().current_terms_version)


@router.post("/send-code")
async def send_code(
    payload: SendCodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    client_ip = _client_ip(request)
    await verify_captcha_token(payload.captcha_token or "", remote_ip=client_ip)
    email = str(payload.email).lower()
    result = await db.execute(select(User).where(User.email == email))
    user_exists = result.scalar_one_or_none() is not None
    if payload.purpose == VerificationPurpose.register and user_exists:
        raise HTTPException(status_code=409, detail="email_already_registered")
    if payload.purpose in {VerificationPurpose.login, VerificationPurpose.reset_password} and not user_exists:
        raise HTTPException(status_code=404, detail="account_not_found")
    await enforce_verification_code_send_limits(db, email, client_ip)
    await send_verification_code(
        db,
        email,
        payload.purpose,
        client_ip,
        rate_limit_checked=True,
    )
    return {"ok": True}


@router.post("/register", response_model=CurrentUserOut)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> CurrentUserOut:
    await verify_captcha_token(payload.captcha_token or "", remote_ip=_client_ip(request))
    if payload.terms_version != get_settings().current_terms_version:
        raise HTTPException(status_code=400, detail="terms_version_outdated")
    email = str(payload.email).lower()
    await verify_email_code(db, email, VerificationPurpose.register, payload.code)
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="email_already_registered")
    user = User(
        email=email,
        email_verified_at=utc_now(),
        password_hash=hash_password(payload.password),
        role=UserRole.user,
        terms_version=payload.terms_version,
        terms_accepted_at=utc_now(),
    )
    db.add(user)
    await db.flush()
    await create_session_response(db, user, request, response)
    return await user_to_out(user, db)


@router.post("/login/password", response_model=CurrentUserOut)
async def login_password(
    payload: PasswordLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> CurrentUserOut:
    client_ip = _client_ip(request)
    email = str(payload.email).lower()
    await verify_captcha_token(payload.captcha_token or "", remote_ip=client_ip)
    await enforce_login_failure_limit(db, email, client_ip)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        await record_login_failure(db, email, client_ip)
        raise HTTPException(status_code=404, detail="account_not_found")
    if not verify_password(payload.password, user.password_hash):
        await record_login_failure(db, email, client_ip)
        raise HTTPException(status_code=401, detail="invalid_email_or_password")
    if user.is_banned:
        raise HTTPException(status_code=403, detail="account_unavailable")
    await clear_login_failures(db, email, client_ip)
    await create_session_response(db, user, request, response)
    return await user_to_out(user, db)


@router.post("/login/code", response_model=CurrentUserOut)
async def login_code(
    payload: CodeLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> CurrentUserOut:
    await verify_captcha_token(payload.captcha_token or "", remote_ip=_client_ip(request))
    email = str(payload.email).lower()
    await verify_email_code(db, email, VerificationPurpose.login, payload.code)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    if user.is_banned:
        raise HTTPException(status_code=403, detail="account_unavailable")
    await create_session_response(db, user, request, response)
    return await user_to_out(user, db)


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    await verify_captcha_token(payload.captcha_token or "", remote_ip=_client_ip(request))
    email = str(payload.email).lower()
    await verify_email_code(db, email, VerificationPurpose.reset_password, payload.code)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="account_not_found")
    user.password_hash = hash_password(payload.password)
    await db.execute(
        AuthSession.__table__.update()
        .where(AuthSession.user_id == user.id)
        .where(AuthSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    await db.commit()
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
    user_and_session: tuple[User, AuthSession] = Depends(get_current_user_and_session),
) -> dict[str, bool]:
    user, current_session = user_and_session
    client_ip = _client_ip(request)
    await enforce_login_failure_limit(db, user.email, client_ip)
    if not verify_password(payload.current_password, user.password_hash):
        await record_login_failure(db, user.email, client_ip)
        raise HTTPException(status_code=401, detail="invalid_current_password")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="password_unchanged")
    await clear_login_failures(db, user.email, client_ip)
    user.password_hash = hash_password(payload.new_password)
    await db.execute(
        AuthSession.__table__.update()
        .where(AuthSession.user_id == user.id)
        .where(AuthSession.revoked_at.is_(None))
        .where(AuthSession.id != current_session.id)
        .values(revoked_at=utc_now())
    )
    await db.commit()
    return {"ok": True}


@router.post("/sessions/revoke-others")
async def revoke_other_sessions(
    db: AsyncSession = Depends(get_session),
    user_and_session: tuple[User, AuthSession] = Depends(get_current_user_and_session),
) -> dict[str, int]:
    user, current_session = user_and_session
    now = utc_now()
    result = await db.execute(
        AuthSession.__table__.update()
        .where(AuthSession.user_id == user.id, AuthSession.id != current_session.id)
        .where(AuthSession.revoked_at.is_(None), AuthSession.expires_at >= now)
        .values(revoked_at=now)
    )
    await db.commit()
    return {"revoked_sessions": int(result.rowcount or 0)}


@router.get("/me", response_model=CurrentUserOut)
async def me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> CurrentUserOut:
    return await user_to_out(user, db)


@router.patch("/privacy-preferences", response_model=CurrentUserOut)
async def update_privacy_preferences(
    payload: PrivacyPreferencesIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> CurrentUserOut:
    user.generation_quality_improvement_consent = payload.generation_quality_improvement_consent
    user.generation_quality_improvement_consent_updated_at = utc_now()
    await db.commit()
    return await user_to_out(user, db)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    await revoke_current_session(request, response, db)
    return {"ok": True}

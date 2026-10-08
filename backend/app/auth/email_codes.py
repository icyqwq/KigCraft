import hashlib
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.email_delivery import VerificationEmail, get_email_delivery_provider
from app.auth.models import VerificationPurpose
from app.auth.send_rate_limit import enforce_verification_code_send_limits
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import EmailVerificationCode


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _new_code() -> str:
    if get_settings().email_provider == "console":
        return "123456"
    return f"{secrets.randbelow(1_000_000):06d}"


async def send_verification_code(
    session: AsyncSession,
    email: str,
    purpose: VerificationPurpose,
    client_ip: str,
    *,
    rate_limit_checked: bool = False,
) -> None:
    settings = get_settings()
    normalized_email = email.lower().strip()
    if not rate_limit_checked:
        await enforce_verification_code_send_limits(session, normalized_email, client_ip)
    code = _new_code()
    row = EmailVerificationCode(
        email=normalized_email,
        purpose=purpose,
        code_hash=_hash_code(code),
        expires_at=utc_now() + timedelta(minutes=settings.email_code_ttl_minutes),
        send_ip=client_ip,
    )
    session.add(row)
    try:
        await get_email_delivery_provider(settings).send_verification(
            VerificationEmail(email=row.email, code=code, purpose=purpose.value)
        )
    except Exception:
        await session.rollback()
        raise
    await session.commit()


async def verify_email_code(
    session: AsyncSession,
    email: str,
    purpose: VerificationPurpose,
    code: str,
) -> None:
    settings = get_settings()
    result = await session.execute(
        select(EmailVerificationCode)
        .where(EmailVerificationCode.email == email.lower().strip())
        .where(EmailVerificationCode.purpose == purpose)
        .where(EmailVerificationCode.consumed_at.is_(None))
        .order_by(EmailVerificationCode.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None or row.expires_at < utc_now():
        raise HTTPException(status_code=400, detail="verification_code_invalid")
    if row.attempt_count >= settings.email_code_max_attempts:
        raise HTTPException(status_code=400, detail="verification_code_invalid")
    row.attempt_count += 1
    if row.code_hash != _hash_code(code):
        await session.commit()
        raise HTTPException(status_code=400, detail="verification_code_invalid")
    row.consumed_at = utc_now()
    await session.commit()

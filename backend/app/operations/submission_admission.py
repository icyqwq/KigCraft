"""Account frequency admission, separate from wallet and runtime concurrency.

Call after authorization/validation and before mutation, in the submission transaction.
The existing business idempotency lookup must run first. Rollback consumes no slot.
"""
from datetime import timedelta
from hashlib import sha256
from math import ceil
from sqlalchemy import func, select, text
from app.core.config import get_settings
from app.design.models import SubmissionAdmission
from app.design.service import ApplicationError

class SubmissionRateLimited(ApplicationError):
    def __init__(self, retry_after: int):
        super().__init__("submission_rate_limited")
        self.retry_after = max(1, retry_after)

async def admit(db, owner: str, action: str, client_request_id: str):
    settings = get_settings()
    # A dedicated advisory namespace avoids coupling to wallet row-lock ordering.
    key = int.from_bytes(sha256(("v2-submit-rate:"+owner).encode()).digest()[:8],"big",signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"),{"key":key})
    now = await db.scalar(select(func.clock_timestamp()))
    existing = await db.scalar(select(SubmissionAdmission.id).where(
        SubmissionAdmission.owner_id==owner, SubmissionAdmission.action==action,
        SubmissionAdmission.client_request_id==client_request_id))
    if existing is not None:
        raise ApplicationError("idempotency_conflict")
    cutoff = now-timedelta(seconds=settings.v2_submit_rate_limit_window_seconds)
    recent = list((await db.scalars(select(SubmissionAdmission.created_at).where(
        SubmissionAdmission.owner_id==owner, SubmissionAdmission.created_at>cutoff)
        .order_by(SubmissionAdmission.created_at.desc())
        .limit(settings.v2_submit_rate_limit_max_requests))).all())
    if len(recent)>=settings.v2_submit_rate_limit_max_requests:
        retry_after = ceil((recent[-1]+timedelta(seconds=settings.v2_submit_rate_limit_window_seconds)-now).total_seconds())
        raise SubmissionRateLimited(retry_after)
    db.add(SubmissionAdmission(owner_id=owner,action=action,client_request_id=client_request_id,created_at=now))
    await db.flush()

from __future__ import annotations
import hashlib
import json
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.time import utc_now
from app.auth.models import UserRole
from app.core.config import get_settings
from app.db.models import User
from app.design.models import Branch, Design, DomainEvent


class ApplicationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def owned_design(db: AsyncSession, owner_id: str, design_id: str, *, lock=False) -> Design:
    query = select(Design).where(Design.id == design_id, Design.owner_id == owner_id, Design.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    design = (await db.execute(query)).scalar_one_or_none()
    if design is None:
        raise ApplicationError("not_found")
    return design


async def owned_branch(db: AsyncSession, owner_id: str, branch_id: str, *, lock=False) -> tuple[Design, Branch]:
    # Resolve the parent without locking; always acquire locks in design -> branch order.
    design_id = (await db.execute(select(Branch.design_id).where(Branch.id == branch_id))).scalar_one_or_none()
    if design_id is None:
        raise ApplicationError("not_found")
    design = await owned_design(db, owner_id, design_id, lock=lock)
    query = select(Branch).where(Branch.id == branch_id, Branch.design_id == design.id)
    if lock:
        query = query.with_for_update()
    branch = (await db.execute(query)).scalar_one()
    return design, branch


PUBLIC_EVENTS = {
    "session.updated", "message.created", "message.delta", "message.completed",
    "operation.state_changed", "question.opened", "question.resolved", "image.ready",
    "revision.current_changed", "draft.conflicted", "quota.changed", "viewset.updated",
}


async def event(db: AsyncSession, design_id: str, event_type: str, payload: dict) -> DomainEvent:
    if event_type not in PUBLIC_EVENTS:
        raise ValueError("Unknown business event")
    now = utc_now()
    changes = {"event_sequence":Design.event_sequence + 1, "updated_at":now}
    if event_type in {"session.updated", "message.created", "message.completed", "image.ready", "question.opened", "question.resolved"}:
        changes["last_activity_at"] = now
    sequence = (await db.execute(update(Design).where(Design.id == design_id).values(**changes)
                                .returning(Design.event_sequence))).scalar_one()
    item = DomainEvent(design_id=design_id, sequence=sequence, event_type=event_type, payload=payload)
    db.add(item)
    await db.flush()
    return item


async def create_design(db: AsyncSession, owner_id: str, client_request_id: str, title: str, *, enforce_limit: bool = True) -> Design:
    # Serialize only design creation for this owner; duplicate HTTP responses are harmless.
    user = (await db.execute(select(User).where(User.id == owner_id).with_for_update())).scalar_one_or_none()
    if user is None or user.is_banned:
        raise ApplicationError("not_found")
    if not title.strip() or len(title) > 200 or not 8 <= len(client_request_id) <= 100:
        raise ApplicationError("invalid_request")
    fingerprint = digest({"title": title})
    previous = (await db.execute(select(Design).where(
        Design.owner_id == owner_id, Design.client_request_id == client_request_id
    ))).scalar_one_or_none()
    if previous is not None:
        if previous.request_digest != fingerprint:
            raise ApplicationError("idempotency_conflict")
        return previous
    if enforce_limit:
        await ensure_design_room(db, user)
    design = Design(owner_id=owner_id, client_request_id=client_request_id, request_digest=fingerprint, title=title)
    db.add(design)
    await db.flush()
    branch = Branch(design_id=design.id)
    db.add(branch)
    await db.flush()
    design.last_branch_id = branch.id
    await event(db, design.id, "session.updated", {"design_id": design.id})
    return design


async def ensure_design_room(db: AsyncSession, user: User) -> None:
    """Regular accounts keep at most V2_DESIGN_LIMIT_PER_USER conversations; administrators are not limited.

    The caller holds the owner's row lock, so concurrent creations count each other."""
    limit = get_settings().v2_design_limit_per_user
    if not limit or user.role == UserRole.admin:
        return
    kept = await db.scalar(select(func.count()).select_from(Design).where(
        Design.owner_id == user.id, Design.deleted_at.is_(None), Design.archived_at.is_(None)))
    if kept >= limit:
        raise ApplicationError("design_limit_reached")


async def rename_design(db: AsyncSession, owner_id: str, design_id: str, title: str, expected_version: int) -> Design:
    design = await owned_design(db, owner_id, design_id, lock=True)
    if design.metadata_version != expected_version:
        raise ApplicationError("state_conflict")
    if not title.strip() or len(title) > 200:
        raise ApplicationError("invalid_request")
    design.title = title
    design.metadata_version += 1
    await event(db, design.id, "session.updated", {"design_id": design.id})
    return design

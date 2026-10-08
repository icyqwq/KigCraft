"""Read-only continuation preparation. Opening a viewer or a draft must not create a branch."""
from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.conversation.checkpoints import ContextBoundary, resolve_boundary
from app.design.models import Revision
from app.design.service import ApplicationError, owned_branch

@dataclass(frozen=True)
class ContinuationPlan:
    design_id: str
    source_branch_id: str
    revision_id: str
    clean_asset_id: str
    boundary: ContextBoundary


async def continuation_plan(db: AsyncSession, owner: str, branch_id: str, revision_id: str,
                            expected_branch_version: int) -> ContinuationPlan:
    design, branch = await owned_branch(db, owner, branch_id)
    if branch.state_version != expected_branch_version:
        raise ApplicationError("state_conflict")
    if design.archived_at:
        raise ApplicationError("design_archived")
    revision = (await db.execute(select(Revision).where(
        Revision.id == revision_id, Revision.design_id == design.id
    ))).scalar_one_or_none()
    if revision is None:
        raise ApplicationError("not_found")
    boundary = await resolve_boundary(db, design.id, revision.context_checkpoint)
    return ContinuationPlan(design.id, branch.id, revision.id, revision.clean_asset_id, boundary)

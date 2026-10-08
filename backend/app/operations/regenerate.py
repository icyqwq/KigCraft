"""Resolve exact original conditions; never substitute the selected result as the input base."""
from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.contracts.v2 import InputSnapshot
from app.conversation.checkpoints import ContextBoundary, resolve_boundary
from app.design.models import InputRecord, Operation, Revision
from app.design.service import ApplicationError, owned_design

@dataclass(frozen=True)
class RegenerationPlan:
    design_id: str
    source_operation_id: str
    input_snapshot_id: str
    snapshot: InputSnapshot
    boundary: ContextBoundary


async def regeneration_plan(db: AsyncSession, owner: str, revision_id: str) -> RegenerationPlan:
    revision = await db.get(Revision, revision_id)
    if revision is None:
        raise ApplicationError("not_found")
    design = await owned_design(db, owner, revision.design_id)
    if design.archived_at:
        raise ApplicationError("design_archived")
    if revision.source_kind != "ai" or not revision.operation_id:
        raise ApplicationError("original_request_unavailable")
    operation = await db.get(Operation, revision.operation_id)
    if operation is None or operation.design_id != design.id or operation.owner_id != owner:
        raise ApplicationError("not_found")
    if operation.upstream_status not in {"completed", "failed", "interrupted"}:
        raise ApplicationError("history_checkpoint_unconfirmed")
    record = await db.get(InputRecord, operation.input_snapshot_id)
    if record is None:
        raise ApplicationError("input_snapshot_missing")
    snapshot = InputSnapshot.model_validate(record.snapshot)
    if snapshot.digest() != record.digest:
        raise ApplicationError("input_snapshot_changed")
    boundary = await resolve_boundary(db, design.id, operation.context_before)
    return RegenerationPlan(design.id, operation.id, record.id, snapshot, boundary)

"""Business-owned immutable historical boundaries; clients never supply Codex identities."""
from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.codex_runtime.launch import DESIGN_MODEL
from app.design.models import Branch, CodexEpoch, Operation
from app.design.service import ApplicationError

@dataclass(frozen=True)
class ContextBoundary:
    epoch_id: str | None
    thread_id: str | None
    turn_id: str | None
    runtime_identity: str | None
    codex_version: str | None


def checkpoint(epoch: CodexEpoch, turn_id: str | None) -> dict:
    return {"kind": "turn", "epoch_id": epoch.id, "turn_id": turn_id} if turn_id else {"kind": "fresh"}


async def resolve_boundary(db: AsyncSession, design_id: str, value: dict | None) -> ContextBoundary:
    if value == {"kind": "fresh"}:
        return ContextBoundary(None, None, None, None, None)
    if (not isinstance(value, dict) or set(value) != {"kind", "epoch_id", "turn_id"}
            or value.get("kind") != "turn" or not value.get("turn_id")):
        raise ApplicationError("history_checkpoint_unavailable")
    epoch = (await db.execute(select(CodexEpoch).join(Branch, Branch.id == CodexEpoch.branch_id).where(
        CodexEpoch.id == value["epoch_id"], Branch.design_id == design_id
    ))).scalar_one_or_none()
    if epoch is None or epoch.model != DESIGN_MODEL:
        raise ApplicationError("history_checkpoint_unavailable")
    source = (await db.execute(select(Operation).where(
        Operation.design_id == design_id, Operation.upstream_thread_id == epoch.thread_id,
        Operation.upstream_turn_id == value["turn_id"],
        Operation.upstream_status.in_(["completed", "failed", "interrupted"]),
    ))).scalars().all()
    if len(source) != 1:
        raise ApplicationError("history_checkpoint_unconfirmed")
    return ContextBoundary(epoch.id, epoch.thread_id, value["turn_id"], epoch.runtime_identity, epoch.codex_version)

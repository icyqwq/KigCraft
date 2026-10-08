"""Present V2 operations through the V1 admin panel's job model.

The panel, trend charts and reliability maths all consume StoredJob. Rather than fork them, every V2 operation is
projected onto that shape here, read-only. Image links point at the admin-only asset route below.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.design.models import Asset, InputRecord, Operation, Revision, RuntimeObservation
from app.generation.job_repository import StoredEvent, StoredJob, StoredOutput
from app.generation.usage import TokenUsage

PROVIDER = "codex"
# V2 operation state -> the status vocabulary the panel already understands.
STATUS = {
    "queued": "queued", "reviewing": "preparing_references", "running": "codex_generating",
    "reconciling": "saving_outputs", "publishing": "saving_outputs", "cancelling": "running",
    "succeeded": "succeeded", "partial_success": "succeeded", "awaiting_input": "succeeded",
    "failed": "failed", "cancelled": "cancelled",
}
PHASE = {
    "queued": "排队中", "reviewing": "审核输入", "running": "正在创作", "reconciling": "核对结果",
    "publishing": "保存图片", "cancelling": "正在停止", "succeeded": "已完成", "partial_success": "部分完成",
    "awaiting_input": "等待补充", "failed": "生成未完成", "cancelled": "已取消",
}
TERMINAL = {"succeeded", "partial_success", "awaiting_input", "failed", "cancelled"}
CHUNK = 500


def asset_url(asset_id: str) -> str:
    return f"/api/audit/v2-assets/{asset_id}"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _chunks(values: list[str]):
    for start in range(0, len(values), CHUNK):
        yield values[start:start + CHUNK]


def _usage(snapshot: dict[str, Any] | None) -> TokenUsage | None:
    total = (snapshot or {}).get("total")
    if not isinstance(total, dict):
        return None
    return TokenUsage(
        input_tokens=total.get("inputTokens"), cached_input_tokens=total.get("cachedInputTokens"),
        output_tokens=total.get("outputTokens"), reasoning_output_tokens=total.get("reasoningOutputTokens"),
        total_tokens=total.get("totalTokens"),
    )


async def v2_jobs(session: AsyncSession, *, owner_id: str | None = None) -> list[StoredJob]:
    statement = select(Operation).order_by(Operation.created_at)
    if owner_id:
        statement = statement.where(Operation.owner_id == owner_id)
    operations = list((await session.execute(statement)).scalars())
    if not operations:
        return []
    ids = [item.id for item in operations]
    snapshots: dict[str, dict[str, Any]] = {}
    for part in _chunks([item.input_snapshot_id for item in operations]):
        for row in (await session.execute(select(InputRecord.id, InputRecord.snapshot).where(InputRecord.id.in_(part)))).all():
            snapshots[row[0]] = row[1] or {}
    usage: dict[str, TokenUsage | None] = {}
    for part in _chunks(ids):
        rows = (await session.execute(select(RuntimeObservation).where(
            RuntimeObservation.operation_id.in_(part), RuntimeObservation.phase == "generation",
        ).order_by(RuntimeObservation.fencing_token))).scalars()
        for row in rows:
            if row.token_status == "observed":
                usage[row.operation_id] = _usage(row.token_snapshot)
    outputs: dict[str, list[tuple[Revision, Asset]]] = {}
    for part in _chunks(ids):
        rows = (await session.execute(select(Revision, Asset).join(Asset, Asset.id == Revision.clean_asset_id).where(
            Revision.operation_id.in_(part)).order_by(Revision.history_sequence))).all()
        for revision, asset in rows:
            outputs.setdefault(revision.operation_id, []).append((revision, asset))
    jobs: list[StoredJob] = []
    for item in operations:
        snapshot = snapshots.get(item.input_snapshot_id, {})
        references = [ref for ref in snapshot.get("references", []) if isinstance(ref, dict) and ref.get("asset_id")]
        created, updated = _iso(item.created_at), _iso(item.updated_at) or _iso(item.created_at)
        status = STATUS.get(item.state, "running")
        events = [StoredEvent(sequence=1, type="created", progress=0, message="", created_at=created or "")]
        if updated and updated != created:
            kind = "failed" if status == "failed" else "updated"
            events.append(StoredEvent(sequence=2, type=kind, progress=100 if item.state in TERMINAL else 50,
                                      message=item.error_code or "", created_at=updated))
        jobs.append(StoredJob(
            id=item.id, character_session_id=item.owner_id, generation_mode=item.kind,
            expected_output_count=1, status=status, progress=100 if item.state in TERMINAL else 50,
            queue_position=None, phase_label=PHASE.get(item.state, item.state), provider=PROVIDER,
            prompt_payload={"user_requirements": snapshot.get("user_text") or "", "v2_references": references},
            created_at=created, updated_at=updated, user_id=item.owner_id,
            token_usage=usage.get(item.id),
            outputs=[StoredOutput(index=index, object_key=asset.object_key, image_url=asset_url(asset.id),
                                  width=asset.width, height=asset.height)
                     for index, (_revision, asset) in enumerate(outputs.get(item.id, []), 1)],
            events=events,
        ))
    return jobs


async def v2_job_counts_by_user(session: AsyncSession, user_ids: list[str] | None = None) -> dict[str, int]:
    from sqlalchemy import func
    statement = select(Operation.owner_id, func.count(Operation.id)).group_by(Operation.owner_id)
    if user_ids is not None:
        statement = statement.where(Operation.owner_id.in_(user_ids))
    return {str(owner): int(count) for owner, count in (await session.execute(statement)).all()}


async def admin_jobs(session: AsyncSession) -> list[StoredJob]:
    """V2 operations when V2 is enabled; the legacy store otherwise (V1 tasks are not mixed in once V2 is live)."""
    from app.core.config import get_settings
    if get_settings().v2_enabled:
        return await v2_jobs(session)
    from app.generation.job_store import job_store
    return await job_store.list()

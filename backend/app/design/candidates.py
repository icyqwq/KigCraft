"""CandidateGroup is a read projection of immutable input and recorded context."""
from dataclasses import dataclass
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from app.contracts.v2 import InputSnapshot
from app.design.history import decode_cursor, encode_cursor
from app.design.models import InputRecord, Operation, Revision
from app.design.service import ApplicationError, digest, owned_design


@dataclass
class CandidatePage:
    group: dict | None
    items: list[Revision]
    next_cursor: str | None
    watermark: int
    total: int


def recorded_boundary(value):
    if value == {"kind": "fresh"}:
        return value
    if (isinstance(value, dict) and set(value) == {"kind", "epoch_id", "turn_id"}
            and value["kind"] == "turn"
            and all(isinstance(value[k], str) and value[k] for k in ("epoch_id", "turn_id"))):
        return value
    return None


async def candidates(db, owner, revision_id, *, cursor=None, limit=50):
    if not 1 <= limit <= 100:
        raise ApplicationError("invalid_history_cursor")
    selected = await db.get(Revision, revision_id)
    if selected is None:
        raise ApplicationError("not_found")
    design = await owned_design(db, owner, selected.design_id, lock=True)
    operation = await db.get(Operation, selected.operation_id) if selected.operation_id else None
    record = await db.get(InputRecord, operation.input_snapshot_id) if operation else None
    boundary = recorded_boundary(operation.context_before) if operation else None
    kind = None
    if (record and boundary and selected.source_kind == "ai"
            and operation.design_id == design.id and operation.owner_id == owner):
        try:
            inputs = InputSnapshot.model_validate(record.snapshot)
            if inputs.digest() == record.digest:
                expected = "local_edit" if inputs.local_edit_mode else "image_edit" if inputs.base_revision_id else "generate"
                if operation.kind in {expected, "regenerate"}:
                    kind = expected
        except ValueError:
            pass
    if kind is None:
        if cursor:
            raise ApplicationError("invalid_history_cursor")
        # A legacy image can still be viewed; unknown input/context never forms a group.
        return CandidatePage(None, [selected], None, design.history_counter, 1)
    group_id = digest({"schema_version": 1, "design_id": design.id, "input_snapshot_id": record.id,
                       "context_checkpoint": boundary, "requested_operation_kind": kind, "view": selected.view})
    scope = "candidates:" + group_id
    watermark, before = (decode_cursor(cursor, selected.id, scope, design.history_counter) if cursor
                         else (design.history_counter, design.history_counter + 1))
    query = select(Revision).join(Operation, Revision.operation_id == Operation.id).where(
        Revision.design_id == design.id, Revision.source_kind == "ai", Revision.view == selected.view,
        Operation.design_id == design.id, Operation.owner_id == owner,
        Operation.input_snapshot_id == record.id, Operation.kind.in_([kind, "regenerate"]),
        cast(Operation.context_before, JSONB) == boundary,
        Revision.history_sequence <= watermark)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = list((await db.scalars(query.where(Revision.history_sequence < before)
                .order_by(Revision.history_sequence.desc()).limit(limit + 1))).all())
    items = rows[:limit]
    next_cursor = encode_cursor(selected.id, scope, watermark, items[-1].history_sequence) if len(rows) > limit else None
    return CandidatePage({"id": group_id, "requested_operation_kind": kind}, items, next_cursor, watermark, total)

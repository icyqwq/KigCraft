"""Read-only history windows. Cursor scope is not authorization."""
from __future__ import annotations
import base64
import binascii
import json
from dataclasses import dataclass
from sqlalchemy import and_, func, literal, or_, select, union_all
from app.design.models import Branch, Design, Message, Revision
from app.design.service import ApplicationError


async def fork_history_sequence(db, origin: Revision, *, regenerate=False) -> int:
    if regenerate:
        sequence = await db.scalar(select(func.min(Message.history_sequence)).where(
            Message.operation_id == origin.operation_id, Message.branch_id == origin.branch_id,
            Message.role == "user"))
        if sequence is None:
            raise ApplicationError("history_checkpoint_unavailable")
        return sequence - 1
    # A generated image may precede its final assistant message within the same turn.
    sequence = await db.scalar(select(func.max(Message.history_sequence)).where(
        Message.operation_id == origin.operation_id, Message.branch_id == origin.branch_id)
    ) if origin.operation_id else None
    return max(origin.history_sequence, sequence or 0)


@dataclass
class HistoryPage:
    records: list[tuple[str, Message | Revision]]
    next_cursor: str | None
    watermark: int
    ancestry_incomplete: bool


def encode_cursor(branch: str, kind: str, watermark: int, before: int) -> str:
    return base64.urlsafe_b64encode(json.dumps(
        [1, branch, kind, watermark, before], separators=(",", ":")).encode()).decode().rstrip("=")


def decode_cursor(value: str, branch: str, kind: str, maximum: int) -> tuple[int, int]:
    try:
        if not 1 <= len(value) <= 512:
            raise ValueError()
        raw = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if (not isinstance(raw, list) or len(raw) != 5 or raw[:3] != [1, branch, kind]
                or type(raw[0]) is not int or type(raw[3]) is not int or type(raw[4]) is not int
                or not 0 <= raw[3] <= maximum or not 1 <= raw[4] <= raw[3] + 1):
            raise ValueError()
        return raw[3], raw[4]
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise ApplicationError("invalid_history_cursor") from None


async def history_page(db, design: Design, branch: Branch, *, cursor: str | None = None,
                       limit: int = 50, kind: str = "all", message_id: str | None = None) -> HistoryPage:
    if not 1 <= limit <= 100 or kind not in {"all", "message", "revision"}:
        raise ApplicationError("invalid_history_cursor")
    # Read the server counter explicitly: an ORM Design loaded before an INSERT
    # can still carry the pre-trigger value in the same transaction.
    maximum = await db.scalar(select(Design.history_counter).where(Design.id == design.id))
    watermark, before = decode_cursor(cursor, branch.id, kind, maximum) if cursor else (maximum, maximum + 1)
    caps, incomplete = await visibility_caps(db, design.id, branch, watermark)
    if message_id and not cursor:
        target = await db.get(Message, message_id)
        if (target is None or target.role not in {"user","assistant"} or target.branch_id not in caps
                or target.history_sequence > caps[target.branch_id]):
            raise ApplicationError("not_found")
        before = target.history_sequence + 1
    queries = []
    for entry_kind, model in (("message", Message), ("revision", Revision)):
        if kind not in {"all", entry_kind}:
            continue
        visibility = or_(*(and_(model.branch_id == branch_id, model.history_sequence <= end)
                           for branch_id, end in caps.items()))
        public = model.role.in_(["user", "assistant"]) if entry_kind == "message" else True
        queries.append(select(literal(entry_kind).label("kind"), model.id.label("id"),
                              model.history_sequence.label("sequence")).where(
            visibility, public, model.history_sequence < before))
    entries = union_all(*queries).subquery()
    rows = (await db.execute(select(entries).order_by(entries.c.sequence.desc()).limit(limit + 1))).all()
    selected = rows[:limit]
    by_id = {}
    for entry_kind, model in (("message", Message), ("revision", Revision)):
        ids = [r.id for r in selected if r.kind == entry_kind]
        if ids:
            by_id.update({(entry_kind, r.id): r for r in (await db.scalars(select(model).where(model.id.in_(ids)))).all()})
    next_cursor = encode_cursor(branch.id, kind, watermark, selected[-1].sequence) if len(rows) > limit else None
    return HistoryPage([(r.kind, by_id[r.kind, r.id]) for r in reversed(selected)],
                       next_cursor, watermark, incomplete)


async def design_revision_page(db, design: Design, *, cursor: str | None = None, limit: int = 50):
    """All schemes within an already authorized, locked design; no ancestry filtering."""
    if not 1 <= limit <= 100:
        raise ApplicationError("invalid_history_cursor")
    maximum = await db.scalar(select(Design.history_counter).where(Design.id == design.id))
    kind = "design-revision"
    watermark, before = decode_cursor(cursor, design.id, kind, maximum) if cursor else (maximum, maximum + 1)
    visible = (Revision.design_id == design.id, Revision.history_sequence <= watermark)
    rows = list((await db.scalars(select(Revision).where(
        *visible, Revision.history_sequence < before
    ).order_by(Revision.history_sequence.desc()).limit(limit + 1))).all())
    total = await db.scalar(select(func.count()).select_from(Revision).where(*visible))
    selected = rows[:limit]
    next_cursor = encode_cursor(design.id, kind, watermark, selected[-1].history_sequence) if len(rows) > limit else None
    return HistoryPage([("revision", item) for item in reversed(selected)], next_cursor, watermark, False), total


async def visibility_caps(db, design_id, branch, maximum):
    branches = {b.id:b for b in (await db.scalars(select(Branch).where(Branch.design_id == design_id))).all()}
    caps, current, cap, incomplete = {}, branch, maximum, False
    while current:
        if current.id in caps:
            raise ApplicationError("history_checkpoint_unavailable")
        caps[current.id] = cap
        if not current.parent_branch_id:
            break
        if current.history_parent_sequence is None or current.parent_branch_id not in branches:
            incomplete = True
            break
        cap = min(cap, current.history_parent_sequence)
        current = branches[current.parent_branch_id]
    return caps, incomplete

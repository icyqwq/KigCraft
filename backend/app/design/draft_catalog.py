"""Owner-scoped editor draft summaries; listing never downloads draft documents."""
import base64
import binascii
import json
from sqlalchemy import select, func
from app.design.models import EditorDraft, Design, Revision
from app.design.service import ApplicationError

def cursor_for(owner, row):
    return base64.urlsafe_b64encode(json.dumps(
        [1, owner, row.id], separators=(",", ":")
    ).encode()).decode().rstrip("=")

def read_cursor(cursor, owner):
    try:
        if not 1 <= len(cursor) <= 512:
            raise ValueError()
        parts = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if (not isinstance(parts, list) or len(parts) != 3 or type(parts[0]) is not int
                or parts[:2] != [1, owner] or not isinstance(parts[2], str) or not 1 <= len(parts[2]) <= 36):
            raise ValueError()
        return parts[2]
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise ApplicationError("invalid_draft_cursor") from None

async def list_account_drafts(db, owner, *, cursor=None, limit=30):
    if not 1 <= limit <= 100:
        raise ApplicationError("invalid_draft_cursor")
    statement = select(
        EditorDraft.id, EditorDraft.version, EditorDraft.design_id, EditorDraft.branch_id,
        EditorDraft.base_revision_id, EditorDraft.root_asset_id,
        EditorDraft.updated_at, Design.title, Design.archived_at,
        Revision.clean_asset_id,
        func.left(EditorDraft.document["content"]["localEditNote"].as_string(), 120).label("note"),
    ).join(Design, Design.id == EditorDraft.design_id).join(Revision, Revision.id == EditorDraft.base_revision_id).where(
        EditorDraft.owner_id == owner, Design.owner_id == owner,
        Design.deleted_at.is_(None), EditorDraft.deleted_at.is_(None),
    )
    if cursor:
        statement = statement.where(EditorDraft.id < read_cursor(cursor, owner))
    rows = (await db.execute(statement.order_by(EditorDraft.id.desc()).limit(limit + 1))).all()
    selected = rows[:limit]
    return {"items": [{
        "id": row.id, "version": row.version, "design_id": row.design_id,
        "design_title": row.title, "branch_id": row.branch_id,
        "base_revision_id": row.base_revision_id, "updated_at": row.updated_at,
        "archived": row.archived_at is not None, "note": row.note or "",
        "image_url": f"/api/v2/assets/{row.clean_asset_id}",
    } for row in selected], "next_cursor": cursor_for(owner, selected[-1]) if len(rows) > limit else None}

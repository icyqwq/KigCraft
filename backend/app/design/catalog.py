"""Owner-scoped design catalog with explicit invalidation of moved pages."""
import base64
import binascii
import json
from datetime import datetime
from sqlalchemy import and_, exists, or_, select, text
from app.design.models import Asset, Branch, Design, EditorDraft, Message, Operation, Revision
from app.design.search_text import excerpt
from app.design.service import ApplicationError, digest


async def list_designs(db, owner, *, q="", status="active", cursor=None, limit=30):
    if status not in {"active","archived","trash"} or not 1 <= limit <= 100:
        raise ApplicationError("invalid_design_cursor")
    q=q.strip()
    scope=digest([owner,q,status])
    async def version():
        # Sequence allocation is not commit order: hash every owned row's generation.
        # A later commit with a lower sequence must still invalidate the cursor.
        return await db.scalar(text("""SELECT md5(COALESCE(string_agg(
            id || ':' || list_sequence::text || ':' || CASE WHEN content_purged_at IS NULL THEN '0' ELSE '1' END, ',' ORDER BY id),'')) FROM v2_designs WHERE owner_id=:owner"""),
            {"owner":owner})
    stamp=await version()
    after=None
    if cursor:
        try:
            raw=json.loads(base64.b64decode(cursor+"="*(-len(cursor)%4),altchars=b"-_",validate=True))
            if (len(cursor)>1024 or not isinstance(raw,list) or len(raw)!=6 or raw[:2]!=[1,scope]
                    or type(raw[0]) is not int or not isinstance(raw[2],str) or len(raw[2])!=32 or type(raw[3]) is not bool
                    or not isinstance(raw[4],str) or not isinstance(raw[5],str) or len(raw[5])>36):
                raise ValueError()
            after=(raw[3],datetime.fromisoformat(raw[4]),raw[5])
            if after[1].tzinfo is None:
                raise ValueError()
        except (ValueError,TypeError,UnicodeError,binascii.Error):
            raise ApplicationError("invalid_design_cursor") from None
        if raw[2]!=stamp:
            raise ApplicationError("design_list_changed")
    match=select(Message.id).join(Branch,Message.branch_id==Branch.id).where(
        Branch.design_id==Design.id,Message.role.in_(["user","assistant"]),Message.search_text.icontains(q,autoescape=True))
    query=select(Design).where(Design.owner_id==owner,Design.content_purged_at.is_(None))
    # "trash" is internal now (retired or closed-account designs for maintenance); the API only offers active and archived.
    if status=="trash":
        query=query.where(Design.deleted_at.is_not(None))
    else:
        query=query.where(Design.deleted_at.is_(None),
            Design.archived_at.is_(None) if status=="active" else Design.archived_at.is_not(None))
    if q:
        query=query.where(or_(Design.title.icontains(q,autoescape=True),exists(match)))
    if after:
        pin,date,key=after
        query=query.where(or_(and_(Design.pinned.is_(False),pin),
            and_(Design.pinned==pin,Design.last_activity_at<date),
            and_(Design.pinned==pin,Design.last_activity_at==date,Design.id>key)))
    designs=list((await db.scalars(query.order_by(Design.pinned.desc(),Design.last_activity_at.desc(),Design.id).limit(limit+1))).all())
    selected=designs[:limit]
    ids=[d.id for d in selected]
    images=dict((await db.execute(select(Branch.design_id,Revision.clean_asset_id).join(
        Revision,Revision.id==Branch.current_revision_id).join(Asset,Asset.id==Revision.clean_asset_id).join(Design,Design.last_branch_id==Branch.id).where(Design.id.in_(ids),Asset.status=="ready"))).all())
    ops={op.design_id:op.state for op in (await db.scalars(select(Operation).where(Operation.design_id.in_(ids))
        .distinct(Operation.design_id).order_by(Operation.design_id,Operation.created_at.desc(),Operation.id.desc()))).all()}
    drafts=set((await db.scalars(select(EditorDraft.design_id).where(EditorDraft.design_id.in_(ids),EditorDraft.deleted_at.is_(None)))).all())
    matches={}
    if q and ids:
        rows=(await db.execute(select(Branch.design_id,Message).join(Message,Message.branch_id==Branch.id)
            .where(Branch.design_id.in_(ids),Message.role.in_(["user","assistant"]),Message.search_text.icontains(q,autoescape=True))
            .distinct(Branch.design_id).order_by(Branch.design_id,Message.history_sequence.desc()))).all()
        matches={design_id:{"message_id":m.id,"branch_id":m.branch_id,"text":excerpt(m.search_text,q)} for design_id,m in rows}
    if await version()!=stamp:
        raise ApplicationError("design_list_changed")
    next_cursor=None
    if len(designs)>limit:
        last=selected[-1]
        next_cursor=base64.urlsafe_b64encode(json.dumps([1,scope,stamp,last.pinned,last.last_activity_at.isoformat(),last.id],
                                            separators=(",",":")).encode()).decode().rstrip("=")
    return selected, {d.id:{"thumbnail_url":"/api/v2/assets/"+images[d.id] if d.id in images else None,
        "operation_state":ops.get(d.id),"has_cloud_draft":d.id in drafts,"match":matches.get(d.id)} for d in selected}, next_cursor

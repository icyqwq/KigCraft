"""Post-response, bounded private-resource rejection counters; no resource identifiers stored."""
import asyncio
from datetime import timedelta
from sqlalchemy import BigInteger, select, func, text
from sqlalchemy.dialects.postgresql import insert
from app.core.time import utc_now
from app.db.models import AuditCounter, User, AlbumItem
from app.db.session import get_sessionmaker
from app.design.models import Asset, Branch, Design, EditorDraft, LegacyMaterial, Message, Operation, Revision, ViewSet, Quote, EditorCheckpoint

PREFIX = "v2-access:"


def resource_queries(route, params):
    route = route.removeprefix("/api")
    if not route.startswith("/v2/"):
        from app.admin.access_legacy import queries
        return queries(route, params)
    queries = []
    direct = {"design_id":Design, "asset_id":Asset, "operation_id":Operation,
              "draft_id":EditorDraft, "material_id":LegacyMaterial}
    for name, model in direct.items():
        value = params.get(name)
        if value is not None and len(str(value)) <= 100:
            queries.append(select(model.owner_id).where(model.id==str(value)))
    for name, model in (("branch_id",Branch),("revision_id",Revision)):
        value = params.get(name)
        if value is not None and len(str(value)) <= 100:
            queries.append(select(Design.owner_id).join(model,model.design_id==Design.id).where(model.id==str(value)))
    value = params.get("message_id")
    if value is not None and len(str(value)) <= 100:
        queries.append(select(Design.owner_id).join(Branch,Branch.design_id==Design.id)
                       .join(Message,Message.branch_id==Branch.id).where(Message.id==str(value)))
    value = params.get("identity")
    if route.startswith("/v2/viewsets/") and value is not None and len(str(value)) <= 100:
        queries.append(select(ViewSet.owner_id).where(ViewSet.id==str(value)))
    return queries


def reference_queries(references):
    direct = {"album":AlbumItem, "asset":Asset, "design":Design, "operation":Operation, "quote":Quote,
              "draft":EditorDraft, "checkpoint":EditorCheckpoint, "viewset":ViewSet}
    grouped = {}
    for kind, identity in references:
        grouped.setdefault(kind, set()).add(identity)
    queries = []
    for kind, identities in grouped.items():
        if kind in direct:
            model = direct[kind]
            owner = model.user_id if kind == "album" else model.owner_id
            queries.append(select(owner).where(model.id.in_(identities)))
        elif kind in {"branch", "revision"}:
            model = Branch if kind == "branch" else Revision
            queries.append(select(Design.owner_id).join(model, model.design_id == Design.id).where(model.id.in_(identities)))
        elif kind == "message":
            queries.append(select(Design.owner_id).join(Branch, Branch.design_id == Design.id)
                .join(Message, Message.branch_id == Branch.id).where(Message.id.in_(identities)))
        elif kind == "account":
            queries.append(select(User.id).where(User.id.in_(identities)))
    return queries


async def increment(db, key):
    statement = insert(AuditCounter).values(key=key,value=1,updated_at=utc_now())
    await db.execute(statement.on_conflict_do_update(index_elements=[AuditCounter.key],
        set_={"value":func.least(AuditCounter.value.cast(BigInteger)+1,2147483647),"updated_at":utc_now()}))


async def record_rejection(actor, route, params, *, factory=None, now=None, collector=None, references=(), complete=True, outcome=None, stream=False, authenticated=True):
    if outcome not in {None, "rejected", "unauthenticated", "forbidden"}:
        raise ValueError("invalid_observation_category")
    queries = (resource_queries(route,params) + reference_queries(references)) if actor and authenticated else []
    if outcome is None and (not actor or (not queries and complete)):
        return
    async def persist():
        if not complete:
            raise ValueError("incomplete_reference_observation")
        async with (factory or get_sessionmaker()).begin() as db:
            await db.execute(text("SET LOCAL statement_timeout = '500ms'"))
            await db.execute(text("SET LOCAL lock_timeout = '200ms'"))
            foreign = False
            for query in queries:
                foreign |= bool(await db.scalar(select(query.where(query.selected_columns[0] != actor).exists())))
            bucket = (now or utc_now()).strftime("%Y%m%dT%H%M")
            category = "cross" if foreign else "miss" if queries else (outcome or "rejected")
            await increment(db,PREFIX+bucket+":"+category)
            if outcome == "forbidden" and category != "forbidden":
                await increment(db, PREFIX+bucket+":forbidden")
            if stream:
                await increment(db,PREFIX+bucket+":stream_closed")
            # Count completed observations; fleet health is maintained separately.
            await increment(db,PREFIX+"observed")
    try:
        await asyncio.wait_for(persist(),timeout=2)
    except asyncio.CancelledError:
        if collector is not None:
            collector.failed(now=now)
        raise
    except Exception:
        # Never attach SQL errors, parameters or credentials to the already-sent response or logs.
        if collector is not None:
            collector.failed(now=now)
            await collector.pulse(now=now)


async def recent_denials(db, settings, *, now=None):
    now = now or utc_now()
    start = (now-timedelta(seconds=settings.v2_alert_recent_seconds)).replace(second=0,microsecond=0)
    first=PREFIX+start.strftime("%Y%m%dT%H%M")
    last=PREFIX+now.strftime("%Y%m%dT%H%M")+":z"
    category = func.split_part(AuditCounter.key, ":", 3)
    rows = (await db.execute(select(category, func.sum(AuditCounter.value)).where(
        AuditCounter.key>=first,AuditCounter.key<=last).group_by(category))).all()
    categories = {key: 0 for key in ("cross", "miss", "unauthenticated", "forbidden", "rejected", "stream_closed")}
    categories.update({key: int(value) for key, value in rows if key in categories})
    count = categories["cross"]
    from app.admin.access_collector import collection_health
    coverage = await collection_health(db, settings, now=now)
    return {"count":int(count) if coverage["status"] == "complete" else None,
            "recorded_count":int(count),"window_start":start,"coverage":coverage,"categories":categories}

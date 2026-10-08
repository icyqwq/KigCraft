"""Persist group authorization and schedule one independent view attempt at a time."""
from datetime import timedelta
from sqlalchemy import select
from app.auth.models import UserRole
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import User
from app.contracts.viewsets import ViewSetSnapshot
from app.design.models import Branch, InputRecord, Message, Operation, Quote, Revision, ViewSet, ViewSetItem, ViewSetAttempt
from app.design.service import ApplicationError, digest, event, owned_design
from app.design.viewsets import freeze_viewset
from app.operations.viewset import generation_snapshot
from app.admin.rollout import require_admission
from app.quota.viewsets import binding, hold, release_hold

GROUP_ACTIVE = {"running","reconciling","publishing","cancelling"}


async def owned_group(db, owner, identity):
    row = await db.scalar(select(ViewSet).where(ViewSet.id == identity, ViewSet.owner_id == owner))
    if row is None:
        raise ApplicationError("not_found")
    await owned_design(db, owner, row.design_id, lock=True)
    return await db.scalar(select(ViewSet).where(ViewSet.id == identity).with_for_update().execution_options(populate_existing=True))


async def items(db, group):
    return list((await db.scalars(select(ViewSetItem).where(ViewSetItem.viewset_id == group.id)
        .order_by(ViewSetItem.ordinal))).all())


def frozen(group):
    result = ViewSetSnapshot.model_validate(group.snapshot)
    if result.digest() != group.snapshot_digest:
        raise ApplicationError("input_snapshot_changed")
    return result


async def ensure_idle(db, design_id, *, own_group=None):
    from app.operations.service import ACTIVE
    if await db.scalar(select(Operation.id).where(Operation.design_id == design_id, Operation.state.in_(ACTIVE)).limit(1)):
        raise ApplicationError("design_busy")
    if await db.scalar(select(ViewSet.id).where(ViewSet.design_id == design_id, ViewSet.state.in_(GROUP_ACTIVE),
            ViewSet.id != own_group if own_group else True).limit(1)):
        raise ApplicationError("design_busy")


async def create_quote(db, owner, branch_id, request):
    from app.design.service import owned_branch
    design, _ = await owned_branch(db, owner, branch_id, lock=True)
    await require_admission(db, owner, design.id)
    snapshot = await freeze_viewset(db,owner,branch_id,source_revision_id=request.source_revision_id,
        expected_branch_version=request.expected_branch_version,options=request.options)
    user = await db.get(User,owner)
    units = 0 if user.role == UserRole.admin else 1
    quote = Quote(owner_id=owner,scope_digest=snapshot.digest(),operation_kind="viewset",
        max_units=units*len(snapshot.generated_views),
        deliverables={view:units for view in snapshot.generated_views},policy_version="legacy-one-image-v1",
        expires_at=utc_now()+timedelta(minutes=5))
    db.add(quote);await db.flush()
    return quote


async def submit_group(db, owner, branch_id, request):
    # Design first, then group/item, operation and wallet throughout this module.
    from app.design.service import owned_branch
    design, branch = await owned_branch(db,owner,branch_id,lock=True)
    fingerprint = digest({"branch_id":branch_id,"request":request.model_dump(mode="json")})
    previous = await db.scalar(select(ViewSet).where(ViewSet.owner_id==owner,ViewSet.client_request_id==request.client_request_id))
    if previous:
        if previous.request_digest != fingerprint:raise ApplicationError("idempotency_conflict")
        return previous
    from app.admin.v2_controls import gate
    await gate(db,owner)
    await require_admission(db, owner, design.id)
    snapshot = await freeze_viewset(db,owner,branch_id,source_revision_id=request.source_revision_id,
        expected_branch_version=request.expected_branch_version,options=request.options)
    await ensure_idle(db,design.id)
    quote = await db.scalar(select(Quote).where(Quote.id==request.quote_id,Quote.owner_id==owner))
    if (quote is None or quote.operation_kind!="viewset" or quote.scope_digest!=snapshot.digest()
            or quote.expires_at<=utc_now() or set(quote.deliverables)!=set(snapshot.generated_views)
            or sum(quote.deliverables.values())!=quote.max_units):
        raise ApplicationError("quote_invalid")
    if await db.scalar(select(ViewSet.id).where(ViewSet.quote_id==quote.id)):
        raise ApplicationError("quote_already_used")
    from app.operations.submission_admission import admit
    await admit(db,owner,"viewset",request.client_request_id)
    group = ViewSet(owner_id=owner,design_id=design.id,branch_id=branch.id,source_revision_id=snapshot.source.revision_id,
        client_request_id=request.client_request_id,request_digest=fingerprint,snapshot=snapshot.model_dump(mode="json"),
        snapshot_digest=snapshot.digest(),quote_id=quote.id,max_units=quote.max_units,
        authorization_expires_at=utc_now()+timedelta(seconds=get_settings().v2_viewset_authorization_seconds))
    db.add(group);await db.flush()
    for ordinal, view in enumerate(snapshot.options.views):
        db.add(ViewSetItem(viewset_id=group.id,design_id=design.id,view=view,ordinal=ordinal,
            unit_cost=quote.deliverables.get(view,0),inferred=snapshot.inferred(view),
            state="delivered" if view=="front" else "pending",
            revision_id=snapshot.source.revision_id if view=="front" else None))
    await db.flush()
    await hold(db,group,group.max_units)
    # This public message contains no internal model instructions.
    message = Message(branch_id=branch.id,role="user",content_parts=[{"type":"text","text":"生成四视图"},{"type":"viewset","viewset_id":group.id}])
    db.add(message);await db.flush()
    await event(db,design.id,"message.created",{"message_id":message.id,"operation_id":None})
    await advance(db,group)
    return group


async def schedule(db, group, item, *, client_request_id=None, request_digest=None, quote=None):
    snapshot = frozen(group)
    source = await db.get(Revision,group.source_revision_id)
    inputs = generation_snapshot(snapshot,item.view,expected_digest=group.snapshot_digest)
    branch = Branch(design_id=group.design_id,parent_branch_id=source.branch_id,
        origin_revision_id=source.id,current_revision_id=source.id,history_parent_sequence=source.history_sequence,
        context_seed={"kind":"fresh"})
    db.add(branch);await db.flush()
    record = InputRecord(branch_id=branch.id,digest=inputs.digest(),snapshot=inputs.model_dump(mode="json"))
    db.add(record);await db.flush()
    item.attempt_count += 1
    key = client_request_id or f"viewset:{item.id}:{item.attempt_count}"
    if quote is None:
        quote = Quote(owner_id=group.owner_id,scope_digest=digest({"viewset":group.id,"view":item.view,"attempt":item.attempt_count}),
            operation_kind="viewset",max_units=item.unit_cost,deliverables={"main_image":item.unit_cost},
            policy_version="viewset-authorized-slice-v1",expires_at=group.authorization_expires_at)
        db.add(quote);await db.flush()
    operation = Operation(owner_id=group.owner_id,design_id=group.design_id,branch_id=branch.id,
        client_request_id=key,request_digest=request_digest or digest({"viewset":group.id,"view":item.view,"attempt":item.attempt_count}),
        input_snapshot_id=record.id,quote_id=quote.id,kind="viewset",expected_branch_version=0,base_revision_id=source.id,
        reserved_units=item.unit_cost,context_before={"kind":"fresh"},context_allocation={"state":"pending"})
    db.add(operation);await db.flush()
    db.add(ViewSetAttempt(operation_id=operation.id,item_id=item.id,design_id=group.design_id,number=item.attempt_count))
    item.state="running";item.error_code=None
    group.state="running"
    await db.flush()
    await event(db,group.design_id,"operation.state_changed",{"operation_id":operation.id,"state":"queued"})
    return operation


async def advance(db, group):
    """Called under the design lock, in the same transaction as terminal operation state."""
    from app.operations.service import ACTIVE
    rows = await items(db,group)
    for item in rows:
        if item.attempt_count==0:continue
        operation = await db.scalar(select(Operation).join(ViewSetAttempt,ViewSetAttempt.operation_id==Operation.id)
            .where(ViewSetAttempt.item_id==item.id,ViewSetAttempt.number==item.attempt_count))
        if operation.state in ACTIVE:
            group.state = "cancelling" if group.cancel_requested else operation.state if operation.state in {"reconciling","publishing"} else "running"
            await changed(db,group)
            return
        if not item.revision_id:
            item.state="cancelled" if operation.state=="cancelled" else "failed"
            item.error_code=operation.error_code
    if group.cancel_requested:
        for item in rows:
            if item.state=="pending":item.state="cancelled"
    pending = next((item for item in rows if item.state=="pending"),None)
    if pending:
        await db.flush()  # Release the one-active-operation partial index before inserting a successor.
        await schedule(db,group,pending)
    else:
        generated=[item for item in rows if item.view!="front"]
        delivered=sum(item.revision_id is not None for item in generated)
        group.state="succeeded" if delivered==len(generated) else "partial" if delivered else "cancelled" if group.cancel_requested else "failed"
        await release_hold(db,group)
    await changed(db,group)


async def changed(db,group):
    await event(db,group.design_id,"viewset.updated",{"viewset_id":group.id,"state":group.state})
    await event(db,group.design_id,"quota.changed",{"viewset_id":group.id})


async def operation_finished(db,operation):
    if operation.kind!="viewset":return
    group,_,_=await binding(db,operation)
    await advance(db,group)


async def authorize_publication(db,operation):
    group,item,attempt=await binding(db,operation)
    if (group.cancel_requested or operation.cancel_requested or attempt.number!=item.attempt_count
            or item.revision_id is not None or item.view=="front"):
        raise ApplicationError("operation_not_publishable")
    if frozen(group).source.revision_id!=operation.base_revision_id:
        raise ApplicationError("input_snapshot_changed")
    return item




async def check_view_image(db, operation, sha256):
    from app.design.models import Asset
    group,item,_=await binding(db,operation)
    if sha256==frozen(group).source.image.sha256:
        raise ApplicationError("viewset_duplicate_image")
    duplicate=await db.scalar(select(ViewSetItem.id).join(Revision,Revision.id==ViewSetItem.revision_id)
        .join(Asset,Asset.id==Revision.clean_asset_id).where(ViewSetItem.viewset_id==group.id,
            ViewSetItem.id!=item.id,Asset.sha256==sha256).limit(1))
    if duplicate:
        raise ApplicationError("viewset_duplicate_image")


def retry_scope(group,item):
    return digest({"viewset":group.id,"snapshot":group.snapshot_digest,"view":item.view,"attempt":item.attempt_count+1})


async def retry_quote(db,owner,group_id,view):
    group=await owned_group(db,owner,group_id)
    design=await owned_design(db,owner,group.design_id)
    await require_admission(db, owner, design.id)
    if design.archived_at:raise ApplicationError("design_archived")
    item=next((item for item in await items(db,group) if item.view==view),None)
    if item is None or item.revision_id or item.state not in {"failed","cancelled"}:
        raise ApplicationError("viewset_retry_unavailable")
    await ensure_idle(db,group.design_id,own_group=group.id)
    quote=Quote(owner_id=owner,scope_digest=retry_scope(group,item),operation_kind="viewset",
        max_units=item.unit_cost,deliverables={"main_image":item.unit_cost},
        policy_version="viewset-confirmed-retry-v1",expires_at=utc_now()+timedelta(minutes=5))
    db.add(quote);await db.flush()
    return quote


async def retry_view(db,owner,group_id,view,request):
    group=await owned_group(db,owner,group_id)
    fingerprint=digest({"viewset":group_id,"view":view,"request":request.model_dump(mode="json")})
    previous=await db.scalar(select(Operation).where(Operation.owner_id==owner,Operation.client_request_id==request.client_request_id))
    if previous:
        if previous.request_digest!=fingerprint:raise ApplicationError("idempotency_conflict")
        return group
    from app.admin.v2_controls import gate
    await gate(db,owner)
    design=await owned_design(db,owner,group.design_id)
    await require_admission(db, owner, design.id)
    if design.archived_at:raise ApplicationError("design_archived")
    item=next((item for item in await items(db,group) if item.view==view),None)
    if item is None or view=="front":raise ApplicationError("viewset_retry_unavailable")
    if item.revision_id:return group
    await ensure_idle(db,group.design_id,own_group=group.id)
    if group.state in GROUP_ACTIVE or item.state not in {"failed","cancelled"}:
        raise ApplicationError("viewset_retry_unavailable")
    quote=None
    if request.quote_id:
        quote=await db.scalar(select(Quote).where(Quote.id==request.quote_id,Quote.owner_id==owner))
        if (quote is None or quote.operation_kind!="viewset" or quote.scope_digest!=retry_scope(group,item)
                or quote.expires_at<=utc_now() or quote.deliverables!={"main_image":item.unit_cost}
                or quote.max_units!=item.unit_cost):
            raise ApplicationError("quote_invalid")
        if await db.scalar(select(Operation.id).where(Operation.quote_id==quote.id)):
            raise ApplicationError("quote_already_used")
    elif group.authorization_expires_at<=utc_now():
        raise ApplicationError("viewset_confirmation_required")
    frozen(group)
    from app.operations.submission_admission import admit
    await admit(db,owner,"viewset_retry",request.client_request_id)
    group.run_number+=1
    group.cancel_requested=False
    await hold(db,group,item.unit_cost)
    await schedule(db,group,item,client_request_id=request.client_request_id,request_digest=fingerprint,quote=quote)
    await changed(db,group)
    return group


async def cancel_group(db,owner,identity):
    group=await owned_group(db,owner,identity)
    if group.state not in GROUP_ACTIVE:return group
    group.cancel_requested=True
    from app.operations.cancel import cancel
    from app.operations.service import ACTIVE
    active=await db.scalar(select(Operation).join(ViewSetAttempt,ViewSetAttempt.operation_id==Operation.id)
        .join(ViewSetItem,ViewSetItem.id==ViewSetAttempt.item_id)
        .where(ViewSetItem.viewset_id==group.id,Operation.state.in_(ACTIVE)))
    if active:
        await cancel(db,owner,active.id)
    else:
        await advance(db,group)
    return group

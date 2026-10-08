"""Resolve an editable message exclusively from its original immutable input."""
from dataclasses import dataclass
from sqlalchemy import select
from app.contracts.v2 import InputSnapshot, SnapshotReference
from app.conversation.checkpoints import ContextBoundary, resolve_boundary
from app.design.history import visibility_caps
from app.design.models import Asset, InputRecord, Message, Operation, Revision
from app.design.service import ApplicationError, owned_branch


@dataclass(frozen=True)
class MessageEdit:
    message: Message
    operation: Operation
    inputs: InputSnapshot
    boundary: ContextBoundary


async def prepare_message_edit(db, owner: str, branch_id: str, message_id: str) -> MessageEdit:
    design, branch = await owned_branch(db, owner, branch_id)
    if design.archived_at:
        raise ApplicationError("design_archived")
    message = await db.get(Message, message_id)
    caps, _ = await visibility_caps(db, design.id, branch, design.history_counter)
    if (message is None or message.role != "user" or message.branch_id not in caps
            or message.history_sequence > caps[message.branch_id] or not message.operation_id):
        raise ApplicationError("not_found")
    operation = await db.get(Operation, message.operation_id)
    if (operation is None or operation.design_id != design.id or operation.owner_id != owner
            or operation.branch_id != message.branch_id):
        raise ApplicationError("not_found")
    from app.operations.service import TERMINAL
    if operation.state not in TERMINAL or (operation.dispatch_intent_at is not None
            and operation.upstream_status not in {"completed", "failed", "interrupted"}):
        raise ApplicationError("history_checkpoint_unconfirmed")
    record = await db.get(InputRecord, operation.input_snapshot_id)
    if record is None:
        raise ApplicationError("input_snapshot_missing")
    try:
        inputs = InputSnapshot.model_validate(record.snapshot)
    except ValueError:
        raise ApplicationError("input_snapshot_changed") from None
    if inputs.digest() != record.digest:
        raise ApplicationError("input_snapshot_changed")
    if inputs.base_revision_id:
        revision = await db.get(Revision, inputs.base_revision_id)
        if revision is None or revision.design_id != design.id:
            raise ApplicationError("not_found")
    boundary = await resolve_boundary(db, design.id, operation.context_before)
    return MessageEdit(message, operation, inputs, boundary)


async def edited_inputs(db, owner, original: MessageEdit, request) -> InputSnapshot:
    source = original.inputs
    if request.base_revision_id != source.base_revision_id or request.continuation.source_revision_id != source.base_revision_id:
        raise ApplicationError("state_conflict")
    return await replace_input_text_references(db, owner, source, request)


async def replace_input_text_references(db, owner, source: InputSnapshot, request) -> InputSnapshot:
    if source.local_edit_mode and request.intent == "discussion_only":
        raise ApplicationError("operation_kind_not_enabled")
    originals = {r.asset_id:r for r in source.references}
    bindings = [(r.asset_id,r.role,r.usage) for r in source.references if r.role == "base"]
    bindings += [(r.asset_id,"reference",r.usage) for r in request.attachments]
    bindings += [(r.asset_id,r.role,r.usage) for r in source.references if r.role not in {"base", "reference"}]
    refs=[]
    for ordinal, (asset_id,role,usage) in enumerate(bindings, 1):
        asset=await db.scalar(select(Asset).where(Asset.id==asset_id,Asset.owner_id==owner,Asset.status=="ready"))
        if asset is None:
            raise ApplicationError("input_asset_unavailable")
        expected=originals.get(asset_id)
        if expected and asset.sha256!=expected.sha256:
            raise ApplicationError("input_asset_changed")
        refs.append(SnapshotReference(asset_id=asset_id,role=role,usage=usage,ordinal=ordinal,sha256=asset.sha256))
    return source.model_copy(update={"user_text":request.text,"references":tuple(refs)})

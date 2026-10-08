from __future__ import annotations
from datetime import timedelta
from pathlib import Path
from app.core.config import get_settings
from app.operations.local_edit import validate_prepared, validate_local_input
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.contracts.v2 import InputSnapshot, SnapshotReference, SubmitTurn
from app.core.time import utc_now
from app.conversation.bindings import user_content_parts
from app.auth.models import UserRole
from app.db.models import User
from app.design.models import Asset, Branch, InputRecord, Message, Operation, Quote, Revision
from app.design.service import ApplicationError, digest, event, owned_branch
from app.quota.wallet import reserve
from app.operations.planning import plan
from app.admin.rollout import require_admission

ACTIVE = {"queued", "reviewing", "running", "reconciling", "publishing", "cancelling"}
TERMINAL = {"succeeded", "partial_success", "failed", "cancelled", "awaiting_input"}


async def snapshot(db: AsyncSession, owner_id: str, branch: Branch, request: SubmitTurn, *, historical: bool = False) -> InputSnapshot:
    # An inline local edit never merges into the branch, so a newer branch version cannot make it stale.
    if not request.inline_local and branch.state_version != request.expected_branch_version:
        raise ApplicationError("state_conflict")
    # An explicit "no base image" turn is allowed; the branch version check above still rejects stale state.
    if not request.inline_local and not historical and request.base_revision_id is not None and branch.current_revision_id != request.base_revision_id:
        raise ApplicationError("state_conflict")
    bindings = []
    if request.base_revision_id:
        revision = (await db.execute(select(Revision).where(
            Revision.id == request.base_revision_id, Revision.design_id == branch.design_id
        ))).scalar_one_or_none()
        if revision is None:
            raise ApplicationError("not_found")
        if request.prepared_edit:
            await validate_prepared(db, owner_id, revision, request.prepared_edit)
        bindings.append((request.prepared_edit.clean_asset_id if request.prepared_edit else revision.clean_asset_id, "base", "Current working image"))
    bindings += [(item.asset_id, "reference", item.usage) for item in request.attachments]
    if request.annotation_asset_id:
        bindings.append((request.annotation_asset_id, "annotation", "Annotations, not the clean base"))
    if request.mask_asset_id:
        bindings.append((request.mask_asset_id, "mask", "Authorized local edit mask"))
    refs = []
    for ordinal, (asset_id, role, usage) in enumerate(bindings, 1):
        asset = (await db.execute(select(Asset).where(
            Asset.id == asset_id, Asset.owner_id == owner_id, Asset.status == "ready"
        ))).scalar_one_or_none()
        if asset is None:
            raise ApplicationError("not_found")
        refs.append(SnapshotReference(asset_id=asset.id, usage=usage, role=role,
                                      sha256=asset.sha256, ordinal=ordinal))
    result = InputSnapshot(user_text=request.text, base_revision_id=request.base_revision_id,
        branch_version=branch.state_version, references=tuple(refs),
        local_edit_mode=request.local_edit_mode, prepared_edit=request.prepared_edit, target_kind=request.target_kind,
        inline_local=request.inline_local)
    if request.mask_asset_id and not request.local_edit_mode:
        raise ApplicationError("local_edit_mode_required")
    return result


def kind_for(request: SubmitTurn) -> str:
    if request.intent == "discussion_only":
        return "discussion"
    if request.mask_asset_id:
        return "local_edit"
    return "image_edit" if request.base_revision_id else "generate"


def scope_digest(kind: str, input_snapshot: InputSnapshot) -> str:
    return digest({"kind": kind, "input_digest": input_snapshot.digest()})


async def require_supported(db, owner_id, prepared):
    settings = get_settings()
    if len(prepared.inputs.references) > settings.v2_max_input_images:
        raise ApplicationError("reference_limit_exceeded")
    asset_ids = [item.asset_id for item in prepared.inputs.references]
    if asset_ids:
        sizes = dict((await db.execute(select(Asset.id, Asset.byte_size).where(Asset.id.in_(asset_ids), Asset.owner_id == owner_id))).all())
        if any(asset_id not in sizes for asset_id in asset_ids):
            raise ApplicationError("not_found")
        if sum(sizes[asset_id] for asset_id in asset_ids) > settings.v2_max_input_bytes:
            raise ApplicationError("reference_limit_exceeded")
    if prepared.kind == "local_edit" or prepared.inputs.local_edit_mode:
        if not settings.v2_local_edit_enabled:
            raise ApplicationError("operation_kind_not_enabled")
        await validate_local_input(db, owner_id, prepared.inputs, Path(settings.v2_storage_dir))


async def quote(db: AsyncSession, owner_id: str, branch_id: str, request: SubmitTurn) -> Quote:
    design, branch = await owned_branch(db, owner_id, branch_id, lock=True)
    if design.archived_at:
        raise ApplicationError("design_archived")
    await require_admission(db, owner_id, design.id)
    prepared = await plan(db, owner_id, branch, request)
    inputs, kind = prepared.inputs, prepared.kind
    await require_supported(db, owner_id, prepared)
    if kind not in {"generate", "image_edit", "regenerate", "local_edit"}:
        raise ApplicationError("operation_kind_not_enabled")
    # Preserve the existing one-unit-per-image rule. Four-view pricing is not exposed here.
    user = await db.get(User, owner_id)
    units = 0 if kind == "discussion" or user.role == UserRole.admin else 1
    result = Quote(owner_id=owner_id, scope_digest=prepared.scope(branch_id, request), operation_kind=kind,
                   max_units=units, deliverables={} if kind == "discussion" else {"main_image": units},
                   policy_version="legacy-one-image-v1", expires_at=utc_now() + timedelta(minutes=5))
    db.add(result)
    await db.flush()
    return result


async def submit(db: AsyncSession, owner_id: str, branch_id: str, request: SubmitTurn,
                 *, discussion_enabled: bool = False) -> Operation:
    design, branch = await owned_branch(db, owner_id, branch_id, lock=True)
    canonical = request.model_dump(mode="json")
    for field in ("local_edit_mode", "prepared_edit", "answer_to"):
        if canonical[field] is None:
            canonical.pop(field)
    if not canonical["inline_local"]:
        canonical.pop("inline_local")  # Identities accepted before the additive field stay valid.
    if canonical["regenerate_revision_id"] is None:
        canonical.pop("regenerate_revision_id")  # Preserve identities accepted before the additive field.
    fingerprint = digest({"branch_id": branch_id, "request": canonical})
    previous = (await db.execute(select(Operation).where(
        Operation.owner_id == owner_id, Operation.client_request_id == request.client_request_id
    ))).scalar_one_or_none()
    if previous is not None:
        if previous.request_digest != fingerprint:
            raise ApplicationError("idempotency_conflict")
        return previous
    if design.archived_at:
        raise ApplicationError("design_archived")
    from app.admin.v2_controls import gate
    await gate(db, owner_id)
    await require_admission(db, owner_id, design.id)
    prepared = await plan(db, owner_id, branch, request)
    inputs, kind = prepared.inputs, prepared.kind
    if kind == "discussion" and not discussion_enabled:
        raise ApplicationError("discussion_not_enabled")
    active = (await db.execute(select(Operation.id).where(
        Operation.design_id == design.id, Operation.state.in_(ACTIVE)
    ))).first()
    if active:
        raise ApplicationError("design_busy")
    await require_supported(db, owner_id, prepared)
    if kind not in {"generate", "image_edit", "regenerate", "discussion", "local_edit"}:
        raise ApplicationError("operation_kind_not_enabled")
    bound_quote = None
    if request.quote_id:
        bound_quote = (await db.execute(select(Quote).where(
            Quote.id == request.quote_id, Quote.owner_id == owner_id
        ))).scalar_one_or_none()
    if kind != "discussion" and bound_quote is None:
        raise ApplicationError("quote_invalid")
    if bound_quote is not None:
        if bound_quote.expires_at <= utc_now() or bound_quote.scope_digest != prepared.scope(branch_id, request):
            raise ApplicationError("quote_invalid")
        used = (await db.execute(select(Operation.id).where(Operation.quote_id == bound_quote.id))).first()
        if used:
            raise ApplicationError("quote_already_used")
    from app.operations.submission_admission import admit
    await admit(db, owner_id, "turn", request.client_request_id)
    if prepared.boundary is not None:
        from app.design.history import fork_history_sequence
        if prepared.boundary_message_id:
            message = await db.get(Message, prepared.boundary_message_id)
            history_end = message.history_sequence - 1
        else:
            origin = await db.get(Revision, prepared.origin_revision_id)
            history_end = await fork_history_sequence(db, origin, regenerate=kind == "regenerate")
        branch = Branch(design_id=design.id, parent_branch_id=prepared.parent_branch_id,
                        origin_revision_id=prepared.origin_revision_id, current_revision_id=inputs.base_revision_id,
                        context_seed=prepared.checkpoint(), history_parent_sequence=history_end,
                        fork_boundary_message_id=prepared.boundary_message_id)
        db.add(branch)
        await db.flush()
    if prepared.original_input_id:
        record = await db.get(InputRecord, prepared.original_input_id)
    else:
        record = InputRecord(branch_id=branch.id, digest=inputs.digest(), snapshot=inputs.model_dump(mode="json"))
        db.add(record)
        await db.flush()
    context = prepared.checkpoint()
    allocate_context = branch.active_epoch_id is None
    if allocate_context and context is None:
        context = branch.context_seed or {"kind": "fresh"}
    operation = Operation(
        owner_id=owner_id, design_id=design.id, branch_id=branch.id,
        client_request_id=request.client_request_id, request_digest=fingerprint,
        input_snapshot_id=record.id, quote_id=bound_quote.id if bound_quote else None,
        kind=kind, expected_branch_version=branch.state_version, base_revision_id=inputs.base_revision_id,
        context_before=context, context_allocation={"state":"pending"} if allocate_context else None,
        source_operation_id=prepared.source_operation_id,
        reserved_units=bound_quote.max_units if bound_quote else 0,
    )
    db.add(operation)
    await db.flush()
    await reserve(db, operation)
    if prepared.answer_version:
        from app.conversation.questions import resolve_question
        await resolve_question(db, prepared.source_operation_id, operation.id)
    if not request.inline_local:
        design.last_branch_id = branch.id
        message = Message(branch_id=branch.id, operation_id=operation.id, role="user",
                          content_parts=user_content_parts(inputs))
        db.add(message)
        await db.flush()
        await event(db, design.id, "message.created", {"message_id": message.id, "operation_id": operation.id})
    await event(db, design.id, "operation.state_changed", {"operation_id": operation.id, "state": operation.state})
    await event(db, design.id, "quota.changed", {"operation_id": operation.id})
    return operation

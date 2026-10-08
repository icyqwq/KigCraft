from __future__ import annotations
import hashlib
from uuid import NAMESPACE_URL, uuid5
from app.codex_runtime.selection import runtime_allowed
import asyncio
import json
from pathlib import Path
from typing import Literal
from app.design.lifecycle import UpdateDesign, update_design
from app.design.purge import DeleteRequest, purge_design, remove_objects
from app.design.catalog import list_designs
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.assets.storage import MAX_UPLOAD_BYTES, asset_path, upload_in_transaction
from app.auth.models import UserRole
from app.auth.sessions import get_current_user_and_session
from app.conversation.public_text import public_assistant_text
from app.conversation.bindings import public_numbered_parts, reference_bindings
from app.contracts.v2 import InputSnapshot, QuoteRequest, SubmitTurn, V2Capabilities
from app.core.config import get_settings
from app.db.models import User
from app.db.session import get_sessionmaker
from app.design.manual import SaveManualRevision, save_manual
from app.design.history import history_page, design_revision_page
from app.design.models import Asset, Branch, Design, DomainEvent, InputRecord, Message, Operation, Quote, Revision, Wallet, LedgerEntry
from app.design.service import ApplicationError, create_design, owned_branch, owned_design
from app.operations.cancel import cancel
from app.operations.service import quote, submit
from app.operations.planning import plan

router = APIRouter(prefix="/v2", tags=["design-v2"])


async def enabled():
    if not get_settings().v2_enabled:
        raise HTTPException(404, detail={"code": "v2_disabled"})


async def transaction():
    async with get_sessionmaker()() as db:
        async with db.begin():
            yield db


async def user(request: Request) -> User:
    async with get_sessionmaker()() as db:
        current_user, _ = await get_current_user_and_session(request, db)
        request.state.v2_actor_id = current_user.id
        return current_user


def require_new_operation(current: User):
    settings = get_settings()
    if not settings.v2_new_operations:
        raise ApplicationError("new_operations_disabled")
    if settings.v2_runtime_mode == "local" and settings.v2_local_user_id != current.id:
        raise ApplicationError("local_test_account_required")
    if not runtime_allowed(settings, current.id):
        raise ApplicationError("runtime_not_configured")


class CreateDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: str = Field(min_length=8, max_length=100)
    title: str = Field(min_length=1, max_length=200)


def design_json(item: Design):
    return {"id": item.id, "title": item.title, "last_branch_id": item.last_branch_id,
            "pinned": item.pinned, "archived_at": item.archived_at, "deleted_at": item.deleted_at, "last_activity_at": item.last_activity_at,
            "metadata_version": item.metadata_version, "updated_at": item.updated_at}


def operation_json(item: Operation):
    result = {"id": item.id, "branch_id": item.branch_id, "kind": item.kind, "state": item.state,
              "base_revision_id": item.base_revision_id, "reserved_units": item.reserved_units,
              "settled_units": item.settled_units, "error_code": item.error_code}
    if item.kind == "local_edit" and item.state == "succeeded":
        # The editor's local result is a bare asset; its id follows from the operation (see publish_inline).
        result["result_asset_id"] = str(uuid5(NAMESPACE_URL, f"kigcraft-v2:{item.id}:main_image"))
    return result


def message_json(item: Message, inputs: InputSnapshot | None = None):
    parts = public_numbered_parts(item.content_parts, inputs) if item.role == "user" else item.content_parts
    if item.role == "assistant":
        parts = [{**part, "text": public_assistant_text(part["text"])}
                 if part.get("type") == "text" else part for part in parts if part.get("type") != "reply_intent"]
        parts = [part for part in parts if part.get("type") != "text" or part.get("text")]
        if not parts:
            return None
    return {"id": item.id, "history_sequence": item.history_sequence, "branch_id": item.branch_id, "role": item.role,
            "can_edit": item.role == "user" and item.operation_id is not None, "content_parts": parts,
            "operation_id": item.operation_id, "status": item.status, "created_at": item.created_at}


def revision_json(item: Revision, dimensions: dict | None = None):
    return {**(dimensions or {}), "id": item.id, "number": item.number, "history_sequence": item.history_sequence, "branch_id": item.branch_id, "parent_revision_id": item.parent_revision_id,
            "asset_id": item.clean_asset_id, "root_source_asset_id": item.root_source_asset_id,
            "operation_id": item.operation_id, "source_kind": item.source_kind, "view": item.view, "image_kind": item.image_kind,
            "root_image_url": f"/api/v2/assets/{item.root_source_asset_id}", "recipe": item.recipe,
            # The revision id lets the server print the version and date into the delivered copy of AI output.
            "image_url": f"/api/v2/assets/{item.clean_asset_id}?revision={item.id}", "created_at": item.created_at,
            "can_continue": item.context_checkpoint is not None,
            "can_regenerate": item.source_kind == "ai" and item.context_checkpoint is not None}


@router.get("/capabilities")
async def capabilities():
    settings = get_settings()
    available = settings.v2_enabled and settings.v2_new_operations and runtime_allowed(settings)
    return V2Capabilities(enabled=settings.v2_enabled, new_operations=available,
                          generation=available,
                          discussion=False, history_fork=available, viewsets=available and settings.v2_viewsets_enabled, local_edits=available and settings.v2_local_edit_enabled, max_input_images=settings.v2_max_input_images,
                          design_limit=settings.v2_design_limit_per_user or None)


@router.post("/designs", dependencies=[Depends(enabled)])
async def create(payload: CreateDesign, current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    return design_json(await create_design(db, current.id, payload.client_request_id, payload.title))


@router.get("/designs", dependencies=[Depends(enabled)])
async def designs(response: Response, q: str = Query(default="", max_length=200),
                  status: Literal["active","archived"] = "active",
                  cursor: str | None = Query(default=None, max_length=1024),
                  limit: int = Query(default=30, ge=1, le=100), current: User = Depends(user),
                  db: AsyncSession = Depends(transaction)):
    rows, details, next_cursor = await list_designs(db, current.id, q=q, status=status, cursor=cursor, limit=limit)
    response.headers["Cache-Control"] = "private, no-store"
    return {"items": [{**design_json(item), **details[item.id]} for item in rows], "next_cursor":next_cursor}


@router.patch("/designs/{design_id}", dependencies=[Depends(enabled)])
async def rename(design_id: str, payload: UpdateDesign, current: User = Depends(user),
                 db: AsyncSession = Depends(transaction)):
    return design_json(await update_design(db, current.id, design_id, payload))


@router.get("/designs/{design_id}/lifecycle", dependencies=[Depends(enabled)])
async def design_lifecycle(design_id: str, response: Response, current: User = Depends(user),
                           db: AsyncSession = Depends(transaction)):
    design = await db.scalar(select(Design).where(Design.id == design_id, Design.owner_id == current.id))
    if design is None:
        raise ApplicationError("not_found")
    response.headers["Cache-Control"] = "private, no-store"
    return design_json(design)


@router.delete("/designs/{design_id}", dependencies=[Depends(enabled)])
async def delete_design(design_id: str, payload: DeleteRequest, background: BackgroundTasks,
                        current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    """Permanent. The files go after the commit, so a failed delete never loses an image."""
    background.add_task(remove_objects, await purge_design(db, current.id, design_id, payload))
    return {"deleted": True}


async def revision_sizes(db, revisions):
    ids = {revision.clean_asset_id for revision in revisions}
    if not ids:
        return {}
    rows = (await db.execute(select(Asset.id, Asset.width, Asset.height).where(Asset.id.in_(ids)))).all()
    return {row.id: {"width":row.width,"height":row.height} for row in rows}


async def history_json(db, design, branch, *, cursor=None, limit=50, kind="all", message_id=None):
    page = await history_page(db, design, branch, cursor=cursor, limit=limit, kind=kind, message_id=message_id)
    messages = [item for entry_kind, item in page.records if entry_kind == "message"]
    legacy_ids = [m.operation_id for m in messages if m.role == "user" and m.operation_id and
                  not any(part.get("type") == "annotations" for part in m.content_parts)]
    message_inputs = {}
    if legacy_ids:
        records = (await db.execute(select(Operation.id, InputRecord).join(InputRecord, Operation.input_snapshot_id == InputRecord.id)
                   .where(Operation.id.in_(legacy_ids), Operation.design_id == design.id))).all()
        for operation_id, record in records:
            try:
                inputs = InputSnapshot.model_validate(record.snapshot)
                if inputs.digest() == record.digest:
                    message_inputs[operation_id] = inputs
            except ValueError:
                pass  # Unverifiable old data stays intact; never infer a binding from current state.
    image_sizes = await revision_sizes(db, [item for kind, item in page.records if kind == "revision"])
    items = []
    for entry_kind, item in page.records:
        if entry_kind == "message":
            value = message_json(item, message_inputs.get(item.operation_id))
            if value is not None:
                items.append({"type": "message", "message": value})
        else:
            items.append({"type": "revision", "revision": revision_json(item, image_sizes.get(item.clean_asset_id))})
    return {"items": items, "next_cursor": page.next_cursor, "watermark": page.watermark,
            "ancestry_incomplete": page.ancestry_incomplete}


@router.get("/branches/{branch_id}/history", dependencies=[Depends(enabled)])
async def branch_history(branch_id: str, response: Response, cursor: str | None = Query(default=None, max_length=512),
                         message_id: str | None = Query(default=None, max_length=36),
                         limit: int = Query(default=50, ge=1, le=100),
                         current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    design, branch = await owned_branch(db, current.id, branch_id, lock=True)
    response.headers["Cache-Control"] = "private, no-store"
    return await history_json(db, design, branch, cursor=cursor, limit=limit, message_id=message_id)


@router.get("/branches/{branch_id}/messages/{message_id}/edit", dependencies=[Depends(enabled)])
async def message_edit_preview(branch_id: str, message_id: str, response: Response,
                               current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    from app.operations.message_edit import prepare_message_edit
    from app.conversation.bindings import annotation_bindings
    _, branch = await owned_branch(db, current.id, branch_id, lock=True)
    original = await prepare_message_edit(db, current.id, branch_id, message_id)
    inputs = original.inputs
    response.headers["Cache-Control"] = "private, no-store"
    return {"message_id": message_id, "source_revision_id": inputs.base_revision_id,
            "branch_version": branch.state_version, "text": inputs.user_text,
            "base_image_url": next((f"/api/v2/assets/{r.asset_id}" for r in inputs.references if r.role == "base"), None),
            "references": [{**r, "image_url": "/api/v2/assets/" + r["asset_id"]} for r in reference_bindings(inputs)],
            "reserved_images": sum(r.role != "reference" for r in inputs.references),
            "local_edit_mode": inputs.local_edit_mode, "target_kind": inputs.target_kind, "annotations": annotation_bindings(inputs)}


@router.get("/branches/{branch_id}/messages", dependencies=[Depends(enabled)])
async def branch_messages(branch_id: str, response: Response, cursor: str | None = Query(default=None, max_length=512),
                          limit: int = Query(default=50, ge=1, le=100),
                          current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    design, branch = await owned_branch(db, current.id, branch_id, lock=True)
    response.headers["Cache-Control"] = "private, no-store"
    page = await history_json(db, design, branch, cursor=cursor, limit=limit, kind="message")
    return {**page, "items": [item["message"] for item in page["items"]]}


@router.get("/designs/{design_id}/revisions", dependencies=[Depends(enabled)])
async def design_revisions(design_id: str, response: Response, branch_id: str | None = None,
                           scope: Literal["branch", "design"] = "branch",
                           cursor: str | None = Query(default=None, max_length=512),
                           limit: int = Query(default=50, ge=1, le=100),
                           current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    design = await owned_design(db, current.id, design_id, lock=True)
    if scope == "design":
        if branch_id is not None:
            raise ApplicationError("invalid_history_cursor")
        page, total = await design_revision_page(db, design, cursor=cursor, limit=limit)
        revisions = [item for _, item in page.records]
        sizes = await revision_sizes(db, revisions)
        response.headers["Cache-Control"] = "private, no-store"
        return {"items": [revision_json(item, sizes.get(item.clean_asset_id)) for item in revisions],
                "next_cursor": page.next_cursor, "watermark": page.watermark, "total": total,
                "ancestry_incomplete": False}
    _, branch = await owned_branch(db, current.id, branch_id or design.last_branch_id)
    if branch.design_id != design.id:
        raise ApplicationError("not_found")
    response.headers["Cache-Control"] = "private, no-store"
    page = await history_json(db, design, branch, cursor=cursor, limit=limit, kind="revision")
    return {**page, "items": [item["revision"] for item in page["items"]]}


@router.get("/designs/{design_id}/revisions/{revision_id}", dependencies=[Depends(enabled)])
async def design_revision(design_id: str, revision_id: str, response: Response,
                          current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    await owned_design(db, current.id, design_id, lock=True)
    revision = await db.scalar(select(Revision).where(Revision.id == revision_id, Revision.design_id == design_id))
    if revision is None:
        raise ApplicationError("not_found")
    sizes = await revision_sizes(db, [revision])
    response.headers["Cache-Control"] = "private, no-store"
    return revision_json(revision, sizes.get(revision.clean_asset_id))


@router.get("/revisions/{revision_id}/candidates", dependencies=[Depends(enabled)])
async def revision_candidates(revision_id: str, response: Response,
                              cursor: str | None = Query(default=None, max_length=512),
                              limit: int = Query(default=50, ge=1, le=100),
                              current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    from app.design.candidates import candidates
    page = await candidates(db, current.id, revision_id, cursor=cursor, limit=limit)
    response.headers["Cache-Control"] = "private, no-store"
    sizes = await revision_sizes(db, page.items)
    return {"group": page.group, "items": [revision_json(item, sizes.get(item.clean_asset_id)) for item in page.items],
            "next_cursor": page.next_cursor, "watermark": page.watermark, "total": page.total}


@router.get("/designs/{design_id}", dependencies=[Depends(enabled)])
async def design_snapshot(design_id: str, response: Response, branch_id: str | None = None, current: User = Depends(user),
                          db: AsyncSession = Depends(transaction)):
    design = await owned_design(db, current.id, design_id, lock=True)
    selected_branch = branch_id or design.last_branch_id
    _, branch = await owned_branch(db, current.id, selected_branch)
    if branch.design_id != design.id:
        raise ApplicationError("not_found")
    response.headers["Cache-Control"] = "private, no-store"
    history = await history_json(db, design, branch)
    operations = (await db.execute(select(Operation).where(Operation.branch_id == branch.id)
                                  .order_by(Operation.created_at.desc()).limit(50))).scalars().all()
    current_revision = await db.get(Revision, branch.current_revision_id) if branch.current_revision_id else None
    branches = (await db.execute(select(Branch).where(Branch.design_id == design.id)
        .order_by(Branch.created_at, Branch.id))).scalars().all()
    from app.operations.service import ACTIVE
    active_operation = (await db.execute(select(Operation).where(
        Operation.design_id == design.id, Operation.state.in_(ACTIVE)))).scalar_one_or_none()
    from app.conversation.questions import question_json
    current_sizes = await revision_sizes(db, [current_revision] if current_revision else [])
    # All design writers acquire the design lock, so sequence and records share one boundary.
    from app.design.models import LegacyImport
    imported=await db.scalar(select(LegacyImport.id).where(LegacyImport.design_id==design.id,LegacyImport.owner_id==current.id).limit(1))
    return {**design_json(design), "legacy_imported":imported is not None, "snapshot_sequence": design.event_sequence,
            "branch": {"id": branch.id, "state_version": branch.state_version, "current_revision_id": branch.current_revision_id},
            "branches": [{"id":b.id,"parent_branch_id":b.parent_branch_id,"origin_revision_id":b.origin_revision_id} for b in branches],
            "design_active_operation": operation_json(active_operation) if active_operation else None,
            "history": history,
            "current_revision": revision_json(current_revision, current_sizes.get(current_revision.clean_asset_id)) if current_revision else None,
            "messages": [item["message"] for item in history["items"] if item["type"] == "message"],
            "revisions": [item["revision"] for item in history["items"] if item["type"] == "revision"],
            "operations": [operation_json(o) for o in operations],
            "questions": [await question_json(db,o) for o in operations if o.question]}


@router.post("/branches/{branch_id}/quotes", dependencies=[Depends(enabled)])
async def create_quote(branch_id: str, payload: QuoteRequest, current: User = Depends(user),
                       db: AsyncSession = Depends(transaction)):
    require_new_operation(current)
    result = await quote(db, current.id, branch_id, payload)
    _, branch = await owned_branch(db, current.id, branch_id)
    preview = (await plan(db, current.id, branch, payload)).inputs
    return {"request_preview":{"text":preview.user_text,
                "base_image_url":next((f"/api/v2/assets/{r.asset_id}" for r in preview.references if r.role=="base"), None),
                "references":[{"image_url":"/api/v2/assets/"+r["asset_id"],"usage":r["usage"],"number":r["number"]} for r in reference_bindings(preview)]},
            "id": result.id, "max_units": result.max_units, "deliverables": result.deliverables,
            "expires_at": result.expires_at, "policy_version": result.policy_version}


@router.post("/branches/{branch_id}/turns", status_code=202, dependencies=[Depends(enabled)])
async def create_turn(branch_id: str, payload: SubmitTurn, current: User = Depends(user),
                      db: AsyncSession = Depends(transaction)):
    require_new_operation(current)
    return operation_json(await submit(db, current.id, branch_id, payload))


@router.get("/operations/{operation_id}/question", dependencies=[Depends(enabled)])
async def question_preparation(operation_id: str, response: Response, current: User = Depends(user),
                               db: AsyncSession = Depends(transaction)):
    from app.conversation.questions import question_json, original_inputs, saved
    from app.conversation.bindings import annotation_bindings
    item = await db.get(Operation, operation_id)
    if not item or item.owner_id != current.id or not item.question:
        raise ApplicationError("not_found")
    await owned_branch(db,current.id,item.branch_id,lock=True)
    await db.refresh(item)  # The question may have been answered while this read waited for the design lock.
    value=saved(item)
    inputs=await original_inputs(db,item)
    response.headers["Cache-Control"]="private, no-store"
    return {**await question_json(db,item),"message_id":item.id,"branch_id":item.branch_id,
        "source_revision_id":value.base_revision_id,"branch_version":value.branch_version,"text":"",
        "base_image_url":next((f"/api/v2/assets/{r.asset_id}" for r in inputs.references if r.role=="base"),None),
        "references":[{**r,"image_url":"/api/v2/assets/"+r["asset_id"]} for r in reference_bindings(inputs)],
        "reserved_images":sum(r.role!="reference" for r in inputs.references),
        "local_edit_mode":inputs.local_edit_mode,"target_kind":inputs.target_kind,"annotations":annotation_bindings(inputs)}


@router.post("/operations/{operation_id}/answers", status_code=202, dependencies=[Depends(enabled)])
async def answer_question(operation_id: str, payload: SubmitTurn, current: User = Depends(user),
                          db: AsyncSession = Depends(transaction)):
    require_new_operation(current)
    if not payload.answer_to or payload.answer_to.operation_id != operation_id:
        raise ApplicationError("question_version_conflict")
    item=await db.get(Operation,operation_id)
    if not item or item.owner_id != current.id:
        raise ApplicationError("not_found")
    return operation_json(await submit(db,current.id,item.branch_id,payload))


@router.get("/operations/{operation_id}", dependencies=[Depends(enabled)])
async def operation(operation_id: str, current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    item = (await db.execute(select(Operation).where(Operation.id == operation_id, Operation.owner_id == current.id))).scalar_one_or_none()
    if item is None:
        raise ApplicationError("not_found")
    return operation_json(item)


@router.post("/operations/{operation_id}/cancel", dependencies=[Depends(enabled)])
async def cancel_operation(operation_id: str, current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    return {"state": await cancel(db, current.id, operation_id)}


@router.post("/assets/uploads", dependencies=[Depends(enabled)])
async def upload(file: UploadFile = File(...), expected_owner_id: str | None = Query(default=None, max_length=36),
                 current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    if expected_owner_id is not None and current.id != expected_owner_id:
        raise ApplicationError("not_found")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    asset = await upload_in_transaction(db, data, current.id, Path(get_settings().v2_storage_dir))
    db.add(asset)
    await db.flush()
    return {"id": asset.id, "status": asset.status, "width": asset.width, "height": asset.height,
            "sha256": asset.sha256, "image_url": f"/api/v2/assets/{asset.id}"}


@router.get("/assets/{asset_id}", dependencies=[Depends(enabled)])
async def image(asset_id: str, request: Request, current: User = Depends(user), db: AsyncSession = Depends(transaction),
                revision: str | None = Query(default=None, max_length=36)):
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id, Asset.owner_id == current.id, Asset.status == "ready"))).scalar_one_or_none()
    if asset is None:
        raise ApplicationError("not_found")
    from app.assets.repository import storage_io
    from app.images.delivery import WATERMARK_VERSION, locale_from, needs_watermark, version_label, watermarked_jpeg
    path = await storage_io(asset_path, asset, Path(get_settings().v2_storage_dir))
    # The stored original's hash identifies the editing source even when the bytes sent are a watermarked copy.
    # An asset never changes (a new picture is a new asset), so the browser may keep it for a week; it is private to the
    # signed-in user's browser and the server still checks ownership on every request that reaches it.
    headers = {"Cache-Control": "private, max-age=604800, immutable", "X-Content-Type-Options": "nosniff", "X-Source-Sha256": asset.sha256}
    if await needs_watermark(db, asset):
        # AI output never leaves the server without the watermark; the stored object stays clean for the pipeline.
        locale = locale_from(request.headers.get("accept-language"))
        label = await version_label(db, current.id, asset.id, revision)
        tag = f'"{asset.sha256[:32]}-{WATERMARK_VERSION}-{locale}-{hashlib.sha256((label or "").encode()).hexdigest()[:8]}"'
        headers = {**headers, "Vary": "Accept-Language", "ETag": tag}
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=headers)
        data = await asyncio.to_thread(watermarked_jpeg, path, key=asset.id + ":" + asset.sha256, locale=locale, label=label)
        return Response(data, media_type="image/jpeg", headers=headers)
    tag = f'"{asset.sha256[:32]}"'
    headers = {**headers, "ETag": tag}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type=asset.mime_type, headers=headers)


@router.get("/account/quota", dependencies=[Depends(enabled)])
async def quota_state(current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    from app.quota import shared
    if shared.enabled():
        from app.generation.quota import get_generation_quota_state
        state = await get_generation_quota_state(db, current)
        return {"available": state.remaining, "reserved": 0, "balance": state.remaining, "status": "shared",
                "reset_at": state.reset_at, "window_start_at": state.window_start_at, "used": state.used,
                "exempt": current.role == UserRole.admin, "entries": []}
    wallet = (await db.execute(select(Wallet).where(
        Wallet.owner_id == current.id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if wallet is None or wallet.write_owner != "v2":
        return {"available": None, "reserved": None, "balance": None,
                "status": wallet.write_owner if wallet else "not_migrated"}
    from app.quota.policy import top_up
    policy = await top_up(db, wallet)
    records = (await db.execute(select(LedgerEntry).where(
        LedgerEntry.owner_id == current.id
    ).order_by(LedgerEntry.created_at.desc(), LedgerEntry.id.desc()).limit(30))).scalars().all()
    return {"available": wallet.balance - wallet.reserved, "reserved": wallet.reserved, "balance": wallet.balance,
            "status": wallet.write_owner, "reset_at": policy["reset_at"],
            "window_start_at": policy["window_start_at"], "used": policy["used"], "exempt": policy["exempt"],
            "entries": [{"id": record.id, "kind": record.kind, "balance_delta": record.balance_delta,
                         "reserved_delta": record.reserved_delta, "created_at": record.created_at,
                         "operation_id": record.operation_id} for record in records]}



@router.get("/designs/{design_id}/events", dependencies=[Depends(enabled)])
async def events(design_id: str, request: Request, after: int = Query(default=0, ge=0),
                 current: User = Depends(user)):
    raw_last = request.headers.get("last-event-id")
    if raw_last:
        try:
            after = max(after, int(raw_last))
        except ValueError:
            raise HTTPException(400, detail={"code": "invalid_event_cursor"}) from None
    async with get_sessionmaker()() as db:
        await owned_design(db, current.id, design_id)
    async def stream():
        cursor = after
        while not await request.is_disconnected():
            async with get_sessionmaker()() as db:
                try:
                    refreshed, _ = await get_current_user_and_session(request, db)
                    if refreshed.id != current.id:
                        request.state.access_stream_rejected = True
                        request.state.access_auth_failure = "unauthenticated"
                        return
                    design = await owned_design(db, current.id, design_id)
                except (HTTPException, ApplicationError):
                    request.state.access_stream_rejected = True
                    return
                if cursor > design.event_sequence:
                    yield 'event: resync_required\ndata: {}\n\n'
                    return
                rows = (await db.execute(select(DomainEvent).where(
                    DomainEvent.design_id == design_id, DomainEvent.sequence > cursor
                ).order_by(DomainEvent.sequence).limit(100))).scalars().all()
                batch = [(row.sequence, row.event_type, row.payload) for row in rows]
                if (batch and batch[0][0] != cursor + 1) or (not batch and cursor < design.event_sequence):
                    yield 'event: resync_required\ndata: {}\n\n'
                    return
            for sequence, event_type, payload in batch:
                yield f"id: {sequence}\nevent: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                cursor = sequence
            if not batch:
                yield ": keep-alive\n\n"
                await asyncio.sleep(2)
    from app.admin.access_requests import observed_stream
    return StreamingResponse(observed_stream(request, stream()), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

@router.post("/branches/{branch_id}/manual-revisions", dependencies=[Depends(enabled)])
async def save_manual_revision(branch_id: str, payload: SaveManualRevision, current: User = Depends(user),
                               db: AsyncSession = Depends(transaction)):
    return revision_json(await save_manual(db, current.id, branch_id, payload))

# Draft synchronization is a free document write, independent of operation/AI gates.
from uuid import UUID
from fastapi import Response
from app.design.drafts import SaveDraft, DeleteDraft, save_draft, list_drafts, delete_draft


@router.get("/branches/{branch_id}/editor-drafts", dependencies=[Depends(enabled)])
async def editor_draft_list(branch_id: str, response: Response,
                            base_revision_id: str = Query(min_length=1, max_length=36),
                            current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    response.headers["Cache-Control"] = "private, no-store"
    return {"items": await list_drafts(db, current.id, branch_id, base_revision_id)}


@router.put("/editor-drafts/{draft_id}", dependencies=[Depends(enabled)])
async def editor_draft_put(draft_id: UUID, payload: SaveDraft, response: Response,
                           current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    response.headers["Cache-Control"] = "private, no-store"
    return await save_draft(db, current.id, str(draft_id), payload)


@router.delete("/editor-drafts/{draft_id}", dependencies=[Depends(enabled)])
async def editor_draft_delete(draft_id: UUID, payload: DeleteDraft, response: Response,
                              current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    response.headers["Cache-Control"] = "private, no-store"
    return await delete_draft(db, current.id, str(draft_id), payload)


from app.design.editor_save import SaveEditorDocument, save_editor, read_checkpoint, checkpoint_json

@router.get("/branches/{branch_id}/editor-checkpoint", dependencies=[Depends(enabled)])
async def editor_checkpoint_get(branch_id: str, response: Response,
                                base_revision_id: str = Query(min_length=1, max_length=36),
                                current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    response.headers["Cache-Control"] = "private, no-store"
    return {"checkpoint": checkpoint_json(await read_checkpoint(db, current.id, branch_id, base_revision_id))}

@router.post("/branches/{branch_id}/editor-saves", dependencies=[Depends(enabled)])
async def editor_save_post(branch_id: str, payload: SaveEditorDocument, response: Response,
                           current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    response.headers["Cache-Control"] = "private, no-store"
    disposition, saved, revision = await save_editor(db, current.id, branch_id, payload,
        storage_root=Path(get_settings().v2_storage_dir))
    return {"disposition":disposition,"checkpoint":checkpoint_json(saved),"revision":revision_json(revision)}


@router.get("/revisions/{revision_id}/legacy", dependencies=[Depends(enabled)])
async def legacy_provenance(revision_id: str, response: Response,
        cursor: str | None = Query(default=None,max_length=36), limit: int = Query(default=50,ge=1,le=100),
        current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    from app.design.legacy import provenance
    response.headers["Cache-Control"]="private, no-store"
    return await provenance(db,current.id,revision_id,cursor=cursor,limit=limit)


@router.get("/designs/{design_id}/legacy", dependencies=[Depends(enabled)])
async def legacy_design_provenance(design_id: str, response: Response,
        cursor: str | None = Query(default=None,max_length=36), limit: int = Query(default=50,ge=1,le=100),
        current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    from app.design.legacy import provenance
    response.headers["Cache-Control"]="private, no-store"
    return await provenance(db,current.id,design_id=design_id,cursor=cursor,limit=limit)


from app.design.local_adoption import AdoptLocalImage, adopt_local

@router.post("/local-images/adopt", dependencies=[Depends(enabled)])
async def adopt_local_image(payload: AdoptLocalImage, response: Response,
                            current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    design, checkpoint, revision = await adopt_local(db, current.id, payload,
        storage_root=Path(get_settings().v2_storage_dir))
    response.headers["Cache-Control"] = "private, no-store"
    return {"design": design_json(design), "checkpoint": checkpoint_json(checkpoint),
            "revision": revision_json(revision)}

# Account-wide summaries do not expose full draft documents or private device IDs.
from app.design.draft_catalog import list_account_drafts

@router.get("/account/editor-drafts", dependencies=[Depends(enabled)])
async def account_editor_drafts(response: Response, cursor: str | None = Query(default=None, max_length=512),
                               limit: int = Query(default=30, ge=1, le=100),
                               current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    response.headers["Cache-Control"] = "private, no-store"
    return await list_account_drafts(db, current.id, cursor=cursor, limit=limit)

@router.get("/account/legacy-materials", dependencies=[Depends(enabled)])
async def legacy_material_list(response: Response, cursor: UUID | None = None,
                               limit: int = Query(default=50, ge=1, le=100),
                               kind: Literal["reference", "draft", "all"] = "reference",
                               current: User = Depends(user), db: AsyncSession = Depends(transaction)):
    from app.design.legacy_materials import list_materials
    response.headers["Cache-Control"] = "private, no-store"
    return await list_materials(db, current.id, cursor=str(cursor) if cursor else None, limit=limit, kind=kind)


@router.get("/account/legacy-materials/{material_id}/draft", dependencies=[Depends(enabled)])
async def legacy_material_draft(material_id: UUID, current: User = Depends(user),
                                db: AsyncSession = Depends(transaction)):
    from app.design.legacy_materials import preserved_draft
    from fastapi.responses import JSONResponse
    result = await preserved_draft(db, current.id, str(material_id))
    return JSONResponse(result, headers={
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'attachment; filename="legacy-draft-{material_id}.json"',
        "X-Content-Type-Options": "nosniff",
    })

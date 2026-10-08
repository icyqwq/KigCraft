"""Durable callbacks shared by the live worker and recovery tooling."""
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.time import utc_now
from app.conversation.public_text import public_assistant_text
from app.conversation.replies import message_parts, message_prefix
from app.design.models import Branch, CodexEpoch, Message, Operation, Revision
from app.design.service import ApplicationError, event
from app.operations.leases import check_fence, locked_operation
from app.operations.service import TERMINAL
from app.quota.wallet import release


async def turn_started(db: AsyncSession, operation_id: str, worker: str, token: int, turn_id: str):
    operation = await locked_operation(db, operation_id)
    check_fence(operation, worker, token)
    if operation.upstream_turn_id and operation.upstream_turn_id != turn_id:
        raise ApplicationError("turn_identity_conflict")
    operation.upstream_turn_id = turn_id


async def message_delta(db: AsyncSession, operation_id: str, worker: str, token: int,
                        turn_id: str, item: dict) -> Message | None:
    operation = await locked_operation(db, operation_id)
    check_fence(operation, worker, token)
    if operation.upstream_turn_id != turn_id:
        raise ApplicationError("turn_identity_conflict")
    if operation.state in TERMINAL or operation.upstream_status is not None:
        return None
    text = message_prefix(item["text"])
    if not text:
        return None
    previous = (await db.execute(select(Message).where(
        Message.operation_id == operation_id, Message.source_item_id == item["id"]
    ))).scalar_one_or_none()
    if previous and (previous.status != "streaming" or previous.source_turn_id != turn_id):
        return previous
    parts = [{"type": "text", "text": text}]
    if previous and previous.content_parts == parts:
        return previous
    message = previous or Message(branch_id=operation.branch_id, operation_id=operation_id, role="assistant",
        content_parts=parts, source_item_id=item["id"], source_turn_id=turn_id, status="streaming")
    if not previous:
        db.add(message)
        await db.flush()
        await event(db, operation.design_id, "message.created",
                    {"operation_id": operation_id, "message_id": message.id})
    message.content_parts = parts
    await db.flush()
    await event(db, operation.design_id, "message.delta",
                {"operation_id": operation_id, "message_id": message.id,
                 "branch_id": operation.branch_id, "text": text})
    return message


async def message_completed(db: AsyncSession, operation_id: str, worker: str, token: int,
                            turn_id: str, item: dict) -> Message | None:
    operation = await locked_operation(db, operation_id)
    check_fence(operation, worker, token)
    if operation.upstream_turn_id != turn_id:
        raise ApplicationError("turn_identity_conflict")
    previous = (await db.execute(select(Message).where(
        Message.operation_id == operation_id, Message.source_item_id == item["id"]
    ))).scalar_one_or_none()
    parts = message_parts(item)
    if previous and previous.status != "completed":
        if previous.source_turn_id != turn_id:
            raise ApplicationError("turn_identity_conflict")
        previous.content_parts = parts
        previous.status = "completed"
        await db.flush()
        await event(db, operation.design_id, "message.completed",
                    {"operation_id": operation_id, "message_id": previous.id})
        return previous
    if not parts:
        return None
    if previous:
        previous_parts = [{**part, "text": public_assistant_text(part["text"])}
                          if part.get("type") == "text" else part for part in previous.content_parts]
        previous_parts = [part for part in previous_parts if part.get("type") != "text" or part["text"]]
        if previous_parts != parts:
            raise ApplicationError("message_identity_conflict")
        return previous
    message = Message(branch_id=operation.branch_id, operation_id=operation_id, role="assistant",
                      content_parts=parts, source_item_id=item["id"], source_turn_id=turn_id)
    db.add(message)
    await db.flush()
    await event(db, operation.design_id, "message.completed",
                {"operation_id": operation_id, "message_id": message.id})
    return message


async def finish(db: AsyncSession, operation_id: str, worker: str, token: int, *,
                 upstream_status: str | None = None, error_code: str | None = None):
    operation = await locked_operation(db, operation_id)
    check_fence(operation, worker, token)
    if operation.state in TERMINAL:
        return operation.state
    if operation.dispatch_intent_at is None and operation.context_allocation and operation.context_allocation.get("state") == "sent":
        operation.context_allocation = {**operation.context_allocation, "state":"unknown"}
        error_code = "context_allocation_unknown"
    if upstream_status not in {None, "completed", "failed", "interrupted"}:
        raise ValueError("Unverified terminal status")
    if upstream_status:
        operation.upstream_status = upstream_status
        branch = await db.get(Branch, operation.branch_id)
        epoch = await db.get(CodexEpoch, branch.active_epoch_id) if branch.active_epoch_id else None
        if epoch and epoch.thread_id == operation.upstream_thread_id:
            epoch.checkpoint_turn_id = operation.upstream_turn_id
            epoch.status = "ready"
    delivered = set((await db.execute(select(Revision.delivery_key).where(
        Revision.operation_id == operation.id
    ))).scalars())
    from app.operations.publish import inline_delivered
    delivered |= await inline_delivered(db, operation)
    pending = set(operation.staged_outputs or {}) - delivered
    known = operation.upstream_status is not None or operation.dispatch_intent_at is None
    if not known:
        operation.state = "reconciling"
        operation.error_code = "upstream_status_unknown"
        branch = await db.get(Branch, operation.branch_id)
        if branch.active_epoch_id:
            (await db.get(CodexEpoch, branch.active_epoch_id)).status = "reconciling"
    elif pending and not operation.cancel_requested:
        operation.state = "publishing"
        operation.error_code = "publication_pending"
    else:
        await release(db, operation)
        if operation.cancel_requested or operation.upstream_status == "interrupted":
            operation.state = "partial_success" if delivered else "cancelled"
        elif delivered:
            operation.state = "succeeded" if operation.upstream_status == "completed" else "partial_success"
        elif error_code or operation.upstream_status == "failed":
            operation.state = "failed"
        elif operation.kind == "viewset":
            operation.state = "failed"
            error_code = "viewset_image_missing"
        else:
            from app.conversation.questions import completed_reply, open_question
            reply = await completed_reply(db, operation)
            if reply == "question":
                operation.state = "awaiting_input"
                if not await open_question(db, operation):
                    operation.state = "failed"
                    error_code = "clarification_unavailable"
            elif reply == "response" and operation.upstream_status == "completed":
                operation.state = "succeeded"
            else:
                operation.state = "failed"
                error_code = "reply_invalid" if reply == "invalid" else "clarification_unavailable"
        operation.error_code = error_code
        await event(db, operation.design_id, "quota.changed", {"operation_id": operation.id})
    if known:
        unfinished = (await db.execute(select(Message).where(
            Message.operation_id == operation_id, Message.status == "streaming"
        ))).scalars().all()
        for message in unfinished:
            # A terminal turn cannot promote an uncompleted item to a final answer.
            message.status = "interrupted"
            await event(db, operation.design_id, "message.completed",
                        {"operation_id": operation_id, "message_id": message.id})
    operation.updated_at = utc_now()
    operation.lease_owner = None
    operation.lease_expires_at = None
    await event(db, operation.design_id, "operation.state_changed",
                {"operation_id": operation.id, "state": operation.state})
    from app.operations.viewset_group import operation_finished
    await operation_finished(db, operation)
    return operation.state

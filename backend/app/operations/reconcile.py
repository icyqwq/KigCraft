"""Reconcile one unknown execution from authenticated persisted evidence; never dispatch."""
from __future__ import annotations
import logging
from app.codex_runtime.selection import execution_owner, SHARED_OWNER
import asyncio
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.codex_runtime.artifacts import ArtifactError, stage_image
from app.codex_runtime.history import read_turn
from app.codex_runtime.launch import DESIGN_MODEL
from app.core.time import utc_now
from app.design.models import Branch, CodexEpoch, Operation, Revision
from app.design.service import ApplicationError
from app.operations.leases import check_fence, locked_operation
from app.operations.lifecycle import finish, message_completed, turn_started
from app.operations.publish import publish
from app.operations.local_edit import process_local_output


async def reconcile(worker, operation_id: str) -> str:
    async with worker.factory.begin() as db:
        from app.admin.v2_controls import gate
        bound = execution_owner(worker)
        operation = await locked_operation(db, operation_id, None if bound == SHARED_OWNER else bound)
        await gate(db, operation.owner_id, recovery=True)
        if operation.state not in {"reconciling", "cancelling"} or not operation.upstream_thread_id:
            raise ApplicationError("reconciliation_unavailable")
        if operation.lease_expires_at and operation.lease_expires_at > utc_now():
            raise ApplicationError("operation_busy")
        branch = await db.get(Branch, operation.branch_id)
        epoch = await db.get(CodexEpoch, branch.active_epoch_id) if branch.active_epoch_id else None
        if (epoch is None or epoch.thread_id != operation.upstream_thread_id or epoch.model != DESIGN_MODEL
                or epoch.codex_version != worker.runtime.version):
            raise ApplicationError("runtime_binding_unavailable")
        operation.fencing_token += 1
        token = operation.fencing_token
        operation.lease_owner = worker.worker_id
        operation.lease_expires_at = utc_now() + timedelta(seconds=90)
        thread_id, turn_id, identity = operation.upstream_thread_id, operation.upstream_turn_id, epoch.runtime_identity
    workspace = worker.root / operation_id
    status, error = None, None
    try:
        async with worker.runtime.connection(workspace, images=False) as rpc:
            if await worker.runtime.identity(rpc) != identity:
                raise ApplicationError("runtime_identity_changed")
            result = await read_turn(rpc, thread_id=thread_id, turn_id=turn_id, client_request_id=operation_id)
        async with worker.factory.begin() as db:
            await turn_started(db, operation_id, worker.worker_id, token, result.turn_id)
            operation = await db.get(Operation, operation_id)
            cancelled = operation.cancel_requested
            delivered = (await db.execute(select(Revision.id).where(
                Revision.operation_id == operation_id, Revision.delivery_key == "main_image"
            ))).scalar_one_or_none() is not None
            from app.operations.publish import inline_delivered
            delivered = delivered or "main_image" in await inline_delivered(db, operation)
            if not cancelled:
                operation.state = "publishing"
        for item in result.messages:
            async with worker.factory.begin() as db:
                await message_completed(db, operation_id, worker.worker_id, token, result.turn_id, item)
        # A committed delivery is authoritative; recovery must not depend on expired
        # temporary image output or reinterpret extra upstream images as new deliveries.
        if not cancelled and not delivered:
            for item in result.images:
                if item.get("status") != "completed" or item.get("failure"):
                    error = "image_generation_failed"
                    continue
                try:
                    staged = await asyncio.to_thread(stage_image, item, runtime_id=identity,
                        thread_id=thread_id, turn_id=result.turn_id, output_root=workspace,
                        staging_root=workspace / "staging")
                    async with worker.factory() as db:
                        operation = await db.get(Operation, operation_id)
                        staged = await process_local_output(db, operation, staged, worker.storage, workspace / "staging")
                        if operation.kind == "viewset":
                            from app.operations.viewset_group import check_view_image
                            await check_view_image(db, operation, staged.sha256)
                except (ArtifactError, ApplicationError) as failure:
                    # read_turn already confirmed a complete terminal history, so invalid output is a known failure.
                    error = str(failure) if str(failure).startswith(("local_", "viewset_")) else "image_generation_failed"
                    continue
                descriptor = {"filename": staged.path.name, "delivery_key": staged.delivery_key,
                    "sha256": staged.sha256, "width": staged.width, "height": staged.height,
                    "source_sha256": staged.source_sha256,
                    **({"local_input_digest": staged.local_input_digest} if staged.local_input_digest else {})}
                async with worker.factory.begin() as db:
                    operation = await locked_operation(db, operation_id)
                    check_fence(operation, worker.worker_id, token)
                    if operation.cancel_requested:
                        break
                    outputs = operation.staged_outputs or {}
                    if outputs.get("main_image") and outputs["main_image"] != descriptor:
                        # Preserve the single quoted delivery even if upstream produced extras.
                        error = "image_generation_failed"
                        continue
                    operation.staged_outputs = {**outputs, "main_image": descriptor}
                try:
                    async with worker.factory.begin() as db:
                        await publish(db, operation_id, worker.worker_id, token, "main_image",
                            staged, storage_root=worker.storage, finalize=False)
                except (OSError, SQLAlchemyError):
                    # Descriptor is durable; finish moves it to publication retry.
                    pass
                except ApplicationError as exc:
                    if exc.code != "operation_not_publishable":
                        raise
        status = result.status
    except asyncio.CancelledError:
        raise
    except Exception:
        # No negative inference from missing history, changed identity, or inaccessible output.
        error = "upstream_status_unknown"
        # Operator log only (never stored or shown): the reason the turn could not be confirmed.
        logging.getLogger(__name__).warning("reconcile of operation %s unresolved", operation_id, exc_info=True)
    finally:
        async with worker.factory.begin() as db:
            state = await finish(db, operation_id, worker.worker_id, token,
                                 upstream_status=status, error_code=error)
    return state

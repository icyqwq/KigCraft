"""Account-bound Worker CLI; local test or provisioned container runtime, no hidden retries."""
from __future__ import annotations
import logging
from app.operations.local_edit import process_local_output
from app.operations.observations import observer
import argparse
import asyncio
import contextlib
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from app.codex_runtime.artifacts import ArtifactError, StagedImage, stage_image
from app.codex_runtime.launch import DESIGN_MODEL
from app.codex_runtime.selection import make_runtime, worker_owner, execution_owner, owner_clause, serves, SHARED_OWNER
from app.codex_runtime.session import CodexSession
from app.conversation.prepare import prepare
from app.conversation.style_references import style_inputs
from app.conversation.replies import REPLY_SCHEMA

log = logging.getLogger(__name__)
from app.conversation.fork import allocate
from app.core.config import Settings, get_settings
from app.core.time import utc_now
from app.db.session import get_sessionmaker
from app.db.v2_schema import require_v2_schema
from app.design.models import Branch, CodexEpoch, Operation
from app.design.service import ApplicationError, event
from app.moderation.reviewer import ModerationUnavailable, review
from app.moderation.schemas import CombinedReviewDecision
from app.moderation.cache import policy_key, cached_assets, missing_assets, save_assets
from app.operations.leases import claim, check_fence, heartbeat, locked_operation, record_dispatch_intent, recover_expired
from app.operations.lifecycle import finish, message_completed, message_delta, turn_started
from app.operations.publish import publish

class LocalWorker:
    def __init__(self, factory, settings: Settings, *, runtime=None, worker_id: str | None = None):
        self.factory = factory
        self.settings = settings
        self.owner_id = worker_owner(settings)
        if not self.owner_id:
            raise ApplicationError("runtime_not_configured")
        self.runtime = runtime or make_runtime(settings)
        self.worker_id = worker_id or f"local-{uuid4()}"
        from app.assets.repository import asset_store
        self.storage = asset_store(settings=settings)
        self.root = (self.runtime.work_root if settings.v2_runtime_mode == "container" else Path(settings.v2_runtime_dir)).resolve()
        self.session: CodexSession | None = None
        self._interrupt_sent = False

    async def recover_expired(self):
        if worker_owner(self.settings) != self.owner_id:
            return
        async with self.factory() as db:
            ids = (await db.execute(select(Operation.id).where(
                owner_clause(Operation.owner_id, self.owner_id),
                Operation.lease_expires_at <= utc_now(),
                Operation.state.in_(["reviewing", "running", "publishing", "cancelling"])
            ))).scalars().all()
        for operation_id in ids:
            async with self.factory.begin() as db:
                await recover_expired(db, operation_id)

    async def run_once(self) -> str | None:
        if worker_owner(self.settings) != self.owner_id:
            return None
        async with self.factory.begin() as db:
            operation = await claim(db, self.worker_id, owner_id=None if self.owner_id == SHARED_OWNER else self.owner_id)
            if not operation:
                return None
            operation_id, token = operation.id, operation.fencing_token
        self.session = None
        self._interrupt_sent = False
        execution = asyncio.create_task(self._execute(operation_id, token))
        maintenance = asyncio.create_task(self._maintain(operation_id, token))
        status, error = None, None
        cancelled = False
        try:
            done, _ = await asyncio.wait([execution, maintenance], return_when=asyncio.FIRST_COMPLETED)
            if maintenance in done:
                await maintenance
            status, error = await execution
        except asyncio.CancelledError:
            cancelled = True
            error = "worker_stopped"
        except ModerationUnavailable:
            error = "moderation_unavailable"
        except ApplicationError as exc:
            error = exc.code
        except Exception:
            # Never place upstream errors, paths, credentials or request content in public events/logs.
            error = "runtime_unavailable"
        finally:
            for task in (execution, maintenance):
                if not task.done():
                    task.cancel()
            await asyncio.gather(execution, maintenance, return_exceptions=True)
            self.session = None
        try:
            async with self.factory.begin() as db:
                await finish(db, operation_id, self.worker_id, token, upstream_status=status, error_code=error)
        except ApplicationError as exc:
            if exc.code != "stale_lease":
                raise
            # The new fencing owner is responsible; old workers must not mutate its state.
        if cancelled:
            raise asyncio.CancelledError()
        return operation_id

    async def _maintain(self, operation_id: str, token: int):
        while True:
            await asyncio.sleep(5)
            async with self.factory.begin() as db:
                await heartbeat(db, operation_id, self.worker_id, token)
                operation = await db.get(Operation, operation_id)
                requested = operation.cancel_requested
            if requested:
                await self._interrupt()

    async def _interrupt(self):
        session = self.session
        if session and session.active_turn_id and not self._interrupt_sent:
            self._interrupt_sent = True
            await session.rpc.request("turn/interrupt", {
                "threadId": session.binding.thread_id, "turnId": session.active_turn_id,
            })

    async def _execute(self, operation_id: str, token: int) -> tuple[str | None, str | None]:
        execution_owner(self)
        if self.settings.v2_runtime_mode == "container":
            async with self.runtime.operation():
                return await self._execute_operation(operation_id, token)
        return await self._execute_operation(operation_id, token)

    async def _execute_operation(self, operation_id: str, token: int) -> tuple[str | None, str | None]:
        workspace = (self.runtime.workspace(operation_id) if self.settings.v2_runtime_mode == "container"
                     else self.root / operation_id)
        workspace.mkdir(parents=True, exist_ok=True)
        async with self.factory() as db:
            operation = await db.get(Operation, operation_id)
            if not serves(self.owner_id, operation.owner_id):
                raise ApplicationError("local_test_account_required")
            operation_owner = operation.owner_id
            if operation.kind not in {"generate", "image_edit", "regenerate", "local_edit", "viewset"}:
                raise ApplicationError("operation_kind_not_enabled")
            if operation.kind == "viewset" and not self.settings.v2_viewsets_enabled:
                raise ApplicationError("operation_kind_not_enabled")
            operation_kind = operation.kind
            allocation = operation.context_allocation
            if allocation and allocation.get("state") in {"sent", "unknown"}:
                raise ApplicationError("context_allocation_unknown")
            snapshot, inputs = await prepare(db, operation,
                workspace=(self.runtime.input_workspace(operation_id) if self.settings.v2_runtime_mode == "container" else workspace),
                storage_root=self.storage,
                max_images=self.settings.v2_max_input_images, max_bytes=self.settings.v2_max_input_bytes)
            if (snapshot.local_edit_mode or operation.kind == "local_edit") and not self.settings.v2_local_edit_enabled:
                raise ApplicationError("operation_kind_not_enabled")
            branch = await db.get(Branch, operation.branch_id)
            previous_epoch = await db.get(CodexEpoch, branch.active_epoch_id) if branch.active_epoch_id else None
            if operation.source_operation_id:
                source = await db.get(Operation, operation.source_operation_id)
                if source and source.question and source.question.get("answered_by") == operation.id:
                    from app.conversation.checkpoints import checkpoint
                    if (not previous_epoch or previous_epoch.status != "ready"
                            or checkpoint(previous_epoch, previous_epoch.checkpoint_turn_id) != operation.context_before
                            or operation.context_before != source.question.get("checkpoint")):
                        raise ApplicationError("question_expired")
        from app.admin.v2_controls import gate
        async with self.factory.begin() as db:
            await gate(db,operation_owner)
        async with self.runtime.connection(workspace / "review", images=False) as rpc:
            reviewer_identity = await self.runtime.identity(rpc)
            policy = policy_key(model=self.settings.v2_review_model,
                codex_version=self.runtime.version, runtime_identity=reviewer_identity)
            async with self.factory() as db:
                cached = await cached_assets(db, operation.owner_id, snapshot, policy)
            missing = missing_assets(snapshot, cached)
            decision = await review(rpc, review_model=self.settings.v2_review_model,
                user_text=snapshot.user_text, references=snapshot.working_context()["references"] + snapshot.annotation_review(),
                image_inputs=[i for i in inputs if i["type"] == "localImage"],
                on_observation=observer(self,operation_id,token,"review",self.settings.v2_review_model),
                asset_checks=list(missing),
                cached_decisions=[{"ordinal":ref.ordinal,"verdict":cached[ref.sha256]}
                                  for ref in snapshot.references if ref.sha256 in cached])
            if isinstance(decision, CombinedReviewDecision):
                async with self.factory.begin() as db:
                    current = await locked_operation(db, operation_id)
                    check_fence(current, self.worker_id, token)
                    await save_assets(db, owner=current.owner_id, operation_id=operation_id,
                                      policy=policy, missing=missing, decision=decision)
        if decision.verdict != "allow":
            return None, "prompt_injection_rejected" if decision.category == "injection" else "moderation_rejected"
        async with self.factory.begin() as db:
            await gate(db,operation_owner)
        async with self.runtime.connection(workspace, images=True) as rpc:
            runtime_identity = await self.runtime.identity(rpc)
            if runtime_identity != reviewer_identity:
                raise ApplicationError("runtime_identity_changed")
            if previous_epoch and (previous_epoch.runtime_identity != runtime_identity
                    or previous_epoch.model != DESIGN_MODEL or previous_epoch.status != "ready"):
                raise ApplicationError("runtime_binding_unavailable")
            if allocation and allocation.get("state") == "pending":
                session, previous_epoch = await allocate(self, rpc, operation_id, token, runtime_identity)
            else:
                from app.operations.context_observations import open_context
                session = await open_context(self, rpc, operation_id, token, thread_id=previous_epoch.thread_id if previous_epoch else None)
            self.session = session
            async with self.factory.begin() as db:
                operation = await locked_operation(db, operation_id)
                check_fence(operation, self.worker_id, token)
                branch = await db.get(Branch, operation.branch_id)
                if branch.active_epoch_id != (previous_epoch.id if previous_epoch else None):
                    raise ApplicationError("runtime_binding_changed")
                if previous_epoch:
                    epoch = await db.get(CodexEpoch, previous_epoch.id)
                else:
                    epoch = CodexEpoch(branch_id=branch.id, thread_id=session.binding.thread_id,
                        runtime_identity=runtime_identity, model=DESIGN_MODEL, codex_version=self.runtime.version)
                    db.add(epoch)
                    await db.flush()
                    branch.active_epoch_id = epoch.id
                epoch.status = "busy"
                await record_dispatch_intent(db, operation_id, self.worker_id, token, session.binding.thread_id)
            image_failed = False
            image_failure_code = None
            # The image is staged durably as it arrives but published only after the turn ends: the version
            # row is immutable and its image_kind comes from the final structured reply, which arrives last.
            staged_for_publish: StagedImage | None = None

            async def started(turn_id: str):
                async with self.factory.begin() as db:
                    await turn_started(db, operation_id, self.worker_id, token, turn_id)
                    requested = (await db.get(Operation, operation_id)).cancel_requested
                if requested:
                    await self._interrupt()

            async def delta(turn_id: str, item: dict):
                async with self.factory.begin() as db:
                    await message_delta(db, operation_id, self.worker_id, token, turn_id, item)

            async def message(turn_id: str, item: dict):
                async with self.factory.begin() as db:
                    await message_completed(db, operation_id, self.worker_id, token, turn_id, item)

            async def image(turn_id: str, item: dict):
                nonlocal image_failed, image_failure_code, staged_for_publish
                if item.get("status") != "completed" or item.get("failure"):
                    image_failed = True
                    return
                try:
                    staged = await asyncio.to_thread(stage_image, item,
                        runtime_id=runtime_identity, thread_id=session.binding.thread_id, turn_id=turn_id,
                        output_root=workspace, staging_root=workspace / "staging")
                    if snapshot.local_edit_mode == "lock_outside" or snapshot.inline_local:
                        async with self.factory() as db:
                            operation = await db.get(Operation, operation_id)
                            staged = await process_local_output(db, operation, staged, self.storage, workspace / "staging")
                    if operation_kind == "viewset":
                        from app.operations.viewset_group import check_view_image
                        async with self.factory() as db:
                            view_operation = await db.get(Operation, operation_id)
                            await check_view_image(db, view_operation, staged.sha256)
                except (ArtifactError, ApplicationError) as failure:
                    image_failure_code = str(failure) if str(failure).startswith(("local_", "viewset_")) else "image_generation_failed"
                    # Reject invalid bytes while still consuming the verified terminal event.
                    image_failed = True
                    return
                descriptor = {"filename": staged.path.name, "delivery_key": staged.delivery_key,
                    "sha256": staged.sha256, "width": staged.width, "height": staged.height,
                    "source_sha256": staged.source_sha256,
                    **({"local_input_digest": staged.local_input_digest} if staged.local_input_digest else {})}
                async with self.factory.begin() as db:
                    operation = await locked_operation(db, operation_id)
                    check_fence(operation, self.worker_id, token)
                    if operation.cancel_requested:
                        return
                    outputs = operation.staged_outputs or {}
                    if outputs.get("main_image") and outputs["main_image"] != descriptor:
                        # One quoted delivery. Extra outputs cannot overwrite it or settle extra quota.
                        image_failed = True
                        return
                    operation.staged_outputs = {**outputs, "main_image": descriptor}
                staged_for_publish = staged

            async def publish_staged():
                if staged_for_publish is None:
                    return
                try:
                    async with self.factory.begin() as db:
                        await publish(db, operation_id, self.worker_id, token, "main_image",
                                      staged_for_publish, storage_root=self.storage, finalize=False)
                except (OSError, SQLAlchemyError):
                    # The durable descriptor survives a failed object/DB publish for explicit retry.
                    return
                except ApplicationError as exc:
                    if exc.code != "operation_not_publishable":
                        raise

            try:
                result = await session.run([*inputs, *style_inputs(snapshot, operation_kind, workspace)],
                    client_request_id=operation_id, output_schema=REPLY_SCHEMA,
                    on_started=started, on_message=message, on_message_delta=delta, on_image=image,
                    on_observation=observer(self,operation_id,token,"generation",DESIGN_MODEL),
                    timeout=self.settings.codex_generation_timeout_seconds)
            except BaseException:
                # A failed turn still delivers the image it already produced (best effort; the descriptor is durable).
                with contextlib.suppress(Exception):
                    await publish_staged()
                raise
            await publish_staged()
            if result.status == "failed":
                # The reason stays in the server log; the operation carries a stable public code instead of none.
                log.warning("codex turn failed for operation %s (model %s): %s", operation_id, DESIGN_MODEL, result.error or "no detail from upstream")
                return result.status, image_failure_code or "image_generation_failed"
            return result.status, (image_failure_code or "image_generation_failed") if image_failed else None

    async def reconcile(self, operation_id: str) -> str:
        from app.operations.reconcile import reconcile
        return await reconcile(self, operation_id)

    async def retry_publish(self, operation_id: str) -> str:
        """Explicit recovery of existing bytes only; this method never opens a model connection."""
        execution_owner(self)
        async with self.factory.begin() as db:
            operation = await locked_operation(db, operation_id, None if self.owner_id == SHARED_OWNER else self.owner_id)
            if operation.state != "publishing" or operation.upstream_status is None or operation.cancel_requested:
                raise ApplicationError("publication_retry_unavailable")
            if operation.lease_expires_at and operation.lease_expires_at > utc_now():
                raise ApplicationError("operation_busy")
            operation.fencing_token += 1
            token = operation.fencing_token
            operation.lease_owner = self.worker_id
            operation.lease_expires_at = utc_now() + timedelta(seconds=90)
            descriptors = operation.staged_outputs or {}
        try:
            for key, descriptor in descriptors.items():
                filename = descriptor["filename"]
                if filename != Path(filename).name or filename not in {descriptor["delivery_key"] + ".png", descriptor["delivery_key"] + ".jpg"}:
                    raise ApplicationError("invalid_staging_descriptor")
                path = self.root / operation_id / "staging" / filename
                if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                    raise ApplicationError("invalid_staging_descriptor")
                staged = StagedImage(descriptor["delivery_key"], path, descriptor["sha256"],
                                     descriptor["width"], descriptor["height"], descriptor["source_sha256"], descriptor.get("local_input_digest"))
                async with self.factory.begin() as db:
                    await publish(db, operation_id, self.worker_id, token, key,
                                  staged, storage_root=self.storage, finalize=False)
        finally:
            async with self.factory.begin() as db:
                state = await finish(db, operation_id, self.worker_id, token)
        return state


async def run(args):
    settings = get_settings()
    if getattr(args, "owner_id", None) is not None:
        from app.codex_runtime.selection import validate_account_service
        validate_account_service(settings, args.owner_id)
    if not settings.v2_enabled:
        raise ValueError("V2 must be enabled explicitly")
    await require_v2_schema()
    worker = LocalWorker(get_sessionmaker(), settings)
    if args.retry_publish:
        print(await worker.retry_publish(args.retry_publish))
        return
    await worker.runtime.verify()
    if args.reconcile:
        print(await worker.reconcile(args.reconcile))
        return
    while True:
        await worker.recover_expired()
        from app.admin.v2_service import run_recovery
        recovered = await run_recovery(worker)
        if recovered:
            print(f"Recovery request {recovered}", flush=True)
        from app.operations.automatic_recovery import run_automatic_recovery
        checked = await run_automatic_recovery(worker)
        if checked:
            print(f"Automatic recovery check {checked}", flush=True)
        if settings.v2_new_operations:
            operation_id = await worker.run_once()
            if operation_id:
                print(f"Processed operation {operation_id}", flush=True)
        if args.once:
            return
        await asyncio.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--owner-id", help="Bind production service instance to this account UUID")
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument("--retry-publish", metavar="OPERATION_ID")
    recovery.add_argument("--reconcile", metavar="OPERATION_ID")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""Resolve one quoted operation from current or immutable historical input."""
from __future__ import annotations
from dataclasses import dataclass
from sqlalchemy import select
from app.contracts.v2 import InputSnapshot, SubmitTurn
from app.conversation.checkpoints import ContextBoundary
from app.design.continuation import continuation_plan
from app.design.models import Asset, Branch, Operation, Revision
from app.design.service import ApplicationError, digest
from app.operations.regenerate import regeneration_plan

@dataclass(frozen=True)
class OperationPlan:
    kind: str
    inputs: InputSnapshot
    boundary: ContextBoundary | None = None
    parent_branch_id: str | None = None
    origin_revision_id: str | None = None
    original_input_id: str | None = None
    source_operation_id: str | None = None
    boundary_message_id: str | None = None
    answer_checkpoint: dict | None = None
    answer_version: int | None = None

    def checkpoint(self):
        if self.boundary is None:
            return self.answer_checkpoint
        if self.boundary.turn_id is None:
            return {"kind": "fresh"}
        return {"kind": "turn", "epoch_id": self.boundary.epoch_id, "turn_id": self.boundary.turn_id}

    def scope(self, branch_id: str, request: SubmitTurn) -> str:
        value = {"kind":self.kind, "input_digest":self.inputs.digest(), "branch_id":branch_id,
            "branch_version":request.expected_branch_version, "checkpoint":self.checkpoint(),
            "origin_revision_id":self.origin_revision_id, "source_operation_id":self.source_operation_id}
        if self.answer_version:
            value["answer_version"] = self.answer_version
        if self.boundary_message_id:
            value["boundary_message_id"] = self.boundary_message_id
        return digest(value)


async def plan(db, owner: str, branch: Branch, request: SubmitTurn) -> OperationPlan:
    from app.operations.service import kind_for, snapshot
    if request.answer_to:
        from app.conversation.questions import require_answer
        operation,value,inputs=await require_answer(db,owner,branch,request)
        kind="local_edit" if inputs.local_edit_mode else "image_edit" if inputs.base_revision_id else "generate"
        return OperationPlan(kind,inputs,source_operation_id=operation.id,
                             answer_checkpoint=value.checkpoint,answer_version=value.version)
    if request.inline_local:
        return await inline_plan(db, owner, branch, request)
    if branch.state_version != request.expected_branch_version:
        raise ApplicationError("state_conflict")
    if request.regenerate_revision_id:
        original = await regeneration_plan(db, owner, request.regenerate_revision_id)
        if original.design_id != branch.design_id:
            raise ApplicationError("not_found")
        source = await db.get(Operation, original.source_operation_id)
        # Recheck original references on quote/submit, before any reservation.
        for ref in original.snapshot.references:
            asset = (await db.execute(select(Asset).where(
                Asset.id == ref.asset_id, Asset.owner_id == owner, Asset.status == "ready",
                Asset.sha256 == ref.sha256))).scalar_one_or_none()
            if asset is None:
                raise ApplicationError("input_asset_unavailable")
        return OperationPlan("regenerate", original.snapshot, original.boundary,
            source.branch_id, request.regenerate_revision_id, original.input_snapshot_id,
            source.source_operation_id or source.id)
    boundary, parent, origin = None, None, None
    if request.continuation:
        draft = request.continuation
        if draft.boundary_message_id:
            from app.operations.message_edit import prepare_message_edit, edited_inputs
            original = await prepare_message_edit(db, owner, branch.id, draft.boundary_message_id)
            inputs = await edited_inputs(db, owner, original, request)
            kind = "discussion" if request.intent == "discussion_only" else "local_edit" if inputs.local_edit_mode else "image_edit" if inputs.base_revision_id else "generate"
            return OperationPlan(kind, inputs, original.boundary, original.message.branch_id,
                inputs.base_revision_id, source_operation_id=original.operation.id,
                boundary_message_id=original.message.id)
        if draft.source_revision_id != request.base_revision_id:
            raise ApplicationError("state_conflict")
        prepared = await continuation_plan(db, owner, branch.id, draft.source_revision_id,
                                           request.expected_branch_version)
        if request.base_revision_id != branch.current_revision_id:
            revision = await db.get(Revision, draft.source_revision_id)
            boundary, parent, origin = prepared.boundary, revision.branch_id, revision.id
    inputs = await snapshot(db, owner, branch, request, historical=boundary is not None)
    return OperationPlan(kind_for(request), inputs, boundary, parent, origin)


async def inline_plan(db, owner: str, branch: Branch, request: SubmitTurn) -> OperationPlan:
    """The editor's local generation: a fresh model session in its own throw-away branch, forked from the edited image.

    The branch the user converses in is never touched: no message, no version, and the main thread's context is not reused.
    """
    from app.operations.service import snapshot
    origin = await db.get(Revision, request.base_revision_id)
    if origin is None or origin.design_id != branch.design_id:
        raise ApplicationError("not_found")
    inputs = await snapshot(db, owner, branch, request, historical=True)
    return OperationPlan("local_edit", inputs, ContextBoundary(None, None, None, None, None), origin.branch_id, origin.id)

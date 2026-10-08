"""Natural-language questions from completed turns; never replay a server RPC request."""
from datetime import datetime,timedelta
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from app.core.time import utc_now
from app.contracts.v2 import InputSnapshot
from app.design.models import Branch,CodexEpoch,InputRecord,Message,Operation
from app.design.service import ApplicationError,event,owned_branch
from app.conversation.public_text import public_assistant_text
from app.conversation.checkpoints import checkpoint
from app.conversation.replies import message_kind

class SavedQuestion(BaseModel):
    model_config=ConfigDict(extra="forbid")
    version:int=Field(ge=1)
    branch_version:int
    base_revision_id:str|None
    checkpoint:dict
    history_end:int
    messages:list[dict]
    expires_at:datetime
    answered_by:str|None=None

def saved(operation):
    try:
        return SavedQuestion.model_validate(operation.question)
    except ValueError:
        raise ApplicationError("question_unavailable") from None

async def completed_messages(db,operation):
    return (await db.execute(select(Message).where(Message.operation_id==operation.id,
        Message.source_turn_id==operation.upstream_turn_id,
        Message.role=="assistant",Message.status=="completed").order_by(Message.history_sequence))).scalars().all()

async def completed_reply(db,operation):
    for row in reversed(await completed_messages(db,operation)):
        kind=message_kind(row.content_parts)
        if kind is not None:
            return kind
    return None

async def open_question(db,operation):
    branch=await db.get(Branch,operation.branch_id)
    epoch=await db.get(CodexEpoch,branch.active_epoch_id) if branch.active_epoch_id else None
    if (operation.upstream_status!="completed" or not epoch or epoch.status!="ready"
        or epoch.thread_id!=operation.upstream_thread_id or epoch.checkpoint_turn_id!=operation.upstream_turn_id
        or branch.state_version!=operation.expected_branch_version or branch.current_revision_id!=operation.base_revision_id):
        return False
    rows=await completed_messages(db,operation)
    if await completed_reply(db,operation)!="question":
        return False
    messages=[]
    # The final structured question alone is the answer target, not earlier commentary.
    for row in [row for row in rows if message_kind(row.content_parts) is not None][-1:]:
        if message_kind(row.content_parts)!="question":
            continue
        text="\n\n".join(public_assistant_text(p["text"]) for p in row.content_parts if p.get("type")=="text")
        if text.strip():
            messages.append({"message_id":row.id,"text":text})
    if not messages:
        return False
    from app.core.config import get_settings
    from app.design.models import Design
    history_end=await db.scalar(select(Design.history_counter).where(Design.id==operation.design_id))
    value=SavedQuestion(version=1,branch_version=branch.state_version,base_revision_id=branch.current_revision_id,
        checkpoint=checkpoint(epoch,operation.upstream_turn_id),history_end=history_end,messages=messages,
        expires_at=utc_now()+timedelta(seconds=get_settings().v2_question_ttl_seconds))
    operation.question=value.model_dump(mode="json")
    await event(db,operation.design_id,"question.opened",{"operation_id":operation.id,"version":value.version})
    return True

async def question_state(db,operation,value=None):
    value=value or saved(operation)
    if value.answered_by:
        return "answered"
    branch=await db.get(Branch,operation.branch_id)
    epoch=await db.get(CodexEpoch,branch.active_epoch_id) if branch and branch.active_epoch_id else None
    if (value.expires_at<=utc_now() or operation.state!="awaiting_input" or operation.upstream_status!="completed"
        or not operation.reservation_closed or not branch or branch.state_version!=value.branch_version
        or branch.current_revision_id!=value.base_revision_id or not epoch or epoch.status!="ready"
        or checkpoint(epoch,epoch.checkpoint_turn_id)!=value.checkpoint or epoch.thread_id!=operation.upstream_thread_id):
        return "expired"
    later=await db.scalar(select(Message.id).where(Message.branch_id==branch.id,Message.role=="user",
        Message.history_sequence>value.history_end).limit(1))
    return "expired" if later else "open"

async def question_json(db,operation):
    value=saved(operation)
    return {"operation_id":operation.id,"version":value.version,"state":await question_state(db,operation,value),
        "expires_at":value.expires_at,"messages":value.messages,"answered_by":value.answered_by}

async def original_inputs(db,operation):
    record=await db.get(InputRecord,operation.input_snapshot_id)
    if record is None:
        raise ApplicationError("input_snapshot_missing")
    try:
        inputs=InputSnapshot.model_validate(record.snapshot)
    except ValueError:
        raise ApplicationError("input_snapshot_changed") from None
    if inputs.digest()!=record.digest:
        raise ApplicationError("input_snapshot_changed")
    return inputs

async def require_answer(db,owner,branch,request):
    binding=request.answer_to
    operation=await db.get(Operation,binding.operation_id,populate_existing=True)
    if not operation or operation.owner_id!=owner or operation.branch_id!=branch.id or not operation.question:
        raise ApplicationError("not_found")
    value=saved(operation)
    if binding.version!=value.version:
        raise ApplicationError("question_version_conflict")
    if await question_state(db,operation,value)!="open":
        raise ApplicationError("question_expired")
    if request.base_revision_id!=value.base_revision_id or request.expected_branch_version!=value.branch_version:
        raise ApplicationError("question_expired")
    source=await original_inputs(db,operation)
    from app.operations.message_edit import replace_input_text_references
    inputs=await replace_input_text_references(db,owner,source,request)
    return operation,value,inputs

async def resolve_question(db,operation_id,answer_id):
    original=await db.get(Operation,operation_id)
    value=saved(original)
    original.question=value.model_copy(update={"answered_by":answer_id}).model_dump(mode="json")
    await event(db,original.design_id,"question.resolved",
                {"operation_id":original.id,"version":value.version,"answered_by":answer_id})

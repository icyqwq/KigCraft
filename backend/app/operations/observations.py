"""Fenced private observations. No wallet changes or user-facing SSE."""
from sqlalchemy import select, func
from app.codex_runtime.usage import token_snapshot
from app.core.time import utc_now
from app.design.models import RuntimeObservation, Operation
from app.design.service import ApplicationError
from app.operations.leases import locked_operation, check_fence

TERMINAL = {"completed", "interrupted", "failed"}

def observer(worker, operation_id, token, phase, model):
    async def receive(value):
        async with worker.factory.begin() as db:
            operation = await locked_operation(db, operation_id)
            check_fence(operation, worker.worker_id, token)
            await record(db, operation_id, token, phase, model, worker.runtime.version, value)
    return receive

async def record(db, operation_id, token, phase, model, version, value):
    row = await db.scalar(select(RuntimeObservation).where(
        RuntimeObservation.operation_id==operation_id, RuntimeObservation.phase==phase,
        RuntimeObservation.fencing_token==token).with_for_update())
    kind = value["kind"]
    thread = value["thread_id"]
    if not isinstance(thread,str) or not 0<len(thread)<=100:
        raise ApplicationError("invalid_observation_identity")
    if row is None:
        if kind!="dispatch":
            raise ApplicationError("observation_not_started")
        row = RuntimeObservation(operation_id=operation_id,phase=phase,fencing_token=token,
            model=model,codex_version=version,state="dispatch_intent",thread_id=thread,
            token_status="unknown",image_items={},image_anomalies={"replayed":[],"conflicting":[]})
        db.add(row)
        await db.flush()
        return
    if (row.thread_id,row.model,row.codex_version)!=(thread,model,version):
        raise ApplicationError("observation_identity_conflict")
    if kind=="dispatch":
        return
    turn = value["turn_id"]
    if not isinstance(turn,str) or not 0<len(turn)<=100 or row.turn_id not in {None,turn}:
        raise ApplicationError("observation_identity_conflict")
    if row.turn_id is None:
        if kind!="started":
            raise ApplicationError("observation_not_started")
        row.turn_id=turn
    if row.state in TERMINAL:
        if kind=="completed" and value["status"]==row.state:
            return
        raise ApplicationError("observation_already_completed")
    if kind=="started":
        row.state="started"
    elif kind=="usage":
        usage=token_snapshot(value.get("token_usage"))
        row.token_status=usage["status"];row.token_snapshot=usage["snapshot"]
    elif kind=="image":
        identity=value["item_id"]
        if not isinstance(identity,str) or not 0<len(identity)<=200:
            raise ApplicationError("invalid_observation_identity")
        status=value["status"]
        if status not in {"completed","failed","unknown"}:
            raise ApplicationError("invalid_observation_state")
        items=dict(row.image_items)
        if identity in items and items[identity]!=status:
            raise ApplicationError("observation_identity_conflict")
        if len(items)>=10000 and identity not in items:
            raise ApplicationError("observation_limit")
        items[identity]=status;row.image_items=items
    elif kind=="image_duplicate":
        identity = value["item_id"]
        if identity not in row.image_items or type(value.get("conflict")) is not bool:
            raise ApplicationError("observation_identity_conflict")
        if row.image_anomalies is None:
            raise ApplicationError("anomaly_observation_unavailable")
        anomalies = {key:list(ids) for key,ids in row.image_anomalies.items()}
        key = "conflicting" if value["conflict"] else "replayed"
        if identity not in anomalies[key]:
            anomalies[key].append(identity)
        row.image_anomalies = anomalies
    elif kind=="completed":
        if value["status"] not in TERMINAL:
            raise ApplicationError("invalid_observation_state")
        row.state=value["status"]
    else:
        raise ApplicationError("invalid_observation_kind")
    row.updated_at=utc_now()
    await db.flush()

def image_anomalies(row):
    if row.image_anomalies is None:
        return None
    return {"replayed_items":len(row.image_anomalies["replayed"]),
            "conflicting_items":len(row.image_anomalies["conflicting"]),
            "extra_completed_items":max(0,sum(value=="completed" for value in row.image_items.values())-1)}


async def summary(db, operation_id):
    rows=list((await db.scalars(select(RuntimeObservation).where(
        RuntimeObservation.operation_id==operation_id).order_by(RuntimeObservation.created_at,RuntimeObservation.id))).all())
    return {"status":"observed" if rows else "unknown",
        "turn_tokens":None, "provider_model_requests":None, "amount":None,
        "attempts":[{"phase":r.phase,"requested_model":r.model,"codex_version":r.codex_version,
            "state":r.state,"turn_acknowledged":r.turn_id is not None,
            "token_status":r.token_status,"token_scope":"upstream_thread_snapshot",
            "token_snapshot":r.token_snapshot,
            "observed_image_items":len(r.image_items),"image_anomalies":image_anomalies(r),"image_tool_calls":None,
            "image_coverage":"through_terminal" if r.state in TERMINAL else "partial",
            "created_at":r.created_at,"updated_at":r.updated_at} for r in rows]}

async def totals(db):
    counts=dict((await db.execute(select(RuntimeObservation.token_status,func.count())
        .group_by(RuntimeObservation.token_status))).all())
    acknowledged=await db.scalar(select(func.count()).select_from(RuntimeObservation)
        .where(RuntimeObservation.turn_id.is_not(None)))
    without=await db.scalar(select(func.count()).select_from(Operation).where(
        ~select(RuntimeObservation.id).where(RuntimeObservation.operation_id==Operation.id).exists()))
    return {"dispatch_intents":sum(counts.values()),"acknowledged_attempts":acknowledged,
        "reported_snapshots":counts.get("observed",0),"missing_snapshots":counts.get("unknown",0),
        "invalid_snapshots":counts.get("invalid",0),"operations_without_observations":without,
        "turn_tokens":None,"provider_model_requests":None,"amount":None}

"""Admin-only, content-minimal operation recovery surface."""
from fastapi import APIRouter,Depends,Query,Response
from sqlalchemy import select,func
from app.admin.dependencies import require_admin_user
from app.admin.v2_controls import controls
from app.admin.v2_service import Command,submit_command,receipt
from app.design.router import enabled,transaction
from app.design.models import AdminAction,Operation,DomainEvent,LedgerEntry,Wallet
from app.design.service import ApplicationError
from app.core.time import utc_now
from app.db.models import User
router=APIRouter(prefix="/admin/v2",tags=["v2-administration"],dependencies=[Depends(enabled),Depends(require_admin_user)])

def operation_json(row):
    return {key:getattr(row,key) for key in ("id","owner_id","design_id","kind","state","reserved_units","settled_units",
        "reservation_closed","upstream_status","dispatch_intent_at","created_at","updated_at")}

@router.get("/overview")
async def overview(response:Response,db=Depends(transaction)):
    response.headers["Cache-Control"]="private, no-store"
    states=dict((await db.execute(select(Operation.state,func.count()).group_by(Operation.state))).all())
    attempts=await db.scalar(select(func.count()).select_from(AdminAction).where(AdminAction.action.in_(["reconcile","retry_publish"])))
    completed=await db.scalar(select(func.count()).select_from(AdminAction).where(
        AdminAction.action.in_(["reconcile","retry_publish"]),AdminAction.status=="completed"))
    duplicates=await db.scalar(select(func.coalesce(func.sum(AdminAction.replay_count),0)))
    from app.operations.observations import totals
    from app.operations.context_observations import totals as context_totals
    from app.admin.v2_health import overview as health
    from app.core.config import get_settings
    return {"controls":await controls(db),"operation_counts":states,"runtime_usage":await totals(db), "context_recovery":await context_totals(db), "health":await health(db,get_settings()),
        "recovery_requests":attempts,"recovery_completed":completed,"duplicate_requests":duplicates,
        "upstream_cost":{"status":"unknown","amount":None},"platform_capacity":{"status":"unknown"}}

@router.get("/operations")
async def operations(response:Response,state:str|None=Query(None,max_length=24),
    limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),db=Depends(transaction)):
    response.headers["Cache-Control"]="private, no-store"
    query=select(Operation)
    if state:query=query.where(Operation.state==state)
    rows=list((await db.scalars(query.order_by(Operation.created_at.desc(),Operation.id.desc()).offset(offset).limit(limit+1))).all())
    return {"items":[operation_json(row) for row in rows[:limit]],"has_more":len(rows)>limit}

@router.get("/operations/{identity}")
async def detail(identity:str,response:Response,db=Depends(transaction)):
    response.headers["Cache-Control"]="private, no-store"
    row=await db.get(Operation,identity)
    if row is None:raise ApplicationError("not_found")
    events=list((await db.scalars(select(DomainEvent).where(DomainEvent.design_id==row.design_id,
        DomainEvent.event_type=="operation.state_changed",
        DomainEvent.payload["operation_id"].as_string()==row.id).order_by(DomainEvent.sequence))).all())
    stages=[];previous="queued";at=row.created_at
    for e in events:
        stages.append({"state":previous,"seconds":max(0,(e.created_at-at).total_seconds())})
        previous=e.payload.get("state","unknown");at=e.created_at
    end=row.updated_at if row.state in {"succeeded","partial_success","failed","cancelled","awaiting_input"} else utc_now()
    stages.append({"state":previous,"seconds":max(0,(end-at).total_seconds())})
    refunded=await db.scalar(select(func.coalesce(func.sum(LedgerEntry.balance_delta),0)).where(
        LedgerEntry.operation_id==identity,LedgerEntry.kind=="refund"))
    wallet=await db.get(Wallet,row.owner_id)
    from app.operations.automatic_recovery import summary
    from app.core.config import get_settings
    automatic = await summary(db,row,get_settings())
    from app.operations.observations import summary as usage_summary
    return {**operation_json(row),"runtime_usage":await usage_summary(db,identity),
        "automatic_recovery":automatic,"stages":stages,"refunded_units":refunded,
        "wallet":None if wallet is None else {"balance":wallet.balance,"reserved":wallet.reserved},
        "upstream_cost":{"status":"unknown","amount":None},
        "has_staged_output":bool(row.staged_outputs),
        "busy":bool(row.lease_expires_at and row.lease_expires_at>utc_now())}

@router.get("/actions")
async def actions(response:Response,limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),db=Depends(transaction)):
    response.headers["Cache-Control"]="private, no-store"
    rows=list((await db.scalars(select(AdminAction).order_by(AdminAction.created_at.desc(),AdminAction.id.desc())
        .offset(offset).limit(limit+1))).all())
    return {"items":[receipt(row) for row in rows[:limit]],"has_more":len(rows)>limit}

@router.post("/actions")
async def command(payload:Command,response:Response,current:User=Depends(require_admin_user),db=Depends(transaction)):
    response.headers["Cache-Control"]="private, no-store"
    return receipt(await submit_command(db,current.id,payload))

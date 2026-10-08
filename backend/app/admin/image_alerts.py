"""Aggregate observed image identity conflicts or extra completed output items."""
from sqlalchemy import select, func, or_
from app.design.models import RuntimeObservation, Operation


async def duplicate_outputs(db, recent):
    row = RuntimeObservation
    entries = func.json_each_text(row.image_items).table_valued("key", "value")
    completed = select(func.count()).select_from(entries).where(entries.c.value=="completed").correlate(row).scalar_subquery()
    conflicts = func.coalesce(func.json_array_length(row.image_anomalies["conflicting"]),0)
    abnormal = select(row.operation_id.label("id")).where(row.phase=="generation",row.updated_at>=recent,
        or_(completed>1,conflicts>0)).distinct().subquery()
    counts = select(abnormal.c.id,func.count().over().label("total")).subquery()
    found = (await db.execute(select(counts.c.id,counts.c.total).order_by(counts.c.id).limit(5))).all()
    if found:
        return {"count":found[0].total,"operation_ids":[item.id for item in found]}
    observed = await db.scalar(select(func.count()).select_from(row).where(
        row.phase=="generation",row.updated_at>=recent,row.image_anomalies.is_not(None)))
    missing = await db.scalar(select(func.count()).select_from(Operation).where(
        Operation.dispatch_intent_at>=recent,
        ~select(row.id).where(row.operation_id==Operation.id,row.phase=="generation",
                              row.image_anomalies.is_not(None)).exists()))
    legacy = await db.scalar(select(func.count()).select_from(row).where(
        row.phase=="generation",row.updated_at>=recent,row.image_anomalies.is_(None)))
    return {"count":0 if observed and not missing and not legacy else None,"operation_ids":[]}

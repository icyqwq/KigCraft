"""Persistent admission controls. Gate decisions share a transaction lock with policy changes."""
from sqlalchemy import select, text
from app.design.models import RuntimeControl
from app.design.service import ApplicationError

LOCK = 7272192215

async def controls(db):
    return {row.scope: row.blocked for row in (await db.scalars(select(RuntimeControl))).all()}

async def gate(db, owner, *, recovery=False):
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": LOCK})
    values = await controls(db)
    if owner and values.get("owner:"+owner):
        raise ApplicationError("runtime_quarantined")
    if not recovery and values.get("global"):
        raise ApplicationError("generation_paused")

async def set_control(db, scope, blocked):
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK})
    row = await db.get(RuntimeControl, scope)
    if row is None:
        db.add(RuntimeControl(scope=scope, blocked=blocked))
    else:
        row.blocked = blocked
    await db.flush()

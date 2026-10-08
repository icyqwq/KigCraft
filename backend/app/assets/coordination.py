"""Coordinate installed-object writes with explicit orphan maintenance.

Locks last until the caller commits/rolls back, including the Asset INSERT.
Every process sharing a storage root must use this protocol and the same DB.
"""
from sqlalchemy import text

STORAGE_LOCK = 7272192221


async def storage_writer(db):
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": STORAGE_LOCK})

"""Shared, non-identifying health for each V2 API rejection collector.

No hostnames, PIDs, users, routes or request data are persisted. A failed sample
remains a coverage gap for the alert window even after a successful heartbeat.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.core.time import utc_now
from app.db.models import AuditCounter
from app.db.session import get_sessionmaker

PREFIX = "v2-access-collector:"
INTERVAL_SECONDS = 20
STALE_SECONDS = 70
MAX_RECORDS = 10000
# Protocol 3 includes private legacy routes, all HTTP 4xx and stream auth loss.
# Old collectors cannot certify this coverage during a rolling upgrade.
RUNNING = 3
STOPPED = -3


class AccessCollector:
    def __init__(self, *, factory=None):
        # Instantiate inside lifespan, after a preloaded ASGI server has forked.
        self.identity = uuid4().hex
        self.factory = factory
        self.pending_failure = None
        self.failure_sequence = 0
        self.last_success = None
        self._lock = asyncio.Lock()

    def failed(self, *, now=None):
        stamp = now or utc_now()
        self.pending_failure = max(stamp, self.pending_failure) if self.pending_failure else stamp
        self.failure_sequence += 1

    async def pulse(self, *, now=None, stopped=False):
        # Coalesce concurrent post-response failures instead of building an
        # unbounded queue of DB retries behind one heartbeat.
        if self._lock.locked():
            return False
        async with self._lock:
            now = now or utc_now()
            if self.last_success is not None and now - self.last_success > timedelta(seconds=STALE_SECONDS):
                self.failed(now=now)
            failure = self.pending_failure
            failure_sequence = self.failure_sequence

            async def persist():
                async with (self.factory or get_sessionmaker()).begin() as db:
                    await db.execute(text("SET LOCAL statement_timeout = '500ms'"))
                    await db.execute(text("SET LOCAL lock_timeout = '200ms'"))
                    for suffix, value, stamp in (
                        ("heartbeat", STOPPED if stopped else RUNNING, now),
                        ("gap", 1, failure),
                    ):
                        if stamp is None:
                            continue
                        statement = insert(AuditCounter).values(
                            key=PREFIX+self.identity+":"+suffix, value=value, updated_at=stamp)
                        await db.execute(statement.on_conflict_do_update(index_elements=[AuditCounter.key],
                            set_={"value":value,"updated_at":stamp}))
            try:
                await asyncio.wait_for(persist(), timeout=2)
            except Exception:
                # Private DB details never enter a response or log.
                self.failed(now=now)
                return False
            else:
                self.last_success = now
                # A concurrent rejected sample must survive this heartbeat.
                if self.failure_sequence == failure_sequence:
                    self.pending_failure = None
                return True

    async def loop(self):
        while True:
            await asyncio.sleep(INTERVAL_SECONDS)
            await self.pulse()


@asynccontextmanager
async def collector_lifespan(app, *, factory=None):
    collector = AccessCollector(factory=factory)
    app.state.access_collector = collector
    await collector.pulse()
    task = asyncio.create_task(collector.loop())
    try:
        yield collector
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await collector.pulse(stopped=True)
        finally:
            app.state.access_collector = None


async def collection_health(db, settings, *, now=None):
    now = now or utc_now()
    start = (now - timedelta(seconds=settings.v2_alert_recent_seconds)).replace(second=0,microsecond=0)
    stale_before = now - timedelta(seconds=STALE_SECONDS)
    rows = list(await db.scalars(select(AuditCounter).where(
        AuditCounter.key.like(PREFIX+"%"), AuditCounter.updated_at >= start)
        .order_by(AuditCounter.key).limit(MAX_RECORDS+1)))
    active, stale, gaps = set(), set(), set()
    malformed = False
    for row in rows[:MAX_RECORDS]:
        tail = row.key.removeprefix(PREFIX)
        identity, separator, kind = tail.partition(":")
        if (not separator or len(identity) != 32 or
                any(c not in "0123456789abcdef" for c in identity) or kind not in {"heartbeat","gap"}):
            malformed = True
            continue
        if row.updated_at > now:
            # A fast host clock cannot establish healthy coverage.
            malformed = True
        if kind == "gap":
            gaps.add(identity)
        elif row.value == RUNNING:
            (active if stale_before <= row.updated_at <= now else stale).add(identity)
        elif row.value != STOPPED:
            malformed = True
    bounded = len(rows) <= MAX_RECORDS
    expected = settings.v2_access_collector_processes
    complete = bounded and not malformed and not stale and not gaps and len(active) == expected
    return {"scope":"private_resource_rejections", "status":"complete" if complete else "incomplete",
            "active_processes":len(active), "expected_processes":expected,
            "stale_processes":len(stale), "gap_recorded":bool(gaps), "inventory_complete":bounded and not malformed}

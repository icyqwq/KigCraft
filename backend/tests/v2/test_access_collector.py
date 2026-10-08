"""Shared-DB component checks; no browser, model, or E2E acceptance."""
import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from app.admin import access_observations as observations
from app.admin.access_collector import (
    AccessCollector, PREFIX, STALE_SECONDS, collection_health, collector_lifespan,
)
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import AuditCounter
from .test_migrations import migration_database
from .test_operations import factory, setup

def settings(processes=1):
    return Settings(_env_file=None,v2_access_collector_processes=processes)

async def health(factory, *, count=1, now=None):
    async with factory() as db:
        return await collection_health(db,settings(count),now=now)

async def test_all_expected_processes_required_and_success_cannot_erase_another_failure(factory):
    first,second=AccessCollector(factory=factory),AccessCollector(factory=factory)
    now=utc_now()
    assert (await health(factory,count=2,now=now))["status"]=="incomplete"
    await first.pulse(now=now)
    assert (await health(factory,count=2,now=now))["active_processes"]==1
    await second.pulse(now=now)
    assert (await health(factory,count=2,now=now))["status"]=="complete"
    assert (await health(factory,count=1,now=now))["status"]=="incomplete"
    second.failed(now=now)
    await second.pulse(now=now)
    await first.pulse(now=now+timedelta(seconds=1))
    result=await health(factory,count=2,now=now+timedelta(seconds=1))
    assert result["status"]=="incomplete" and result["gap_recorded"]
    # A different collector's successes never update/delete this failure marker.
    async with factory() as db:
        assert (await db.get(AuditCounter,PREFIX+second.identity+":gap")).updated_at==now

async def test_hung_collector_and_recovery_keep_lost_window_unknown(factory):
    collector=AccessCollector(factory=factory)
    now=utc_now()
    await collector.pulse(now=now)
    later=now+timedelta(seconds=STALE_SECONDS+1)
    result=await health(factory,now=later)
    assert result["stale_processes"]==1 and result["status"]=="incomplete"
    await collector.pulse(now=later)
    result=await health(factory,now=later)
    assert result["active_processes"]==1 and result["gap_recorded"]
    # Simulate uninterrupted subsequent heartbeats without wall-clock sleeping.
    for seconds in range(20,3721,20):
        await collector.pulse(now=later+timedelta(seconds=seconds))
    assert (await health(factory,now=later+timedelta(seconds=3720)))["status"]=="complete"

async def test_record_failure_visible_in_shared_db_and_success_still_incomplete(factory,monkeypatch):
    actor,_,_=await setup(factory)
    _,foreign,_=await setup(factory)
    now=utc_now()
    collector=AccessCollector(factory=factory)
    await collector.pulse(now=now)
    async def broken(*args):
        raise RuntimeError("private database details")
    with monkeypatch.context() as patch:
        patch.setattr(observations,"increment",broken)
        await observations.record_rejection(actor,"/v2/designs/{design_id}",{"design_id":foreign},
            factory=factory,collector=collector,now=now)
    await observations.record_rejection(actor,"/v2/designs/{design_id}",{"design_id":foreign},
        factory=factory,collector=collector,now=now)
    async with factory() as db:
        result=await observations.recent_denials(db,settings(),now=now)
        rows=list(await db.scalars(select(AuditCounter)))
    assert result["recorded_count"]==1 and result["count"] is None
    assert result["coverage"]["gap_recorded"]
    serialized=repr([(r.key,r.value) for r in rows])
    assert actor not in serialized and foreign not in serialized and "private" not in serialized

async def test_database_failure_preserves_pending_gap_until_recovery(factory):
    class Unavailable:
        def begin(self):
            raise OSError("unavailable")
    collector=AccessCollector(factory=Unavailable())
    now=utc_now()
    assert not await collector.pulse(now=now)
    assert collector.pending_failure==now
    collector.factory=factory
    assert await collector.pulse(now=now+timedelta(seconds=1))
    assert collector.pending_failure is None
    assert (await health(factory,now=now+timedelta(seconds=1)))["gap_recorded"]

async def test_lifespan_registers_and_retires_collector_without_request_traffic(factory):
    app=SimpleNamespace(state=SimpleNamespace())
    async with collector_lifespan(app,factory=factory) as collector:
        assert app.state.access_collector is collector
        assert (await health(factory))["status"]=="complete"
    assert app.state.access_collector is None
    result=await health(factory)
    assert result["active_processes"]==0 and result["status"]=="incomplete"
    async with factory() as db:
        assert (await db.get(AuditCounter,PREFIX+collector.identity+":heartbeat")).value==-3

async def test_concurrent_failure_with_same_timestamp_not_lost(factory):
    collector=AccessCollector(factory=factory)
    now=utc_now()
    collector.failed(now=now)
    started,proceed=asyncio.Event(),asyncio.Event()
    class Delayed:
        def begin(self):
            real=factory.begin()
            class Context:
                async def __aenter__(self):
                    started.set()
                    await proceed.wait()
                    return await real.__aenter__()
                async def __aexit__(self,*args):
                    return await real.__aexit__(*args)
            return Context()
    collector.factory=Delayed()
    task=asyncio.create_task(collector.pulse(now=now))
    await started.wait()
    collector.failed(now=now)
    assert not await collector.pulse(now=now)
    proceed.set()
    assert await task
    assert collector.pending_failure==now
    collector.factory=factory
    await collector.pulse(now=now)
    assert collector.pending_failure is None

@pytest.mark.parametrize("kind",["bad_key","bad_state","future","bound"])
async def test_untrusted_or_unbounded_health_never_becomes_complete(factory,monkeypatch,kind):
    import app.admin.access_collector as module
    collector=AccessCollector(factory=factory)
    now=utc_now()
    await collector.pulse(now=now)
    async with factory.begin() as db:
        if kind=="bad_key":
            db.add(AuditCounter(key=PREFIX+"bad",value=1,updated_at=now))
        elif kind=="bad_state":
            row=await db.get(AuditCounter,PREFIX+collector.identity+":heartbeat")
            row.value=22
        elif kind=="future":
            row=await db.get(AuditCounter,PREFIX+collector.identity+":heartbeat")
            row.updated_at=now+timedelta(minutes=1)
        else:
            monkeypatch.setattr(module,"MAX_RECORDS",0)
    result=await health(factory,now=now)
    assert result["status"]=="incomplete" and not result["inventory_complete"]

async def test_separate_process_failure_is_visible_without_shared_memory(factory,migration_database):
    collector=AccessCollector(factory=factory)
    await collector.pulse()
    env={**os.environ,"KIG_COLLECTOR_TEST_DATABASE_URL":migration_database,
         "KIG_COLLECTOR_TEST_FAIL":"1","PYTHONPATH":str(Path.cwd())}
    child=await asyncio.create_subprocess_exec(sys.executable,
        "tests/v2/fixtures/access_collector_peer.py",env=env,
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        output,error=await asyncio.wait_for(child.communicate(),timeout=15)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    assert child.returncode==0, "Synthetic collector process failed"
    assert output.decode().strip()!=collector.identity
    result=await health(factory,count=2)
    assert result["active_processes"]==2 and result["gap_recorded"] and result["status"]=="incomplete"


async def test_cancelled_rejection_is_not_silently_counted_as_healthy(factory,monkeypatch):
    actor,_,_=await setup(factory)
    _,foreign,_=await setup(factory)
    collector=AccessCollector(factory=factory)
    now=utc_now()
    await collector.pulse(now=now)
    started=asyncio.Event()
    async def waiting(*args):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(observations,"increment",waiting)
    task=asyncio.create_task(observations.record_rejection(actor,"/v2/designs/{design_id}",
        {"design_id":foreign},factory=factory,collector=collector,now=now))
    await asyncio.wait_for(started.wait(),3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert collector.pending_failure==now
    await collector.pulse(now=now)
    assert (await health(factory,now=now))["gap_recorded"]

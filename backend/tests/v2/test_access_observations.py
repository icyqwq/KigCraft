"""Development checks for bounded, non-identifying rejection telemetry."""
import asyncio
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.admin import access_observations as observations
from app.admin.v2_health import overview
from app.admin.access_collector import AccessCollector
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import AuditCounter
from .test_api import api
from .test_operations import factory, setup
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints

async def counters(factory):
    async with factory() as db:
        return {row.key:row.value for row in await db.scalars(select(AuditCounter))}

async def test_api_rejections_remain_indistinguishable_and_only_write_counters(api):
    client, factory, owner, design, _ = api
    _, foreign, _ = await setup(factory)
    assert (await client.get(f"/api/v2/designs/{design}")).status_code == 200
    assert await counters(factory) == {}
    before = await fingerprints(factory)
    denied = await client.get(f"/api/v2/designs/{foreign}")
    missing = await client.get(f"/api/v2/designs/{uuid4()}")
    assert denied.status_code == missing.status_code == 404
    assert denied.content == missing.content and dict(denied.headers) == dict(missing.headers)
    after = await fingerprints(factory)
    assert {k:v for k,v in after.items() if k != "audit_counters"} == {k:v for k,v in before.items() if k != "audit_counters"}
    values = await counters(factory)
    assert sum(v for k,v in values.items() if k.endswith(":cross")) == 1
    assert sum(v for k,v in values.items() if k.endswith(":miss")) == 1
    assert values[observations.PREFIX+"observed"] == 2
    assert owner not in repr(values) and foreign not in repr(values)
    client.cookies.clear()
    assert (await client.get(f"/api/v2/designs/{foreign}")).status_code == 401
    after_auth = await counters(factory)
    assert sum(v for k,v in after_auth.items() if k.endswith(":unauthenticated")) == 1
    assert sum(v for k,v in after_auth.items() if k.endswith(":cross")) == 1

async def test_failure_does_not_change_404_or_mutate_business_state(api, monkeypatch):
    client, factory, _, _, _ = api
    _, foreign, _ = await setup(factory)
    before = await fingerprints(factory)
    async def unavailable(*args):
        raise RuntimeError("private failure details")
    monkeypatch.setattr(observations, "increment", unavailable)
    denied = await client.get(f"/api/v2/designs/{foreign}")
    assert denied.status_code == 404 and "private failure" not in denied.text
    assert await fingerprints(factory) == before

async def test_concurrent_rejections_count_once_per_request(factory):
    actor, _, _ = await setup(factory)
    _, foreign, _ = await setup(factory)
    await asyncio.gather(*(observations.record_rejection(actor, "/v2/designs/{design_id}",
        {"design_id":foreign}, factory=factory) for _ in range(10)))
    values = await counters(factory)
    assert sum(v for k,v in values.items() if k.endswith(":cross")) == 10
    assert values[observations.PREFIX+"observed"] == 10

async def test_window_threshold_and_unknown_observer(factory, tmp_path, monkeypatch):
    actor, _, _ = await setup(factory)
    _, foreign, _ = await setup(factory)
    now = utc_now().replace(second=0,microsecond=0)
    settings = Settings(_env_file=None, v2_storage_dir=str(tmp_path), v2_alert_access_denials=2)
    async def report():
        async with factory() as db:
            result = await overview(db,settings,now=now)
            return next(s for s in result["signals"] if s["code"] == "access_denials")
    assert (await report())["status"] == "unknown"
    collector=AccessCollector(factory=factory)
    await collector.pulse(now=now)
    for stamp in [now-timedelta(days=1), now+timedelta(days=1), now]:
        await observations.record_rejection(actor,"/v2/designs/{design_id}",{"design_id":foreign},factory=factory,now=stamp)
    assert (await report())["count"] == 1
    collector.failed(now=now)
    await collector.pulse(now=now)
    assert (await report())["status"] == "unknown"
    await observations.record_rejection(actor,"/v2/designs/{design_id}",{"design_id":foreign},factory=factory,now=now)
    collector.failed(now=now)
    await collector.pulse(now=now)
    result = await report()
    assert result["status"] == "active" and result["count"] == 2 and result["severity"] == "critical"

@pytest.mark.parametrize("route,params,expected",[
    ("/v2/designs/{design_id}",{"design_id":"d"},1),
    ("/api/v2/assets/{asset_id}",{"asset_id":"a"},1),
    ("/v2/operations/{operation_id}",{"operation_id":"o"},1),
    ("/v2/branches/{branch_id}/messages/{message_id}",{"branch_id":"b","message_id":"m"},2),
    ("/v2/designs/{design_id}/revisions/{revision_id}",{"design_id":"d","revision_id":"r"},2),
    ("/v2/account/editor-drafts/{draft_id}",{"draft_id":"d"},1),
    ("/v2/account/legacy-materials/{material_id}/draft",{"material_id":"m"},1),
    ("/legacy/designs/{design_id}",{"design_id":"d"},0),
    ("/v2/unknown",{"identity":"v"},0),
    ("/v2/designs/{design_id}",{"design_id":"x"*101},0),
])
def test_known_resource_mapping(route,params,expected):
    assert len(observations.resource_queries(route,params)) == expected


async def test_saturated_counter_does_not_overflow(factory):
    async with factory.begin() as db:
        db.add(AuditCounter(key=observations.PREFIX+"observed",value=2147483647,updated_at=utc_now()))
    async with factory.begin() as db:
        await observations.increment(db,observations.PREFIX+"observed")
    assert (await counters(factory))[observations.PREFIX+"observed"] == 2147483647

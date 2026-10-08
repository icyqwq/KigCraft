from datetime import timedelta
from types import SimpleNamespace
import pytest
from app.admin.v2_health import overview, storage_signal
from app.core.config import Settings
from app.core.time import utc_now
from app.design.models import Operation, Wallet
from .test_worker import prepared
from .test_operations import factory
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints


def settings(tmp_path,**values):
    return Settings(_env_file=None,v2_storage_dir=str(tmp_path),**values)


@pytest.mark.parametrize("state,age,lease,error,code",[
    ("running",0,-1,None,"expired_leases"),
    ("publishing",301,None,None,"publishing_backlog"),
    ("reconciling",301,None,None,"unresolved_operations"),
    ("failed",0,None,"runtime_identity_changed","runtime_identity"),
])
async def test_actionable_signals_point_to_operations_without_mutation(factory,tmp_path,state,age,lease,error,code):
    _,_,_,_,opid=await prepared(factory,tmp_path)
    now=utc_now()
    async with factory.begin() as db:
        op=await db.get(Operation,opid);op.state=state;op.updated_at=now-timedelta(seconds=age);op.error_code=error
        op.lease_expires_at=now+timedelta(seconds=lease) if lease is not None else None
    before=await fingerprints(factory)
    async with factory() as db:
        result=await overview(db,settings(tmp_path,v2_alert_expired_leases=1),now=now)
    item=next(x for x in result["signals"] if x["code"]==code)
    assert item["status"]=="active" and item["count"]==1 and item["operation_ids"]==[opid]
    assert await fingerprints(factory)==before


async def test_wallet_drift_is_critical_and_unmeasured_sources_stay_unknown(factory,tmp_path):
    _,_,owner,_,_=await prepared(factory,tmp_path)
    async with factory() as db:
        first=await overview(db,settings(tmp_path))
    assert next(x for x in first["signals"] if x["code"]=="wallet_mismatch")["status"]=="clear"
    async with factory.begin() as db:(await db.get(Wallet,owner)).balance+=1
    async with factory() as db:result=await overview(db,settings(tmp_path))
    signals={s["code"]:s for s in result["signals"]}
    assert signals["wallet_mismatch"]["status"]=="active"
    assert signals["wallet_mismatch"]["severity"]=="critical"
    for code in ("duplicate_outputs","access_denials"):
        assert signals[code]["status"]=="unknown" and signals[code]["count"] is None


async def test_review_streak_stops_at_success_and_excludes_old_failures(factory,tmp_path):
    now=utc_now()
    ids=[]
    for i in range(3):
        _,_,_,_,opid=await prepared(factory,tmp_path/str(i));ids.append(opid)
        async with factory.begin() as db:
            op=await db.get(Operation,opid);op.state="failed";op.error_code="moderation_unavailable";op.updated_at=now-timedelta(seconds=i)
    async def item():
        async with factory() as db:
            result=await overview(db,settings(tmp_path),now=now)
            return next(x for x in result["signals"] if x["code"]=="review_failures")
    assert (await item())["status"]=="active"
    async with factory.begin() as db:
        op=await db.get(Operation,ids[1]);op.state="succeeded";op.error_code=None
    assert (await item())["count"]==1
    async with factory.begin() as db:
        (await db.get(Operation,ids[0])).updated_at=now-timedelta(days=2)
    assert (await item())["count"]==0


def test_storage_boundary_and_unavailable_path(tmp_path,monkeypatch):
    config=settings(tmp_path,v2_alert_storage_free_bytes=100)
    monkeypatch.setattr("app.admin.v2_health.shutil.disk_usage",lambda _:SimpleNamespace(free=100))
    assert storage_signal(config)["status"]=="clear"
    monkeypatch.setattr("app.admin.v2_health.shutil.disk_usage",lambda _:SimpleNamespace(free=99))
    assert storage_signal(config)["status"]=="active"
    config.v2_storage_dir=str(tmp_path/"missing")
    assert storage_signal(config)["status"]=="unknown"


async def test_fresh_publication_and_expired_terminal_lease_do_not_alert(factory,tmp_path):
    _,_,_,_,opid=await prepared(factory,tmp_path)
    now=utc_now()
    async with factory.begin() as db:
        op=await db.get(Operation,opid);op.state="publishing";op.updated_at=now
    async with factory() as db:result=await overview(db,settings(tmp_path),now=now)
    assert next(x for x in result["signals"] if x["code"]=="publishing_backlog")["count"]==0
    async with factory.begin() as db:
        op=await db.get(Operation,opid);op.state="succeeded";op.lease_expires_at=now-timedelta(seconds=5)
    async with factory() as db:result=await overview(db,settings(tmp_path,v2_alert_expired_leases=1),now=now)
    assert next(x for x in result["signals"] if x["code"]=="expired_leases")["count"]==0

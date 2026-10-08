"""Disposable PG/files and synthetic native profiles; no model, Docker or E2E."""
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import shutil
import sqlite3
import pytest
from sqlalchemy import select

from app.db.models import Project, ProjectDraft, User
from app.design.models import Design, InputRecord, Operation
from app.maintenance import account_files as module
from app.maintenance.backup_bundle import dependencies
from app.maintenance.orphan_assets import CleanupError
from .test_operations import factory
from .test_migrations import migration_database
from .test_codex_snapshot import profile
from .test_account_content import fixture as content_fixture, inspect as inspect_content, apply
from .test_legacy_inventory import fingerprints

REASON="Verified closed account file removal"


async def fixture(factory,tmp_path,profile):
    owner,admin,cfg,now,ids,original=await content_fixture(factory,tmp_path)
    await apply(factory,cfg,owner,admin,await inspect_content(factory,cfg,owner),now)
    roots={group:tmp_path/group for group in ("legacy_generated","legacy_references")}
    for root in roots.values():root.mkdir()
    for group,key in (("legacy_generated",ids["job"]+"/original.png"),("legacy_references",owner+"/original.png")):
        path=roots[group]/key;path.parent.mkdir();path.write_bytes(original.read_bytes())
    work=tmp_path/"runtime";work.mkdir()
    cfg.v2_runtime_dir=str(work);cfg.v2_account_file_cleanup_enabled=True;cfg.v2_account_file_grace_days=1
    async with factory.begin() as db:
        design=await db.scalar(select(Design).where(Design.owner_id==owner))
        source=InputRecord(branch_id=design.last_branch_id,digest="c"*64,snapshot={})
        db.add(source);await db.flush()
        op=Operation(owner_id=owner,design_id=design.id,branch_id=design.last_branch_id,
            client_request_id="historical-file-fixture",request_digest="d"*64,input_snapshot_id=source.id,
            kind="generate",state="cancelled",expected_branch_version=0,reservation_closed=True)
        db.add(op);await db.flush();opid=op.id
        key="a"*64
        data=b"synthetic stage bytes"
        op.staged_outputs={"main_image":{"delivery_key":key,"filename":key+".png","sha256":hashlib.sha256(data).hexdigest()}}
    (work/opid/"staging").mkdir(parents=True)
    (work/opid/"staging"/(key+".png")).write_bytes(data)
    (work/opid/"input-copy.bin").write_bytes(b"private input")
    return owner,admin,cfg,now,ids,roots,work,opid,profile[0]


async def inspect(factory,values,**kwargs):
    owner,_,cfg,_,_,roots,_,_,home=values
    async with factory.begin() as db:
        return await module.inspect_files(db,cfg,owner,roots,writers_stopped=True,profile=home,**kwargs)


async def retire(factory,values,report,**kwargs):
    owner,admin,cfg,now,_,roots,_,_,home=values
    async with factory.begin() as db:
        return await module.retire_files(db,cfg,owner,roots,report["digest"],admin,REASON,
            writers_stopped=True,profile=home,now=now,**kwargs)


async def purge(factory,values,batch,**kwargs):
    owner,admin,cfg,now,_,roots,_,_,home=values
    async with factory.begin() as db:
        return await module.purge_files(db,cfg,owner,roots,batch["receipt_id"],admin,REASON,
            writers_stopped=True,profile=home,now=kwargs.pop("now",now+timedelta(days=2)),**kwargs)


async def test_closed_files_and_work_removed_with_backup_proof(factory,tmp_path,profile):
    values=await fixture(factory,tmp_path,profile)
    owner,admin,cfg,now,ids,roots,work,opid,home=values
    before=await fingerprints(factory)
    native={p.relative_to(home):p.read_bytes() for p in home.rglob("*") if p.is_file()}
    report=await inspect(factory,values)
    assert len(report["candidates"])==4 and not report["retained"]
    assert await fingerprints(factory)==before
    batch=await retire(factory,values,report)
    assert await retire(factory,values,report)==batch
    with pytest.raises(CleanupError,match="grace_period"):await purge(factory,values,batch,now=now)
    result=await purge(factory,values,batch)
    assert result["count"]==4 and await purge(factory,values,batch)==result
    assert not list(work.rglob("*.bin")) and not list(work.rglob("*.png"))
    assert not list(roots["legacy_generated"].rglob("*.png"))
    assert not list(roots["legacy_references"].rglob("*.png"))
    assert {p.relative_to(home):p.read_bytes() for p in home.rglob("*") if p.is_file()}==native
    async with factory() as db:
        files,_,absent=await dependencies(db,{**roots,"runtime":work,"assets":Path(cfg.v2_storage_dir)})
        assert len(files)==1 and next(iter(files)).startswith("assets/")
        assert len([x for x in absent if x["state"]=="retired_account_file"])==4
        assert len(await module.completed_files(db))==4
    assert not (await inspect(factory,values))["candidates"]
    after=await fingerprints(factory)
    assert {k:v for k,v in before.items() if k!="v2_admin_actions"}=={k:v for k,v in after.items() if k!="v2_admin_actions"}


@pytest.mark.parametrize("kind",["business","native"])
async def test_shared_reference_is_retained_and_late_reference_blocks_purge(factory,tmp_path,profile,kind):
    values=await fixture(factory,tmp_path,profile);report=await inspect(factory,values)
    batch=await retire(factory,values,report)
    if kind=="business":
        async with factory.begin() as db:
            other=User(email=str(uuid4())+"@example.com");db.add(other);await db.flush()
            project=Project(user_id=other.id,name="other");db.add(project);await db.flush()
            db.add(ProjectDraft(project_id=project.id,data={"url":"/old/original.png"}))
    else:
        home,threads=profile
        with sqlite3.connect(home/"thread_history_1.sqlite") as db:
            db.execute("UPDATE thread_items SET item_json=? WHERE thread_id=?",(json.dumps({"url":"/old/original.png"}),threads[1]))
        db.close()
    with pytest.raises(CleanupError,match="reference_added"):await purge(factory,values,batch)
    report=await inspect(factory,values)
    assert len(report["retained"])==2 and len(report["candidates"])==2
    assert list(values[5]["legacy_generated"].rglob("*.png"))


@pytest.mark.parametrize("fault",["changed","missing","policy","stop","profile_busy","hardlink"])
async def test_unconfirmed_files_or_boundary_refuse(factory,tmp_path,profile,fault):
    values=await fixture(factory,tmp_path,profile);report=await inspect(factory,values)
    batch=await retire(factory,values,report);path=values[5]["legacy_generated"]/values[4]["job"]/"original.png"
    if fault=="changed":
        path.write_bytes(b"changed")
        with pytest.raises(CleanupError,match="account_file_changed"):await purge(factory,values,batch)
    elif fault=="missing":
        path.unlink()
        with pytest.raises(CleanupError,match="unconfirmed_account_file_missing"):await inspect(factory,values)
        async with factory() as db:
            with pytest.raises((ValueError,FileNotFoundError)):
                await dependencies(db,{**values[5],"runtime":values[6],"assets":Path(values[2].v2_storage_dir)})
    elif fault=="policy":
        values[2].v2_account_file_cleanup_enabled=False
        with pytest.raises(CleanupError,match="disabled"):await inspect(factory,values)
    elif fault=="stop":
        async with factory.begin() as db:
            with pytest.raises(CleanupError,match="writers_stopped"):
                await module.inspect_files(db,values[2],values[0],values[5],profile=values[8])
    elif fault=="profile_busy":
        from app.codex_runtime.profile_lock import ProfileLease,ProfileLockError
        lease=ProfileLease(values[8]).acquire()
        try:
            with pytest.raises(ProfileLockError):await inspect(factory,values)
        finally:lease.release(clean=True)
    else:
        import os
        os.link(path,tmp_path/"second-link.png")
        with pytest.raises(CleanupError):await inspect(factory,values)


async def test_partial_delete_requires_successful_retry_before_backup(factory,tmp_path,profile,monkeypatch):
    values=await fixture(factory,tmp_path,profile);batch=await retire(factory,values,await inspect(factory,values))
    original=Path.unlink;count=0
    def fail(path,*args,**kwargs):
        nonlocal count
        if path.suffix==".png":
            count+=1
            if count==2:raise OSError("synthetic interruption")
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,"unlink",fail)
        with pytest.raises(OSError,match="synthetic"):await purge(factory,values,batch)
    async with factory() as db:
        assert await module.completed_files(db)=={}
        with pytest.raises((ValueError,FileNotFoundError)):
            await dependencies(db,{**values[5],"runtime":values[6],"assets":Path(values[2].v2_storage_dir)})
    assert (await purge(factory,values,batch))["count"]==4


async def test_commit_required_and_inventory_digest_changes(factory,tmp_path,profile):
    values=await fixture(factory,tmp_path,profile)
    owner,admin,cfg,now,_,roots,work,opid,home=values
    report=await inspect(factory,values)
    async with factory.begin() as db:
        batch=await module.retire_files(db,cfg,owner,roots,report["digest"],admin,REASON,writers_stopped=True,profile=home,now=now)
        with pytest.raises(CleanupError,match="commit_required"):
            await module.purge_files(db,cfg,owner,roots,batch["receipt_id"],admin,REASON,writers_stopped=True,profile=home,now=now+timedelta(days=2))
    (work/opid/"new.txt").write_text("new content",encoding="utf-8")
    with pytest.raises(CleanupError,match="inventory_changed"):
        await retire(factory,values,{**report,"digest":"e"*64})


async def test_pagination_and_duplicate_row_bound(factory,tmp_path,profile,monkeypatch):
    values=await fixture(factory,tmp_path,profile)
    first=await inspect(factory,values,limit=1)
    second=await inspect(factory,values,limit=1,after=first["next_after"])
    assert first["next_after"] and first["candidates"]!=second["candidates"]
    monkeypatch.setattr(module,"MAX_FILES",0)
    with pytest.raises(CleanupError,match="inventory_too_large"):await inspect(factory,values)

def production(values,tmp_path,monkeypatch,profile):
    from .test_runtime_retention_production import production as bind, StoppedRuntime
    from app.maintenance import runtime_retention
    owner,admin,cfg,now,ids,roots,work,opid,home=values
    base,state=bind(cfg,owner,work,tmp_path,monkeypatch)
    (base/owner/"inputs"/"keep.png").unlink()
    (base/owner/"inputs"/opid).mkdir()
    (base/owner/"inputs"/opid/"copy.bin").write_bytes(b"private input copy")
    other=str(uuid4());cfg.v2_container_user_ids=[owner,other]
    for kind in ("work","inputs","profile"):(base/other/kind).mkdir(parents=True)
    for identity in (owner,other):
        target=base/identity/"profile"
        shutil.copytree(home,target,dirs_exist_ok=True)
        with sqlite3.connect(target/"state_5.sqlite") as db:
            for thread in profile[1]:
                db.execute("UPDATE threads SET rollout_path=?,cwd=? WHERE id=?",(str(target/"sessions"/("rollout-"+thread+".jsonl")),str(target),thread))
        db.close()
    def bound(settings):
        result=StoppedRuntime(settings,base,state)
        result.input_root=base/result.owner/"inputs"
        return result
    monkeypatch.setattr(module,"ContainerRuntime",bound)
    monkeypatch.setattr(module,"root_path",runtime_retention.root_path)
    return (owner,admin,cfg,now,ids,roots,base/owner/"work",opid,None),base,state,other


async def test_production_locks_all_profiles_and_purges_only_owner(factory,tmp_path,profile,monkeypatch):
    values,base,state,other=production(await fixture(factory,tmp_path,profile),tmp_path,monkeypatch,profile)
    (base/other/"inputs"/"preserve.bin").write_bytes(b"other account")
    report=await inspect(factory,values)
    assert len(report["candidates"])==5 and set(state["owners"])=={values[0],other}
    batch=await retire(factory,values,report)
    assert (await purge(factory,values,batch))["count"]==5
    assert (base/other/"inputs"/"preserve.bin").read_bytes()==b"other account"
    assert (base/values[0]/"profile"/"auth.json").exists()
    assert not list((base/values[0]/"inputs").rglob("*.bin"))
    (base/other/"inputs"/"preserve.bin").unlink()
    accounts={identity:{kind:base/identity/kind for kind in ("work","inputs")} for identity in (values[0],other)}
    async with factory() as db:
        files,_,absent=await dependencies(db,{**values[5],"assets":Path(values[2].v2_storage_dir)},accounts=accounts)
        assert len(files)==1
        assert any(x.get("group")==f"accounts/{values[0]}/inputs" and x["state"]=="retired_account_file" for x in absent)



@pytest.mark.parametrize("fault",["running","before_delete","foreign_profile"])
async def test_production_stop_and_cross_profile_boundaries(factory,tmp_path,profile,monkeypatch,fault):
    values,base,state,other=production(await fixture(factory,tmp_path,profile),tmp_path,monkeypatch,profile)
    batch=await retire(factory,values,await inspect(factory,values))
    if fault=="running":state["running"]=True
    elif fault=="before_delete":state["fail_at"]=2
    else:
        with sqlite3.connect(base/other/"profile"/"thread_history_1.sqlite") as db:
            db.execute("UPDATE thread_items SET item_json=?",(json.dumps({"file":"copy.bin"}),))
        db.close()
    with pytest.raises(CleanupError,match="container_stop_unconfirmed|reference_added"):await purge(factory,values,batch)
    assert (base/values[0]/"inputs"/values[7]/"copy.bin").exists()
    assert list(values[5]["legacy_generated"].rglob("*.png"))

async def test_new_live_reference_invalidates_backup_tombstone(factory,tmp_path,profile):
    values=await fixture(factory,tmp_path,profile)
    batch=await retire(factory,values,await inspect(factory,values))
    await purge(factory,values,batch)
    async with factory.begin() as db:
        other=User(email=str(uuid4())+"@example.com");db.add(other);await db.flush()
        project=Project(user_id=other.id,name="other");db.add(project);await db.flush()
        db.add(ProjectDraft(project_id=project.id,data={"url":"original.png"}))
    async with factory() as db:
        with pytest.raises(CleanupError,match="retired_account_file_referenced"):
            await dependencies(db,{**values[5],"runtime":values[6],"assets":Path(values[2].v2_storage_dir)})

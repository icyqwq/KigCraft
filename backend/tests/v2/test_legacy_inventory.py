import hashlib
import json
from pathlib import Path
from uuid import uuid4
import pytest
from PIL import Image
from sqlalchemy import select,text
from app.db.models import User,Project,GenerationJob,GenerationOutput,AlbumItem,JobStatus,ProjectDraft,ReferenceAsset
from app.maintenance.legacy_status_adapter import legacy_status
from app.maintenance.v2_migration import inventory,generated_key,image_inventory,recipe_inventory,InventoryError
from .test_migrations import migration_database
from .test_operations import factory

async def seed(factory,root):
    async with factory.begin() as db:
        owner=User(email=str(uuid4())+"@example.com")
        other=User(email=str(uuid4())+"@example.com")
        db.add_all([owner,other]);await db.flush()
        p=Project(user_id=owner.id,name="Same name");q=Project(user_id=other.id,name="Same name")
        db.add_all([p,q]);await db.flush()
        jobs=[]
        for index in range(2):
            job=GenerationJob(user_id=owner.id,project_id=p.id,status=JobStatus.succeeded,
                runtime_status="accepted",accepted_output_index=0,provider="seedream",
                generation_mode="front_design" if index==0 else "turnaround",character_session_id="same-character",
                prompt_payload={"character_session_id":"same-character","text":"PRIVATE PROMPT",
                    "analysis_provider":"qwen","image_model":"original-image-model"})
            db.add(job);await db.flush();jobs.append(job)
            key=f"seedream/{job.id}/image.png";file=root/key;file.parent.mkdir(parents=True,exist_ok=True)
            Image.new("RGB",(16,12),"coral").save(file)
            db.add(GenerationOutput(job_id=job.id,index=0,object_key=key,image_url="/api/generated/"+key,width=16,height=12))
        await db.flush()
        outputs=list((await db.scalars(select(GenerationOutput).order_by(GenerationOutput.job_id))).all())
        output=next(o for o in outputs if o.job_id==jobs[0].id)
        album=AlbumItem(user_id=owner.id,image_url=output.image_url,recipe={"eyeHeight":0.1},metadata_json={"note":"PRIVATE NOTE"})
        db.add(album);db.add(ProjectDraft(project_id=p.id,data={"private":"draft"}))
        db.add(ReferenceAsset(user_id=owner.id,kind="hair",object_key="private/reference.png",file_name="private.png",mime_type="image/png"))
        await db.flush()
        return {"owner":owner.id,"other":other.id,"project":p.id,"foreign_project":q.id,
                "job":jobs[0].id,"second":jobs[1].id,"output":output.id,"album":album.id}

async def scan(factory,root,owner):
    async with factory() as db:
        return await inventory(db,owner,asset_root=root)

def row(report,kind,identity):
    return next(r for r in report["records"] if r["kind"]==kind and r["id"]==identity)

async def fingerprints(factory):
    async with factory() as db:
        result={}
        for name in (await db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))).all():
            values=(await db.scalars(text('SELECT to_jsonb(t)::text FROM "'+name+'" t ORDER BY to_jsonb(t)::text'))).all()
            result[name]=hashlib.sha256("\n".join(values).encode()).hexdigest()
        return result

async def test_two_scans_preserve_all_data_exact_groups_provider_and_flat_recipe(factory,tmp_path):
    ids=await seed(factory,tmp_path)
    before=await fingerprints(factory)
    first=await scan(factory,tmp_path,ids["owner"]);second=await scan(factory,tmp_path,ids["owner"])
    assert first==second and await fingerprints(factory)==before
    assert first["counts"]=={"projects":1,"jobs":2,"outputs":2,"albums":1,"references":1,"project_references":0,"drafts":1}
    assert len(first["groups"])==1
    jobs=[r for r in first["records"] if r["kind"]=="job"]
    assert all(r["provider"]["provider"]=="seedream" and r["provider"]["analysis_provider"]=="qwen" for r in jobs)
    assert all(r["status"]["execution"]=="completed" and r["status"]["selection"]=="confirmed" for r in jobs)
    outputs=[r for r in first["records"] if r["kind"]=="output"]
    assert {r["view"] for r in outputs}=={"front","composite"}
    assert all(r["image"]["state"]=="available" for r in outputs)
    album=row(first,"album",ids["album"])
    assert album["link_basis"]=="exact_owned_object" and album["linked_output_id"]==ids["output"]
    assert album["recipe"]["state"]=="legacy_unverified" and album["recipe"]["replay"] is False
    rendered=json.dumps(first)
    assert all(secret not in rendered for secret in ("PRIVATE PROMPT","PRIVATE NOTE","private/reference.png",ids["other"],ids["foreign_project"]))
    assert not list(tmp_path.rglob("*.json"))


@pytest.mark.parametrize("defect",["project","session","missing_session"])
async def test_conflicting_or_missing_identity_is_never_guessed_into_same_group(factory,tmp_path,defect):
    ids=await seed(factory,tmp_path)
    async with factory.begin() as db:
        job=await db.get(GenerationJob,ids["second"])
        if defect=="project":job.project_id=ids["foreign_project"]
        elif defect=="session":job.prompt_payload={"character_session_id":"different"}
        else:job.character_session_id=""
    report=await scan(factory,tmp_path,ids["owner"])
    a=row(report,"job",ids["job"]);b=row(report,"job",ids["second"])
    assert a["group_id"]!=b["group_id"] and len(report["groups"])==2
    assert next(g for g in report["groups"] if g["id"]==b["group_id"])["basis"]=="job"
    if defect=="project":
        output=next(r for r in report["records"] if r["kind"]=="output" and r["job_id"]==ids["second"])
        assert output["image"]["state"]=="unmapped"


async def test_foreign_output_link_and_same_name_owner_never_leak_image_or_group(factory,tmp_path):
    ids=await seed(factory,tmp_path)
    async with factory.begin() as db:
        db.add(AlbumItem(user_id=ids["other"],image_url="/generated/seedream/unknown/image.png",output_id=ids["output"],project_id=ids["foreign_project"]))
    report=await scan(factory,tmp_path,ids["other"])
    assert report["counts"]["jobs"]==0 and report["counts"]["outputs"]==0
    album=report["records"][0]
    assert album["linked_output_id"] is None and album["image"]["state"]=="unmapped" and album["provider"] is None
    assert ids["output"] not in json.dumps(report) and ids["owner"] not in json.dumps(report)


@pytest.mark.parametrize("defect",["missing","invalid","size","duplicate_claim"])
async def test_image_failures_and_conflicting_claims_stay_visible(factory,tmp_path,defect):
    ids=await seed(factory,tmp_path)
    async with factory.begin() as db:
        output=await db.get(GenerationOutput,ids["output"])
        file=tmp_path/output.object_key
        if defect=="missing":file.unlink()
        elif defect=="invalid":file.write_bytes(b"not an image")
        elif defect=="size":output.width=99
        else:
            job=GenerationJob(user_id=ids["other"],project_id=ids["foreign_project"],status=JobStatus.succeeded)
            db.add(job);await db.flush()
            db.add(GenerationOutput(job_id=job.id,index=0,object_key=output.object_key,image_url=output.image_url))
    report=await scan(factory,tmp_path,ids["owner"])
    output=row(report,"output",ids["output"])
    expected={"missing":"image_missing","invalid":"image_invalid_image","size":"stored_dimensions_mismatch","duplicate_claim":"asset_owner_conflict"}[defect]
    assert expected in output["issues"]
    assert report["counts"]["outputs"]==2


async def test_legacy_identity_without_login_and_standalone_flat_upload_are_preserved(factory,tmp_path):
    async with factory.begin() as db:
        user=User(id="legacy-anonymous",email="legacy-anonymous@example.invalid")
        db.add(user);await db.flush()
        album=AlbumItem(user_id=user.id,image_url="placeholder",recipe=["broken"])
        db.add(album);await db.flush()
        album.image_url="/generated/album/"+album.id+"/old.png"
        file=tmp_path/"album"/album.id/"old.png";file.parent.mkdir(parents=True);Image.new("RGB",(12,8),"blue").save(file)
        identity=album.id
    report=await scan(factory,tmp_path,"legacy-anonymous")
    assert report["account"]=={"has_password":False,"email_verified":False}
    album=row(report,"album",identity)
    assert album["image"]["state"]=="available" and album["view"]=="unknown" and album["provider"] is None
    assert album["recipe"]["state"]=="invalid" and album["recipe"]["replay"] is False


@pytest.mark.parametrize("url",["https://other.test/api/generated/x.png","//other.test/api/generated/x.png",
    "/api/generated/../x.png","/api/generated/%2e%2e/x.png","/api/generated/%252e%252e/x.png",
    "/api/generated/C:/x.png","/api/generated/x.png?token=secret","/api/generated/a\\x.png",
    "/api/generated/a//x.png","/api/generated/a./x.png"])
def test_unsafe_or_remote_locators_are_not_resolved(url):
    assert generated_key(url,"/api/generated") is None

def test_unknown_accepted_is_not_v2_accepted_and_does_not_invent_selection():
    assert legacy_status("succeeded","accepted",0,[0])["selection"]=="confirmed"
    for stored,runtime,index,outputs in [("running","accepted",0,[0]),("succeeded","accepted",2,[0]),("succeeded","accepted",0,[0,0])]:
        result=legacy_status(stored,runtime,index,outputs)
        assert result["selection"]=="unverified" and "selection_conflict" in result["issues"]
    assert legacy_status("accepted","accepted",0,[0])["execution"]=="unknown"
    assert legacy_status("succeeded","succeeded",None,[])["issues"]==["completed_without_output_record"]


async def test_album_alias_cannot_bypass_its_source_jobs_project_ownership_conflict(factory,tmp_path):
    ids=await seed(factory,tmp_path)
    async with factory.begin() as db:
        (await db.get(GenerationJob,ids["job"])).project_id=ids["foreign_project"]
    report=await scan(factory,tmp_path,ids["owner"])
    album=row(report,"album",ids["album"])
    assert album["image"]["state"]=="unmapped" and "project_owner_conflict" in album["issues"]

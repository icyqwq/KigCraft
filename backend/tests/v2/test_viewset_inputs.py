import base64
from uuid import uuid4
from pathlib import Path
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from app.assets.storage import asset_path, upload_asset
from app.contracts.viewsets import ViewSetOptions, ViewSetSnapshot, ViewSetSource
from app.contracts.v2 import SnapshotReference
from app.conversation.prepare import prepare
from app.design.models import Asset, Branch, Design, InputRecord, Operation, Revision, Wallet
from app.design.service import ApplicationError, create_design
from app.design.viewsets import freeze_viewset
from app.operations.viewset import generation_snapshot
from .test_operations import factory, setup
from .test_migrations import migration_database
from .test_worker import png


def frozen(**options):
    return ViewSetSnapshot(design_id="design", branch_id="branch", branch_version=7,
        source=ViewSetSource(revision_id="front-v4", number=4, width=100, height=100,
            image=SnapshotReference(asset_id="front-asset", sha256="a"*64, role="base", ordinal=1, usage="original front")),
        options=ViewSetOptions(allow_back_inference=True, **options))


@pytest.mark.parametrize("views", [
    ("front","left_three_quarter","left_profile","back"),
    ("front","right_three_quarter","right_profile","back"),
    ("front","left_three_quarter","left_profile","right_profile","back"),
    ("front","left_three_quarter","left_profile","right_three_quarter","right_profile","back"),
])
def test_every_angle_and_retry_uses_same_original_and_features(views):
    snapshot=frozen(views=views,confirmed_features=("coral wig", "brown eyes"))
    before=snapshot.model_dump_json()
    for view in snapshot.generated_views:
        first=generation_snapshot(snapshot,view,expected_digest=snapshot.digest())
        replay=generation_snapshot(snapshot,view,expected_digest=snapshot.digest())
        assert first.digest()==replay.digest()
        assert first.references==(snapshot.source.image,)
        assert first.base_revision_id=="front-v4"
        assert first.branch_version==0
        assert "coral wig" in first.user_text and "brown eyes" in first.user_text
        if view.startswith("left_"):assert "viewer's LEFT" in first.user_text
        if view.startswith("right_"):assert "viewer's RIGHT" in first.user_text
    assert snapshot.model_dump_json()==before
    assert snapshot.inferred("back") and not snapshot.inferred("front")


@pytest.mark.parametrize("options", [
    {"views":("front","front","left_profile","back")},
    {"views":("front","left_three_quarter","right_profile","back")},
    {"views":("left_three_quarter","left_profile","back","front")},
    {"views":("front","left_three_quarter","left_profile","collage")},
    {"allow_back_inference":"true"}, {"allow_back_inference":1},
    {"confirmed_features":(" ",)}, {"confirmed_features":("x"*1001,)},
    {"confirmed_features":tuple("feature" for _ in range(17))},
    {"back_details":"x"*4001}, {"inferred":False}, {"source_asset_id":"other"},
])
def test_invalid_or_client_forged_options_are_rejected(options):
    with pytest.raises(ValidationError):ViewSetOptions.model_validate(options)


def test_missing_back_requires_explicit_consent_and_snapshot_is_deeply_frozen():
    with pytest.raises(ValidationError):
        ViewSetSnapshot.model_validate({**frozen().model_dump(),"options":{"back_details":"  "}})
    snapshot=frozen(back_details="coral bob, no rear accessories")
    assert not snapshot.inferred("back")
    with pytest.raises(ValidationError):snapshot.source.revision_id="replacement"
    with pytest.raises(ValidationError):snapshot.options.allow_back_inference=False
    with pytest.raises(ValidationError):snapshot.source.image.asset_id="replacement"
    for angle,code in (("front","viewset_front_is_reused"),("right_profile","viewset_view_not_authorized")):
        with pytest.raises(ApplicationError,match=code):
            generation_snapshot(snapshot,angle,expected_digest=snapshot.digest())
    changed=frozen(confirmed_features=("changed",))
    with pytest.raises(ApplicationError,match="input_snapshot_changed"):
        generation_snapshot(changed,"back",expected_digest=snapshot.digest())


async def seed(factory,tmp_path,setup_design=setup,historical=False):
    owner,design,branch=await setup_design(factory)
    async with factory.begin() as db:
        asset=upload_asset(base64.b64decode(png()),owner,tmp_path)
        db.add(asset);await db.flush()
        if historical:
            # An old schema has none of the columns added since; the current model cannot insert into it.
            source_id=str(uuid4())
            await db.execute(text("""INSERT INTO v2_revisions (id,design_id,branch_id,clean_asset_id,root_source_asset_id,source_kind,view,context_checkpoint,created_at)
                VALUES (:id,:design,:branch,:asset,:asset,'manual','front','{"kind":"fresh"}',now())"""),
                {"id":source_id,"design":design,"branch":branch,"asset":asset.id})
        else:
            source=Revision(design_id=design,branch_id=branch,clean_asset_id=asset.id,
                root_source_asset_id=asset.id,source_kind="manual",context_checkpoint={"kind":"fresh"})
            db.add(source);await db.flush();source_id=source.id
        b=await db.get(Branch,branch);b.current_revision_id=source_id;b.state_version=1
        return owner,design,branch,source_id,asset.id


async def state(db,owner,design,branch):
    d=await db.get(Design,design);b=await db.get(Branch,branch);w=await db.get(Wallet,owner)
    return (d.event_sequence,d.last_branch_id,b.current_revision_id,b.state_version,b.active_epoch_id,w.balance,w.reserved,
        await db.scalar(select(func.count()).select_from(Operation)),
        await db.scalar(select(func.count()).select_from(Revision)),
        await db.scalar(select(func.count()).select_from(Branch)))


async def test_freeze_is_readonly_owned_historical_and_front_bound(factory,tmp_path):
    owner,design,branch,source,asset=await seed(factory,tmp_path)
    foreign,_,foreign_branch,foreign_revision,_=await seed(factory,tmp_path)
    async with factory.begin() as db:
        other=await create_design(db,owner,str(uuid4()),"Other")
        side=Revision(design_id=design,branch_id=branch,clean_asset_id=asset,
            root_source_asset_id=asset,source_kind="ai",view="left_profile")
        db.add(side);await db.flush();side_id=side.id
        b=await db.get(Branch,branch);b.current_revision_id=side.id;b.state_version=2
        other_branch=other.last_branch_id
    options=ViewSetOptions(allow_back_inference=True)
    async with factory() as db:before=await state(db,owner,design,branch)
    async with factory.begin() as db:
        snapshot=await freeze_viewset(db,owner,branch,source_revision_id=source,expected_branch_version=2,options=options)
        assert snapshot.source.revision_id==source and snapshot.source.image.asset_id==asset
        assert snapshot.source.number==1
    for user,target,revision,version,opts,code in [
        (foreign,branch,source,2,options,"not_found"),
        (owner,branch,foreign_revision,2,options,"not_found"),
        (owner,other_branch,source,0,options,"not_found"),
        (owner,branch,side_id,2,options,"viewset_front_required"),
        (owner,branch,source,1,options,"state_conflict"),
        (owner,branch,source,2,ViewSetOptions(),"viewset_inference_required"),
    ]:
        with pytest.raises(ApplicationError,match=code):
            async with factory.begin() as db:
                await freeze_viewset(db,user,target,source_revision_id=revision,expected_branch_version=version,options=opts)
    async with factory() as db:assert await state(db,owner,design,branch)==before


async def test_frozen_views_prepare_original_bytes_after_current_front_changes(factory,tmp_path):
    owner,design,branch,source,asset_id=await seed(factory,tmp_path)
    async with factory.begin() as db:
        snapshot=await freeze_viewset(db,owner,branch,source_revision_id=source,expected_branch_version=1,
            options=ViewSetOptions(allow_back_inference=True,confirmed_features=("coral wig",)))
        asset=await db.get(Asset,asset_id);path=asset_path(asset,tmp_path);original=path.read_bytes()
        latest=Revision(design_id=design,branch_id=branch,clean_asset_id=asset_id,
            root_source_asset_id=asset_id,source_kind="manual")
        db.add(latest);await db.flush()
        b=await db.get(Branch,branch);b.current_revision_id=latest.id;b.state_version=2
        records=[]
        for view in snapshot.generated_views:
            inputs=generation_snapshot(snapshot,view,expected_digest=snapshot.digest())
            record=InputRecord(branch_id=branch,digest=inputs.digest(),snapshot=inputs.model_dump(mode="json"))
            db.add(record);await db.flush();records.append((view,record.id))
    async with factory() as db:
        before=await state(db,owner,design,branch)
        for view,record_id in records:
            operation=Operation(owner_id=owner,input_snapshot_id=record_id)
            _,transport=await prepare(db,operation,workspace=tmp_path/view,storage_root=tmp_path,max_images=1,max_bytes=100000)
            images=[item for item in transport if item["type"]=="localImage"]
            assert len(images)==1 and Path(images[0]["path"]).read_bytes()==original
        assert await state(db,owner,design,branch)==before
        assert path.read_bytes()==original
        path.write_bytes(b"changed after snapshot")
        with pytest.raises(ApplicationError,match="input_asset_changed"):
            await prepare(db,operation,workspace=tmp_path/"retry",storage_root=tmp_path,max_images=1,max_bytes=100000)


@pytest.mark.parametrize("field,value,code", [
    ("archived_at","now","design_archived"),("deleted_at","now","not_found"),
])
async def test_unavailable_design_cannot_freeze_new_views(factory,tmp_path,field,value,code):
    from app.core.time import utc_now
    owner,design,branch,source,_=await seed(factory,tmp_path)
    async with factory.begin() as db:setattr(await db.get(Design,design),field,utc_now())
    with pytest.raises(ApplicationError,match=code):
        async with factory.begin() as db:
            await freeze_viewset(db,owner,branch,source_revision_id=source,expected_branch_version=1,
                options=ViewSetOptions(allow_back_inference=True))

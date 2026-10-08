from sqlalchemy import event as orm_event
from app.design.manual import SaveManualRevision, save_manual
from app.design.models import Branch, Revision
from .test_operations import factory, quoted, request, submit_request
from .test_migrations import migration_database
from .test_worker import prepared
from .test_history_boundaries import next_turn, revision_for


async def test_manual_ai_and_regenerated_side_revisions_keep_view_and_main_front(factory,tmp_path):
    worker,runtime,owner,main_branch,first=await prepared(factory,tmp_path)
    await worker.run_once()
    front=await revision_for(factory,first)
    async with factory.begin() as db:
        side_branch=Branch(design_id=front.design_id,context_seed={"kind":"fresh"})
        db.add(side_branch);await db.flush();side_id=side_branch.id
    payload=await quoted(factory,owner,side_id,request(client_request_id="initial-side",text="Left profile"))
    operation_id=await submit_request(factory,owner,side_id,payload)
    # The fixture supplies the initial view identity at insertion; immutable revisions stay immutable.
    # View-set dispatch itself remains disabled until group publication and settlement are implemented.
    def seed_view(_mapper,_connection,revision):
        if revision.branch_id==side_id:revision.view="left_profile"
    orm_event.listen(Revision,"before_insert",seed_view)
    try:
        await worker.run_once()
    finally:
        orm_event.remove(Revision,"before_insert",seed_view)
    side=await revision_for(factory,operation_id)
    async with factory.begin() as db:
        manual=await save_manual(db,owner,side_id,SaveManualRevision(client_request_id="manual-side",
            base_revision_id=side.id,expected_branch_version=1,clean_asset_id=side.clean_asset_id,recipe={"faceWidth":0.1}))
        assert manual.view=="left_profile"
        assert manual.root_source_asset_id==side.root_source_asset_id
    _,edited=await next_turn(factory,worker,owner,side_id,"Slight smile","edit-side")
    assert edited.view=="left_profile"
    # The original side was generated from an empty base: view cannot be inferred from its input.
    payload=await quoted(factory,owner,side_id,request(client_request_id="regenerate-side",text="",
        expected_branch_version=3,regenerate_revision_id=side.id))
    retry=await submit_request(factory,owner,side_id,payload)
    await worker.run_once()
    regenerated=await revision_for(factory,retry)
    assert regenerated.view=="left_profile"
    assert regenerated.parent_revision_id is None
    assert regenerated.branch_id!=side_id
    async with factory() as db:
        main=await db.get(Branch,main_branch)
        assert main.current_revision_id==front.id and main.state_version==1
        assert (await db.get(Branch,side_id)).current_revision_id==edited.id
        assert (await db.get(Revision,front.id)).view=="front"

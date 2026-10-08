from uuid import uuid4
from sqlalchemy import func, select
from app.core.time import utc_now
from app.design.models import Branch, Design, Operation, Wallet
from .test_api import api
from .test_migrations import migration_database
from .test_history_pages import seed_history
from .test_operations import setup


async def test_exact_revision_read_is_private_readonly_and_design_bound(api):
    client, factory, owner, design, branch = api
    old = await seed_history(factory, owner, design, branch, rounds=2)
    async with factory.begin() as db:
        sibling = Branch(design_id=design, parent_branch_id=branch, history_parent_sequence=0)
        db.add(sibling);await db.flush()
        sibling_id=sibling.id
    different = await seed_history(factory, owner, design, sibling_id, rounds=1)
    foreign_owner, foreign, foreign_branch = await setup(factory)
    foreign_revision = (await seed_history(factory, foreign_owner, foreign, foreign_branch, rounds=1))[-1]
    other_response=await client.post("/api/v2/designs",json={"client_request_id":str(uuid4()),"title":"Other owned design"})
    assert other_response.status_code==200
    other_owned=other_response.json()["id"]
    async with factory() as db:
        d=await db.get(Design,design);w=await db.get(Wallet,owner)
        before=(d.event_sequence,d.last_branch_id,d.updated_at,w.balance,w.reserved,
                await db.scalar(select(func.count()).select_from(Operation)),
                await db.scalar(select(func.count()).select_from(Branch)))
    for revision in (old[1],different[-1]):
        response=await client.get(f"/api/v2/designs/{design}/revisions/{revision}")
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="private, no-store"
        data=response.json()
        assert data["id"]==revision and data["width"]==16 and data["height"]==16
        assert "thread" not in repr(data) and "context_checkpoint" not in data
    for target,revision in ((design,foreign_revision),(foreign,foreign_revision),(foreign,old[1]),(other_owned,old[1]),(design,str(uuid4()))):
        response=await client.get(f"/api/v2/designs/{target}/revisions/{revision}")
        assert response.status_code==404,response.text
    async with factory() as db:
        d=await db.get(Design,design);w=await db.get(Wallet,owner)
        after=(d.event_sequence,d.last_branch_id,d.updated_at,w.balance,w.reserved,
               await db.scalar(select(func.count()).select_from(Operation)),
               await db.scalar(select(func.count()).select_from(Branch)))
    assert before==after
    async with factory.begin() as db:
        (await db.get(Design,design)).deleted_at=utc_now()
    assert (await client.get(f"/api/v2/designs/{design}/revisions/{old[1]}")).status_code==404

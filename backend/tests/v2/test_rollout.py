import asyncio
from uuid import uuid4
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from app.admin.rollout import Policy, allows, configure, inspect, require_admission
from app.auth.models import UserRole
from app.db.models import User
from app.design.models import AdminAction, Branch, Operation, Quote, RuntimeControl, Wallet
from app.design.service import ApplicationError, create_design
from app.operations.service import quote, submit
from app.operations.cancel import cancel
from .test_operations import factory, setup, request, quoted, submit_request
from .test_migrations import migration_database
from .test_api import api

OWNER = str(uuid4())
DESIGN = str(uuid4())


@pytest.mark.parametrize("restricted,account,project,expected", [
    (False,None,None,True), (True,None,None,False), (True,True,None,True),
    (True,None,True,True), (True,False,True,False), (True,True,False,False),
    (False,False,None,False), (False,None,False,False),
])
def test_policy_is_stable_and_explicit_denial_wins(restricted,account,project,expected):
    policy = Policy(restricted=restricted, owners={} if account is None else {OWNER:account},
                    designs={} if project is None else {DESIGN:project})
    assert all(allows(policy, OWNER, DESIGN) is expected for _ in range(10))


@pytest.mark.parametrize("payload", [
    {"restricted":"false"}, {"restricted":True,"owners":{"not-a-uuid":True}},
    {"restricted":True,"owners":{OWNER:"yes"}}, {"restricted":True,"unexpected":True},
])
def test_invalid_policy_fails_closed(payload):
    with pytest.raises(ValidationError):
        Policy.model_validate(payload)


async def admin(factory):
    owner, _, _ = await setup(factory)
    async with factory.begin() as db:
        (await db.get(User,owner)).role = UserRole.admin
    return owner


async def change(factory, actor, policy):
    async with factory.begin() as db:
        before = await inspect(db)
        return await configure(db, actor, str(uuid4()), "Approved internal cohort",
                               before["digest"], policy)


async def test_configure_is_atomic_audited_and_replay_does_not_restore_old_policy(factory):
    actor = await admin(factory)
    owner, design, _ = await setup(factory)
    policy = Policy(restricted=True, designs={design:True})
    async with factory.begin() as db:
        db.add(RuntimeControl(scope="global", blocked=True))
        before = await inspect(db)
    args = (actor,"rollout-change-001","Internal cohort",before["digest"],policy)
    async with factory.begin() as db:
        result = await configure(db,*args)
    async with factory.begin() as db:
        assert (await db.get(RuntimeControl,"global")).blocked
        assert (await inspect(db)) == result["after"]
        assert (await db.get(Wallet,owner)).write_owner == "v2"
    await change(factory,actor,Policy(restricted=True))
    async with factory.begin() as db:
        assert await configure(db,*args) == result
        assert (await inspect(db))["policy"]["designs"] == {}
        assert await db.scalar(select(func.count()).select_from(AdminAction)) == 2
    with pytest.raises(ApplicationError,match="rollout_policy_conflict"):
        async with factory.begin() as db:
            await configure(db,actor,"stale-request-001","Internal cohort",before["digest"],policy)
    with pytest.raises(ApplicationError,match="idempotency_conflict"):
        async with factory.begin() as db:
            await configure(db,*args[:-1],Policy(restricted=False))


async def test_invalid_actor_or_resource_cannot_change_policy(factory):
    owner, _, _ = await setup(factory)
    actor = await admin(factory)
    async with factory.begin() as db:
        before = await inspect(db)
    for user,policy,code in [
        (owner,Policy(restricted=True),"admin_required"),
        (actor,Policy(restricted=True,designs={str(uuid4()):True}),"not_found"),
    ]:
        with pytest.raises(ApplicationError,match=code):
            async with factory.begin() as db:
                await configure(db,user,str(uuid4()),"Internal cohort",before["digest"],policy)
    async with factory.begin() as db:
        assert await inspect(db) == before
        assert await db.scalar(select(func.count()).select_from(AdminAction)) == 0


async def test_design_cohort_covers_new_branches_without_admitting_other_designs(factory):
    actor = await admin(factory)
    owner, design, branch = await setup(factory)
    async with factory.begin() as db:
        other = await create_design(db,owner,"second-design","Second")
    await change(factory,actor,Policy(restricted=True,designs={design:True}))
    await quoted(factory,owner,branch)
    async with factory.begin() as db:
        fork = Branch(design_id=design, parent_branch_id=branch)
        db.add(fork)
        await db.flush()
    await quoted(factory,owner,fork.id)
    with pytest.raises(ApplicationError,match="rollout_not_enabled"):
        await quoted(factory,owner,other.last_branch_id)
    await change(factory,actor,Policy(restricted=True,owners={owner:True}))
    await quoted(factory,owner,other.last_branch_id)


async def test_revoking_after_quote_blocks_new_reservation_but_keeps_replay_and_cancel(factory):
    actor = await admin(factory)
    owner, design, branch = await setup(factory)
    payload = await quoted(factory,owner,branch)
    await change(factory,actor,Policy(restricted=True))
    with pytest.raises(ApplicationError,match="rollout_not_enabled"):
        await submit_request(factory,owner,branch,payload)
    async with factory.begin() as db:
        assert await db.scalar(select(func.count()).select_from(Operation)) == 0
        assert (await db.get(Wallet,owner)).reserved == 0
    await change(factory,actor,Policy(restricted=True,owners={owner:True}))
    operation = await submit_request(factory,owner,branch,payload)
    await change(factory,actor,Policy(restricted=True))
    assert await submit_request(factory,owner,branch,payload) == operation
    async with factory.begin() as db:
        await cancel(db,owner,operation)
    async with factory.begin() as db:
        wallet = await db.get(Wallet,owner)
        assert wallet.reserved == 0 and wallet.balance == 5 and wallet.write_owner == "v2"


async def test_policy_update_waits_for_admission_transaction(factory):
    actor = await admin(factory)
    owner,design,_ = await setup(factory)
    entered = asyncio.Event()
    async def update():
        entered.set()
        return await change(factory,actor,Policy(restricted=True))
    task = None
    try:
        async with factory.begin() as db:
            await require_admission(db,owner,design)
            task = asyncio.create_task(update())
            await entered.wait()
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task),timeout=0.2)
        await asyncio.wait_for(task,timeout=5)
        with pytest.raises(ApplicationError,match="rollout_not_enabled"):
            async with factory.begin() as db:
                await require_admission(db,owner,design)
    finally:
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)


async def test_http_denial_preserves_read_only_access_and_hides_foreign_design(api):
    client,factory,owner,design,branch = api
    actor = await admin(factory)
    other,_,foreign_branch = await setup(factory)
    await change(factory,actor,Policy(restricted=True))
    payload = {"client_request_id":"cohort-request","intent":"generate_or_clarify",
               "text":"coral wig","expected_branch_version":0}
    result = await client.post(f"/api/v2/branches/{branch}/quotes",json=payload)
    assert result.status_code == 403 and result.json()["code"] == "rollout_not_enabled"
    assert result.json()["quota_outcome"] == "unchanged"
    foreign = await client.post(f"/api/v2/branches/{foreign_branch}/quotes",json=payload)
    assert foreign.status_code == 404
    assert (await client.get(f"/api/v2/designs/{design}")).status_code == 200
    assert (await client.get("/api/v2/account/quota")).status_code == 200



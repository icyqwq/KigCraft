from uuid import uuid4
from app.core.time import utc_now
from sqlalchemy import func, select
from app.design.models import Branch, Design, Revision, Wallet, Operation
from .test_api import api
from .test_migrations import migration_database
from .test_history_pages import seed_history
from .test_operations import setup


async def test_all_schemes_revision_pages_are_fixed_private_and_read_only(api):
    client, factory, owner, design, branch = api
    expected = await seed_history(factory, owner, design, branch, rounds=43)
    async with factory.begin() as db:
        sibling = Branch(design_id=design, parent_branch_id=branch, history_parent_sequence=0)
        db.add(sibling)
        await db.flush()
        sibling_id = sibling.id
    expected += await seed_history(factory, owner, design, sibling_id, rounds=62)
    expected = expected[1::2]
    path = f"/api/v2/designs/{design}/revisions"
    ordinary = (await client.get(path, params={"limit":100})).json()
    assert len(ordinary["items"]) == 43
    response = await client.get(path, params={"scope":"design", "limit":17})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    page = response.json()
    assert page["total"] == 105 and page["watermark"] == 210
    assert page["next_cursor"] and not page["ancestry_incomplete"]
    # A late image on any scheme stays outside this pagination window.
    late = await seed_history(factory, owner, design, branch, rounds=1)
    async with factory() as db:
        d = await db.get(Design, design)
        w = await db.get(Wallet, owner)
        before = (d.event_sequence, d.last_branch_id, d.metadata_version, d.updated_at, w.balance, w.reserved,
                  (await db.scalars(select(Branch.state_version).where(Branch.design_id == design))).all(),
                  await db.scalar(select(func.count()).select_from(Operation)))
    found = []
    while True:
        found[0:0] = [r["id"] for r in page["items"]]
        assert page["total"] == 105 and page["watermark"] == 210
        assert all(r["width"] == 16 and r["height"] == 16 for r in page["items"])
        assert "thread_id" not in repr(page)
        if not page["next_cursor"]:
            break
        response = await client.get(path, params={"scope":"design","limit":17,"cursor":page["next_cursor"]})
        assert response.status_code == 200, response.text
        page = response.json()
    assert found == expected and len(set(found)) == 105
    current = (await client.get(path, params={"scope":"design"})).json()
    assert current["total"] == 106 and current["items"][-1]["id"] == late[-1]
    async with factory() as db:
        d = await db.get(Design, design)
        w = await db.get(Wallet, owner)
        after = (d.event_sequence, d.last_branch_id, d.metadata_version, d.updated_at, w.balance, w.reserved,
                 (await db.scalars(select(Branch.state_version).where(Branch.design_id == design))).all(),
                 await db.scalar(select(func.count()).select_from(Operation)))
    assert after == before


async def test_revision_catalog_authorization_and_cursor_scope_are_rechecked(api):
    client, factory, owner, design, branch = api
    await seed_history(factory, owner, design, branch, rounds=3)
    path = f"/api/v2/designs/{design}/revisions"
    all_page = (await client.get(path, params={"scope":"design","limit":1})).json()
    branch_page = (await client.get(path, params={"limit":1})).json()
    for params in (
        {"scope":"design","cursor":branch_page["next_cursor"]},
        {"cursor":all_page["next_cursor"]},
        {"scope":"design","branch_id":branch},
        {"scope":"design","cursor":"garbage"},
    ):
        response = await client.get(path, params=params)
        assert response.status_code == 400, response.text
        assert response.json()["code"] == "invalid_history_cursor"
    created = await client.post("/api/v2/designs", json={"client_request_id":str(uuid4()),"title":"Other design"})
    assert created.status_code in (200,201)
    other = created.json()["id"]
    mixed = await client.get(f"/api/v2/designs/{other}/revisions",
                             params={"scope":"design","cursor":all_page["next_cursor"]})
    assert mixed.status_code == 400 and mixed.json()["code"] == "invalid_history_cursor"
    _, foreign, _ = await setup(factory)
    for target in (foreign, str(uuid4())):
        response = await client.get(f"/api/v2/designs/{target}/revisions",
                                    params={"scope":"design","cursor":all_page["next_cursor"]})
        assert response.status_code == 404
    async with factory.begin() as db:
        (await db.get(Design, design)).deleted_at = utc_now()
    assert (await client.get(path, params={"scope":"design","cursor":all_page["next_cursor"]})).status_code == 404

"""Cancellation after delivery cannot rewrite a completed outcome or its ledger."""
import hashlib
import pytest
from sqlalchemy import select, text
from app.design.models import Asset, Wallet
from app.operations.cancel import cancel
from app.operations.lifecycle import finish
from app.operations.publish import publish
from .test_operations import factory, setup, quoted, submit_request, running, staged
from .test_migrations import migration_database

async def fingerprint(factory):
    async with factory() as db:
        return {table: sorted(await db.scalars(text('SELECT to_jsonb(t)::text FROM "'+table+'" t')))
                for table in ("v2_operations","v2_revisions","v2_assets","v2_wallets","v2_ledger","v2_events","v2_branches")}

@pytest.mark.parametrize("upstream,expected",[
    ("completed","succeeded"),("failed","partial_success"),("interrupted","partial_success"),
])
async def test_repeated_cancel_of_terminal_delivery_is_a_noop(factory,tmp_path,upstream,expected):
    owner,_,branch=await setup(factory)
    opid=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    token=await running(factory,opid)
    image=staged(tmp_path)
    async with factory.begin() as db:
        revision=await publish(db,opid,"worker",token,"main_image",image,storage_root=tmp_path/"objects",finalize=False)
        asset=await db.get(Asset,revision.clean_asset_id)
        path=tmp_path/"objects"/asset.object_key
        assert await finish(db,opid,"worker",token,upstream_status=upstream)==expected
    before=await fingerprint(factory)
    original=hashlib.sha256(path.read_bytes()).hexdigest()
    for _ in range(2):
        async with factory.begin() as db:
            assert await cancel(db,owner,opid)==expected
    assert await fingerprint(factory)==before
    assert hashlib.sha256(path.read_bytes()).hexdigest()==original
    async with factory() as db:
        wallet=await db.get(Wallet,owner)
        assert (wallet.balance,wallet.reserved)==(4,0)

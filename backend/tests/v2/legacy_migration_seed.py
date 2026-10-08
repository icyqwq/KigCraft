"""Historical migration seeds use old SQL shapes, never current design services."""
import hashlib
import json
from uuid import uuid4
from sqlalchemy import text
from app.db.models import User
from app.design.models import Wallet
from app.generation.quota import current_generation_quota_window
from app.quota.wallet import entry


async def setup(factory):
    async with factory.begin() as db:
        user=User(email=f"{uuid4()}@example.com")
        db.add(user)
        await db.flush()
        wallet=Wallet(owner_id=user.id,balance=0,reserved=0,version=0,write_owner="v2",
                      last_topup_window=current_generation_quota_window()[0])
        db.add(wallet)
        await db.flush()
        await entry(db,wallet,key="opening",kind="opening",balance_delta=5,reserved_delta=0)
        design,branch=str(uuid4()),str(uuid4())
        await db.execute(text("""INSERT INTO v2_designs
            (id,owner_id,client_request_id,request_digest,title,metadata_version,event_sequence,
             pinned,last_activity_at,created_at,updated_at)
            VALUES (:design,:owner,'legacy-design','digest','Character',0,0,false,now(),now(),now())"""),
            {"design":design,"owner":user.id})
        await db.execute(text("""INSERT INTO v2_branches (id,design_id,state_version,created_at)
            VALUES (:branch,:design,0,now())"""),{"branch":branch,"design":design})
        await db.execute(text("UPDATE v2_designs SET last_branch_id=:branch WHERE id=:design"),
                         {"branch":branch,"design":design})
        return user.id,design,branch


async def operation(factory,owner,branch):
    async with factory.begin() as db:
        design=await db.scalar(text("SELECT design_id FROM v2_branches WHERE id=:branch"),{"branch":branch})
        identity,snapshot=str(uuid4()),str(uuid4())
        await db.execute(text("""INSERT INTO v2_input_snapshots
            (id,branch_id,schema_version,digest,snapshot,created_at)
            VALUES (:id,:branch,1,'digest','{}',now())"""),{"id":snapshot,"branch":branch})
        await db.execute(text("""INSERT INTO v2_operations
            (id,owner_id,design_id,branch_id,client_request_id,request_digest,input_snapshot_id,kind,state,
             expected_branch_version,reserved_units,settled_units,reservation_closed,fencing_token,
             cancel_requested,created_at,updated_at)
            VALUES (:id,:owner,:design,:branch,:id,'digest',:snapshot,'generate','queued',0,0,0,false,0,false,now(),now())"""),
            {"id":identity,"owner":owner,"design":design,"branch":branch,"snapshot":snapshot})
        return identity


async def historical_rows(db, name):
    """Exclude newly added fields only after proving they are unpopulated."""
    quoted = '"' + name.replace('"', '""') + '"'
    rows = list(await db.scalars(text("SELECT to_jsonb(t) FROM " + quoted + " t ORDER BY to_jsonb(t)::text")))
    for row in rows:
        for key in ("trash_restore_until", "content_purged_at", "retained_resources"):
            if key in row:
                assert row.pop(key) is None, "Migration unexpectedly populated " + key
        # Added with the defaulting migration v2_0028: every historical version is a head shell.
        if "image_kind" in row:
            assert row.pop("image_kind") == "head_shell", "Migration unexpectedly changed image_kind"
    return rows


async def fingerprints(factory):
    async with factory() as db:
        result = {}
        for name in (await db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))).all():
            values = [json.dumps(row, sort_keys=True) for row in await historical_rows(db, name)]
            result[name] = hashlib.sha256("\n".join(values).encode()).hexdigest()
        return result

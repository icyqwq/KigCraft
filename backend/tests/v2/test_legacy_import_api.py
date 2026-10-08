from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from app.auth.sessions import _hash_token
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession
from app.design.models import Revision,LegacyImport
from .test_migrations import migration_database
from .test_api import api
from .test_legacy_import import prepared,apply
from .test_legacy_inventory import fingerprints

async def test_imported_image_history_and_minimal_sources_are_owner_only(api,tmp_path,monkeypatch):
    client,factory,other,_,_=api
    ids,report=await prepared(factory,tmp_path/"old")
    store=tmp_path/"new"
    monkeypatch.setenv("V2_STORAGE_DIR",str(store));get_settings.cache_clear()
    result=await apply(factory,ids,report,tmp_path/"old",store)
    async with factory() as db:
        mapping=await db.scalar(select(LegacyImport).where(LegacyImport.source_kind=="output",LegacyImport.source_id==ids["output"]))
        revision=await db.get(Revision,mapping.revision_id)
    url=f"/api/v2/revisions/{revision.id}/legacy"
    assert (await client.get(url)).status_code==404
    assert (await client.get("/api/v2/assets/"+revision.clean_asset_id)).status_code==404
    token="legacy-"+uuid4().hex
    async with factory.begin() as db:
        db.add(AuthSession(user_id=ids["owner"],session_token_hash=_hash_token(token),
            expires_at=utc_now()+timedelta(hours=1),created_ip="127.0.0.1"))
    client.cookies.clear();client.cookies.set(get_settings().session_cookie_name,token)
    before=await fingerprints(factory)
    snapshot=await client.get("/api/v2/designs/"+revision.design_id)
    assert snapshot.status_code==200 and snapshot.json()["messages"]==[] and snapshot.json()["operations"]==[]
    assert snapshot.json()["legacy_imported"] and len(snapshot.json()["revisions"])==1
    versions=(await client.get("/api/v2/designs/"+revision.design_id+"/revisions?scope=design")).json()
    assert len(versions["items"])==2
    original=(await client.get("/api/v2/designs/"+revision.design_id+"/legacy")).json()
    assert len(original["items"])==5 and sum(r["source_kind"]=="job" for r in original["items"])==2
    provenance=await client.get(url+"?limit=1")
    assert provenance.status_code==200 and provenance.headers["cache-control"]=="private, no-store"
    page=provenance.json();items=page["items"]
    assert page["next_cursor"]
    items+=(await client.get(url+"?limit=1&cursor="+page["next_cursor"])).json()["items"]
    assert len(items)==2 and {row["source_kind"] for row in items}=={"output","album"}
    assert all(row["provider"]["provider"]=="seedream" and row["provider"]["analysis_provider"]=="qwen" for row in items)
    assert all(row["legacy_execution"]=="completed" and row["legacy_selection"]=="confirmed" and not row["recipe_replayed"] for row in items)
    assert all(value not in str(items) for value in ("PRIVATE","original_metadata","original_recipe","image_url","object_key"))
    image=await client.get("/api/v2/assets/"+revision.clean_asset_id)
    from app.images.delivery import watermarked_jpeg
    stored=store/(await asset_key(factory,revision.clean_asset_id))
    # Imported generated images are AI output too: delivered watermarked, stored object unchanged.
    assert image.status_code==200 and image.content==watermarked_jpeg(stored,key="expected-legacy",locale="zh-CN")
    assert image.content!=stored.read_bytes()
    assert await fingerprints(factory)==before

async def asset_key(factory,identity):
    from app.design.models import Asset
    async with factory() as db:return (await db.get(Asset,identity)).object_key

"""Private HTTP/iterator components using isolated PostgreSQL; no live service or E2E."""
import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, HTTPException
import pytest
from sqlalchemy import select

from app.admin import access_observations as observations
from app.admin.access_collector import AccessCollector
from app.admin.access_requests import capture, observed_stream
from app.core.config import get_settings, Settings
from app.core.time import utc_now
from app.db.models import AlbumItem, AuthSession, GenerationJob, GenerationOutput, Project, ReferenceAsset, User
from app.design import router
from app.design.models import Design
from app.design.service import event
from .test_api import api
from .test_operations import setup
from .test_migrations import migration_database
from .test_access_observations import counters
from .test_session_private_reads import StreamRequest


def count(values, category):
    return sum(v for k, v in values.items() if k.endswith(":" + category))


async def legacy_records(factory):
    owner, _, _ = await setup(factory)
    async with factory.begin() as db:
        project = Project(user_id=owner, name="Private fixture")
        db.add(project)
        await db.flush()
        job = GenerationJob(user_id=owner, project_id=project.id, status="succeeded", runtime_status="succeeded")
        db.add(job)
        await db.flush()
        key = "private-fixture/" + str(uuid4()) + ".png"
        db.add(GenerationOutput(job_id=job.id, index=0, object_key=key, image_url="/api/generated/" + key))
        ref = ReferenceAsset(user_id=owner, kind="extra", object_key=f"references/{uuid4()}/private.png",
            file_name="private.png", mime_type="image/png")
        album = AlbumItem(user_id=owner, image_url="/api/generated/fixture.png")
        db.add_all([ref, album])
        await db.flush()
    return owner, job.id, key, ref.object_key, album.id


@pytest.mark.parametrize("kind", ["job", "job_events", "reference", "generated", "album"])
async def test_legacy_foreign_and_missing_remain_indistinguishable(api, kind, tmp_path):
    client, factory, _, _, _ = api
    owner, job, key, ref, album = await legacy_records(factory)
    if kind == "generated":
        settings = get_settings()
        settings.codex_output_dir = str(tmp_path)
        file = tmp_path / key
        file.parent.mkdir(parents=True)
        file.write_bytes(b"fixture")
    if kind == "album":
        denied = await client.post("/api/album/items", json={"image_url":f"/api/generated/album/{album}/private.png"})
        missing = await client.post("/api/album/items", json={"image_url":f"/api/generated/album/{uuid4()}/private.png"})
    else:
        url = {
            "job": f"/api/generation/jobs/{job}",
            "job_events": f"/api/generation/jobs/{job}/events",
            "reference": "/api/references/" + ref,
            "generated": "/api/generated/" + key,
        }[kind]
        denied = await client.get(url)
        # Keep the same route and path shape, but remove the resource association.
        missing_url = url.replace(job, str(uuid4())) if kind.startswith("job") else (
            "/api/references/references/" + str(uuid4()) + "/private.png" if kind == "reference"
            else "/api/generated/missing/" + str(uuid4()) + ".png")
        missing = await client.get(missing_url)
    assert denied.status_code == missing.status_code == 404
    assert denied.content == missing.content and dict(denied.headers) == dict(missing.headers)
    values = await counters(factory)
    assert count(values, "cross") == count(values, "miss") == 1
    assert owner not in repr(values) and key not in repr(values) and "private" not in repr(values)


async def test_unauthenticated_hidden_legacy_404_never_looks_up_owners(api, monkeypatch):
    client, factory, _, _, _ = api
    client.cookies.clear()
    def forbidden_query(*args):
        raise AssertionError("Unauthenticated observation must not query ownership")
    monkeypatch.setattr(observations, "resource_queries", forbidden_query)
    response = await client.get("/api/generated/private.png")
    assert response.status_code == 404 and response.json() == {"detail":"Generated asset not found"}
    values = await counters(factory)
    assert count(values, "unauthenticated") == 1 and count(values, "cross") == 0


@pytest.mark.parametrize("status", [400, 403, 409, 422, 429])
async def test_other_http_rejections_observe_known_path_references(api, status):
    client, factory, _, _, _ = api
    _, foreign, _ = await setup(factory)
    app = client._transport.app
    @app.get("/v2/probe/{design_id}")
    async def probe(design_id: str, current=Depends(router.user)):
        raise HTTPException(status, detail="same refusal", headers={"Retry-After":"3"})
    result = await client.get("/v2/probe/" + foreign)
    assert result.status_code == status and result.json() == {"detail":"same refusal"}
    assert result.headers["retry-after"] == "3"
    assert count(await counters(factory), "cross") == 1


async def test_request_validation_observed_without_changing_error(api):
    client, factory, _, design, _ = api
    result = await client.get(f"/api/v2/designs/{design}/events?after=invalid")
    assert result.status_code == 422
    assert result.json()["detail"][0]["loc"] == ["query", "after"]
    assert count(await counters(factory), "miss") == 1


@pytest.mark.parametrize("reason", ["expired", "banned", "deleted", "changed_session"])
async def test_existing_stream_stops_and_counts_once_without_emitting_private_event(api, reason):
    client, factory, owner, design, _ = api
    token = client.cookies.get(get_settings().session_cookie_name)
    request = StreamRequest(token, design)
    async with factory.begin() as db:
        cursor = (await db.get(Design, design)).event_sequence
        await event(db, design, "message.delta", {"text":"before"})
        user = await db.get(User, owner)
    response = await router.events(design, request, after=cursor, current=user)
    assert "before" in await anext(response.body_iterator)
    async with factory.begin() as db:
        if reason == "expired":
            session = await db.scalar(select(AuthSession).where(AuthSession.user_id == owner))
            session.expires_at = utc_now() - timedelta(seconds=1)
        elif reason == "banned":
            (await db.get(User, owner)).is_banned = True
        elif reason == "deleted":
            (await db.get(Design, design)).deleted_at = utc_now()
        else:
            # A different authenticated identity in an already-bound stream is refused.
            other, _, _ = await setup(factory)
            session = await db.scalar(select(AuthSession).where(AuthSession.user_id == owner))
            session.user_id = other
        await event(db, design, "message.delta", {"text":"must not be emitted"})
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(response.body_iterator), 4)
    await response.body_iterator.aclose()
    values = await counters(factory)
    assert count(values, "stream_closed") == 1 and count(values, "cross") == 0
    category = "forbidden" if reason == "banned" else "miss" if reason == "deleted" else "unauthenticated"
    assert count(values, category) == 1


async def test_normal_stream_completion_is_not_a_rejection(api):
    _, factory, _, design, _ = api
    request = StreamRequest("irrelevant", design)
    async def source():
        yield "heartbeat"
    assert [part async for part in observed_stream(request, source())] == ["heartbeat"]
    assert await counters(factory) == {}


async def test_stream_observation_failure_marks_gap_and_does_not_emit_exception(api, monkeypatch):
    client, factory, _, design, _ = api
    request = StreamRequest(client.cookies.get(get_settings().session_cookie_name), design)
    collector = AccessCollector(factory=factory)
    request.app.state.access_collector = collector
    await collector.pulse()
    request.state.access_stream_rejected = True
    request.state.access_auth_failure = "unauthenticated"
    async def broken(*args):
        raise RuntimeError("private database details")
    monkeypatch.setattr(observations, "increment", broken)
    async def source():
        if False:
            yield ""
    assert [part async for part in observed_stream(request, source())] == []
    async with factory() as db:
        result = await observations.recent_denials(db, Settings(_env_file=None))
    assert result["coverage"]["gap_recorded"] and result["count"] is None
    assert "private" not in repr(await counters(factory))


async def test_recorded_categories_do_not_raise_cross_account_alert(api):
    client, factory, _, design, _ = api
    collector = AccessCollector(factory=factory)
    await collector.pulse()
    client.cookies.clear()
    assert (await client.get("/api/v2/designs/" + design)).status_code == 401
    async with factory() as db:
        result = await observations.recent_denials(db, Settings(_env_file=None))
    assert result["count"] == 0 and result["categories"]["unauthenticated"] == 1
    assert result["coverage"]["scope"] == "private_resource_rejections"


async def test_invalid_body_keeps_validation_response_and_observes_allowlisted_reference(api):
    client, factory, _, _, branch = api
    from .test_access_references import foreign_asset
    _, asset = await foreign_asset(factory)
    response = await client.post(f"/api/v2/branches/{branch}/quotes", json={
        "client_request_id":"invalid-fixture","intent":"generate_or_clarify","text":"private",
        "expected_branch_version":"invalid", "attachments":[{"asset_id":asset,"usage":"private"}]})
    assert response.status_code == 422
    assert count(await counters(factory), "cross") == 1
    assert "private" not in repr(await counters(factory))


async def test_editor_checkpoint_reference_is_an_explicit_field():
    from .test_access_references import make_request
    from app.admin.access_references import request_references
    request = make_request("POST", "/", b'{"expected_checkpoint_id":"checkpoint","text":"ignored"}')
    assert await request_references(request, "/v2/branches/{branch_id}/editor-saves") == (
        [("checkpoint","checkpoint")], True)


async def test_forbidden_resource_rejection_is_recorded_in_both_views(api):
    client, factory, _, _, _ = api
    _, foreign, _ = await setup(factory)
    app = client._transport.app
    @app.get("/v2/denial-probe/{design_id}")
    async def probe(design_id: str, current=Depends(router.user)):
        raise HTTPException(403, detail="refused")
    assert (await client.get("/v2/denial-probe/"+foreign)).status_code == 403
    values = await counters(factory)
    assert count(values, "forbidden") == 1 and count(values, "cross") == 1
    assert values[observations.PREFIX+"observed"] == 1

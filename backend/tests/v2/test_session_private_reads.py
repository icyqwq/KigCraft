"""A29: real cookie/session checks on private resources and an already-open SSE iterator."""
import asyncio
from types import SimpleNamespace
from starlette.requests import Request
import io
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from PIL import Image
import pytest
from sqlalchemy import select
from app.auth.sessions import _hash_token
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession, User
from app.design.models import Asset, Design
from app.design.service import event
from app.design import router
from .test_api import api
from .test_migrations import migration_database
from .test_operations import setup, quoted, submit_request, running, staged
from .test_drafts import request as draft_request
from app.operations.publish import publish
from app.operations.lifecycle import finish
from .test_account_sessions import seed


class StreamRequest(Request):
    def __init__(self, token, design):
        super().__init__({"type":"http","method":"GET","path":f"/api/v2/designs/{design}/events",
            "query_string":b"", "headers":[(b"cookie", f"{get_settings().session_cookie_name}={token}".encode())],
            "path_params":{"design_id":design},
            "route":SimpleNamespace(path="/api/v2/designs/{design_id}/events"),
            "app":SimpleNamespace(state=SimpleNamespace(access_collector=None))})
    async def is_disconnected(self):
        return False


@pytest.mark.parametrize("reason", ["logout", "expired", "banned", "revoke_others"])
async def test_old_session_loses_private_reads_and_existing_stream(api, reason, tmp_path):
    client, factory, owner, design, branch = api
    cookie = get_settings().session_cookie_name
    token = client.cookies.get(cookie)
    png = io.BytesIO()
    Image.new("RGB", (16, 16), "coral").save(png, format="PNG")
    upload = await client.post("/api/v2/assets/uploads", files={"file": ("test.png", png.getvalue(), "image/png")})
    assert upload.status_code == 200
    asset_url = upload.json()["image_url"]
    opid = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    fence = await running(factory, opid)
    async with factory.begin() as db:
        revision = await publish(db, opid, "worker", fence, "main_image", staged(tmp_path),
                                 storage_root=Path(get_settings().v2_storage_dir), finalize=False)
        await finish(db, opid, "worker", fence, upstream_status="completed")
        root = await db.get(Asset, revision.root_source_asset_id)
        payload = draft_request(branch, revision, root)
    saved = await client.put("/api/v2/editor-drafts/" + str(uuid4()), json=payload.model_dump(mode="json"))
    assert saved.status_code == 200, saved.text
    draft_path = f"/api/v2/branches/{branch}/editor-drafts?base_revision_id={revision.id}"
    paths = [asset_url, f"/api/v2/designs/{design}", draft_path,
             "/api/v2/account/editor-drafts"]
    for path in paths:
        assert (await client.get(path)).status_code == 200, path
    async with factory.begin() as db:
        cursor = (await db.get(Design, design)).event_sequence
        await event(db, design, "message.delta", {"text": "authorized-before-revocation"})
        user = await db.get(User, owner)
    stream = await router.events(design, StreamRequest(token, design), after=cursor, current=user)
    assert "authorized-before-revocation" in await asyncio.wait_for(anext(stream.body_iterator), 3)
    replacement, _ = await seed(factory, owner)
    try:
        if reason == "logout":
            assert (await client.post("/api/auth/logout")).status_code == 200
        elif reason == "revoke_others":
            client.cookies.clear(); client.cookies.set(cookie, replacement)
            assert (await client.post("/api/auth/sessions/revoke-others")).status_code == 200
        else:
            async with factory.begin() as db:
                if reason == "banned":
                    (await db.get(User, owner)).is_banned = True
                else:
                    session = await db.scalar(select(AuthSession).where(AuthSession.session_token_hash == _hash_token(token)))
                    session.expires_at = utc_now() - timedelta(seconds=1)
        client.cookies.clear(); client.cookies.set(cookie, token)
        expected = 403 if reason == "banned" else 401
        for path in [*paths, f"/api/v2/designs/{design}/events"]:
            assert (await client.get(path)).status_code == expected, path
        async with factory.begin() as db:
            await event(db, design, "message.delta", {"text": "private-after-revocation"})
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream.body_iterator), 3)
        # Losing one session does not revoke another one, unless the account itself is banned.
        client.cookies.clear(); client.cookies.set(cookie, replacement)
        for path in paths:
            assert (await client.get(path)).status_code == (403 if reason == "banned" else 200)
        foreign, _, _ = await setup(factory)
        foreign_token, _ = await seed(factory, foreign)
        client.cookies.clear(); client.cookies.set(cookie, foreign_token)
        for path in [asset_url, f"/api/v2/designs/{design}", draft_path,
                     f"/api/v2/designs/{design}/events"]:
            assert (await client.get(path)).status_code == 404
    finally:
        await stream.body_iterator.aclose()

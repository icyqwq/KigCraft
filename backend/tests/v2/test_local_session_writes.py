"""Local edit HTTP authorization and exact replay against isolated PostgreSQL; no worker/model."""
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.auth.sessions import _hash_token
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthSession, User
from app.design.models import Asset, Branch, InputRecord, LedgerEntry, Operation, Revision, Wallet
from .test_account_sessions import seed
from .test_api import api
from .test_local_images import inputs, png
from .test_migrations import migration_database
from .test_operations import setup


@pytest.mark.parametrize("phase", ["uploaded", "quoted", "submitted"])
@pytest.mark.parametrize("reason", ["logout", "expired", "banned", "switch"])
async def test_local_writes_reject_old_identity_and_replay_once(api, monkeypatch, phase, reason):
    client, factory, owner, _, branch = api
    monkeypatch.setenv("V2_LOCAL_EDIT_ENABLED", "true")
    get_settings.cache_clear()
    cookie = get_settings().session_cookie_name
    token = client.cookies.get(cookie)
    base, mask, _ = inputs()
    assets = []
    for image in [base, mask, base]:
        response = await client.post("/api/v2/assets/uploads", params={"expected_owner_id": owner},
            files={"file": ("input.png", png(image), "image/png")})
        assert response.status_code == 200, response.text
        assets.append(response.json())
    async with factory.begin() as db:
        current = await db.get(Branch, branch)
        revision = Revision(design_id=current.design_id, branch_id=branch, clean_asset_id=assets[0]["id"],
                            root_source_asset_id=assets[0]["id"], source_kind="manual")
        db.add(revision)
        await db.flush()
        current.current_revision_id = revision.id
        current.state_version = 1
        revision_id = revision.id
    payload = {"client_request_id": str(uuid4()), "intent": "generate_or_clarify", "text": "Private selected edit",
        "base_revision_id": revision_id, "expected_branch_version": 1, "mask_asset_id": assets[1]["id"],
        "local_edit_mode": "lock_outside", "attachments": [],
        "prepared_edit": {"clean_asset_id": assets[2]["id"], "root_source_asset_id": assets[0]["id"],
            "root_sha256": assets[0]["sha256"], "width": 160, "height": 160,
            "recipe": {"imageCoordinates": {"version": 1, "width": 160, "height": 160, "effectScale": 1}}}}
    quote_path, submit_path = (f"/api/v2/branches/{branch}/" + suffix for suffix in ["quotes", "turns"])
    if phase != "uploaded":
        price = await client.post(quote_path, json=payload)
        assert price.status_code == 200, price.text
        payload["quote_id"] = price.json()["id"]
    original_operation = None
    if phase == "submitted":
        accepted = await client.post(submit_path, json=payload)
        assert accepted.status_code == 202, accepted.text
        original_operation = accepted.json()["id"]

    async def counts():
        async with factory() as db:
            wallet = await db.get(Wallet, owner)
            return {"assets": await db.scalar(select(func.count()).select_from(Asset)),
                "operations": await db.scalar(select(func.count()).select_from(Operation)),
                "inputs": await db.scalar(select(func.count()).select_from(InputRecord)),
                "reserves": await db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == "reserve")),
                "balance": wallet.balance, "reserved": wallet.reserved}
    before = await counts()
    if reason == "logout":
        assert (await client.post("/api/auth/logout")).status_code == 200
    elif reason == "switch":
        other = (await setup(factory))[0]
        other_token, _ = await seed(factory, other)
        client.cookies.clear()
        client.cookies.set(cookie, other_token)
        # Authorize the second synthetic account for this local-only test to reach ownership checks.
        monkeypatch.setenv("V2_LOCAL_USER_ID", other)
        get_settings.cache_clear()
    else:
        async with factory.begin() as db:
            if reason == "banned":
                (await db.get(User, owner)).is_banned = True
            else:
                session = await db.scalar(select(AuthSession).where(AuthSession.session_token_hash == _hash_token(token)))
                session.expires_at = utc_now() - timedelta(seconds=1)
    expected = 404 if reason == "switch" else 403 if reason == "banned" else 401
    rejected_upload = await client.post("/api/v2/assets/uploads", params={"expected_owner_id": owner},
        files={"file": ("late.png", png(base), "image/png")})
    assert rejected_upload.status_code == expected, rejected_upload.text
    for path in [quote_path, submit_path]:
        body = {key: value for key, value in payload.items() if key != "quote_id"} if path == quote_path else {**payload, "quote_id": payload.get("quote_id", "not-issued")}
        rejected = await client.post(path, json=body)
        assert rejected.status_code == expected, rejected.text
    assert await counts() == before

    # Restore authorization without changing any request that was already quoted/dispatched.
    async with factory.begin() as db:
        (await db.get(User, owner)).is_banned = False
    replacement, _ = await seed(factory, owner)
    client.cookies.clear()
    client.cookies.set(cookie, replacement)
    monkeypatch.setenv("V2_LOCAL_USER_ID", owner)
    get_settings.cache_clear()
    if phase == "uploaded":
        price = await client.post(quote_path, json=payload)
        assert price.status_code == 200, price.text
        payload["quote_id"] = price.json()["id"]
    first = await client.post(submit_path, json=payload)
    second = await client.post(submit_path, json=payload)
    assert first.status_code == second.status_code == 202, (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    if original_operation:
        assert first.json()["id"] == original_operation
    final = await counts()
    assert final == {"assets": 3, "operations": 1, "inputs": 1, "reserves": 1, "balance": 5, "reserved": 1}
    async with factory() as db:
        op = await db.get(Operation, first.json()["id"])
        assert op.kind == "local_edit" and op.state == "queued"
        assert op.owner_id == owner and op.settled_units == 0
        record = await db.get(InputRecord, op.input_snapshot_id)
        assert record.snapshot["local_edit_mode"] == "lock_outside"
        assert record.snapshot["prepared_edit"]["clean_asset_id"] == assets[2]["id"]

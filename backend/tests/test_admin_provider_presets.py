import json
from uuid import uuid4

from app.auth.admin_cli import create_or_update_admin
from app.core.config import get_settings


async def _register_user(async_client) -> dict:
    email = f"provider-user-{uuid4()}@example.com"
    send_response = await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )
    assert send_response.status_code == 200
    response = await async_client.post(
        "/api/auth/register",
        json={
            "email": email,
            "code": "123456",
            "password": "ProviderPassphrase7!",
            "terms_accepted": True,
            "terms_version": get_settings().current_terms_version,
        },
    )
    assert response.status_code == 200
    return response.json()


async def _login_admin(async_client) -> dict:
    email = f"provider-admin-{uuid4()}@example.com"
    await create_or_update_admin(email, "AdminPassphrase7!")
    response = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": "AdminPassphrase7!"},
    )
    assert response.status_code == 200
    return response.json()


async def test_non_admin_cannot_access_provider_admin_routes(async_client):
    await _register_user(async_client)

    responses = [
        await async_client.get("/api/admin/provider-presets"),
        await async_client.put(
            "/api/admin/provider-presets/active",
            json={"preset_id": "qwen_seedream"},
        ),
        await async_client.get("/api/admin/provider-usage"),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]


async def test_admin_can_inspect_and_switch_fixed_provider_presets(
    async_client, tmp_path, monkeypatch
):
    preset_path = tmp_path / "provider-preset.json"
    monkeypatch.setattr(get_settings(), "provider_preset_path", str(preset_path))
    admin = await _login_admin(async_client)

    initial_response = await async_client.get("/api/admin/provider-presets")
    switch_response = await async_client.put(
        "/api/admin/provider-presets/active",
        json={"preset_id": "codex_full"},
    )
    refreshed_response = await async_client.get("/api/admin/provider-presets")

    assert initial_response.status_code == 200
    initial = initial_response.json()
    assert initial["active"]["preset_id"] == "codex_full"
    assert {preset["id"] for preset in initial["presets"]} == {
        "codex_full",
    }
    assert switch_response.status_code == 200
    switched = switch_response.json()
    assert switched["preset_id"] == "codex_full"
    assert switched["updated_by"] == admin["id"]
    assert switched["updated_at"]
    assert refreshed_response.json()["active"] == switched

    persisted = json.loads(preset_path.read_text(encoding="utf-8"))
    assert persisted == switched

    serialized = json.dumps(refreshed_response.json()).lower()
    assert "api_key" not in serialized
    assert "prompt" not in serialized


async def test_admin_provider_usage_returns_provider_summaries(async_client):
    await _login_admin(async_client)

    response = await async_client.get("/api/admin/provider-usage")

    assert response.status_code == 200
    assert isinstance(response.json(), list)

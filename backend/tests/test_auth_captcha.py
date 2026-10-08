from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.captcha import verify_captcha_token
from app.core.config import get_settings
from app.main import app


def enable_captcha(monkeypatch):
    monkeypatch.setenv("CAPTCHA_ENABLED", "true")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "test-secret")
    get_settings.cache_clear()


@pytest.mark.anyio
async def test_send_code_requires_captcha(monkeypatch):
    enable_captcha(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/send-code",
            json={"email": f"{uuid4()}@example.com", "purpose": "register"},
        )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_captcha_config_exposes_site_key_without_secret(monkeypatch):
    enable_captcha(monkeypatch)
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "test-site-key")
    get_settings.cache_clear()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/auth/captcha")

    assert response.status_code == 200
    body = response.json()
    assert body == {"enabled": True, "site_key": "test-site-key"}
    assert "secret" not in body


@pytest.mark.anyio
async def test_send_code_rejects_failed_captcha(monkeypatch):
    enable_captcha(monkeypatch)

    async def fake_verify(*args, **kwargs):
        return False

    monkeypatch.setattr("app.auth.captcha._verify_turnstile_siteverify", fake_verify)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/send-code",
            json={
                "email": f"{uuid4()}@example.com",
                "purpose": "register",
                "captcha_token": "bad-token",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "captcha_invalid"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/auth/register",
            {"email": f"{uuid4()}@example.com", "code": "123456", "password": "Password123!"},
        ),
        (
            "/api/auth/login/password",
            {"email": f"{uuid4()}@example.com", "password": "Password123!"},
        ),
        (
            "/api/auth/login/code",
            {"email": f"{uuid4()}@example.com", "code": "123456"},
        ),
        (
            "/api/auth/reset-password",
            {"email": f"{uuid4()}@example.com", "code": "123456", "password": "Password123!"},
        ),
    ],
)
async def test_auth_endpoints_require_captcha_before_auth_work(monkeypatch, path, payload):
    enable_captcha(monkeypatch)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path, json=payload)

    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, request=httpx.Request("POST", "https://turnstile.test/siteverify")),
        httpx.Response(
            200,
            content=b"not json",
            request=httpx.Request("POST", "https://turnstile.test/siteverify"),
        ),
    ],
)
async def test_verify_captcha_token_fails_closed_on_bad_siteverify_response(
    monkeypatch,
    response,
):
    enable_captcha(monkeypatch)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return response

    monkeypatch.setattr("app.auth.captcha.httpx.AsyncClient", FakeClient)

    with pytest.raises(Exception) as exc_info:
        await verify_captcha_token("token")

    assert getattr(exc_info.value, "status_code", None) == 400
    assert getattr(exc_info.value, "detail", None) == "captcha_invalid"


@pytest.mark.anyio
async def test_verify_captcha_token_fails_closed_on_siteverify_network_error(monkeypatch):
    enable_captcha(monkeypatch)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            request = httpx.Request("POST", "https://turnstile.test/siteverify")
            raise httpx.ConnectError("unreachable", request=request)

    monkeypatch.setattr("app.auth.captcha.httpx.AsyncClient", FakeClient)

    with pytest.raises(Exception) as exc_info:
        await verify_captcha_token("token")

    assert getattr(exc_info.value, "status_code", None) == 400
    assert getattr(exc_info.value, "detail", None) == "captcha_invalid"

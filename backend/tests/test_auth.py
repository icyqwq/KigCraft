import json
import sys
import types
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.auth.email_delivery import EmailDeliveryError, TencentSesEmailDeliveryProvider, VerificationEmail
from app.auth.email_codes import send_verification_code
from app.auth.models import VerificationPurpose
from app.auth.passwords import verify_password
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuthLoginFailure, AuthSession, EmailVerificationCode, User, UserRole
from app.db.session import get_sessionmaker


CURRENT_TERMS_VERSION = "2026-07-11"


async def register_current_terms_user(async_client, *, email: str | None = None) -> str:
    email = email or f"terms-{uuid4()}@example.com"
    send_response = await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )
    assert send_response.status_code == 200
    register_response = await async_client.post(
        "/api/auth/register",
        json={
            "email": email,
            "code": "123456",
            "password": "CorrectHorseBattery7!",
            "terms_accepted": True,
            "terms_version": CURRENT_TERMS_VERSION,
        },
    )
    assert register_response.status_code == 200
    return email


async def test_tencent_ses_template_data_uses_json_encoded_variables(monkeypatch):
    captured = {}

    class FakeCredential:
        def __init__(self, secret_id, secret_key):
            captured["secret_id_set"] = bool(secret_id)
            captured["secret_key_set"] = bool(secret_key)

    class FakeSesClient:
        def __init__(self, _credential, region):
            captured["region"] = region

        def SendEmail(self, request):
            captured["request"] = request

    class FakeSendEmailRequest:
        pass

    class FakeTemplate:
        pass

    class FakeSimple:
        pass

    tencentcloud_module = types.ModuleType("tencentcloud")
    common_module = types.ModuleType("common")
    monkeypatch.setitem(sys.modules, "tencentcloud", tencentcloud_module)
    monkeypatch.setitem(sys.modules, "tencentcloud.common", common_module)
    credential_module = types.ModuleType("credential")
    credential_module.Credential = FakeCredential
    common_module.credential = credential_module
    monkeypatch.setitem(sys.modules, "tencentcloud.common.credential", credential_module)
    exception_module = types.ModuleType("exception")
    exception_module.TencentCloudSDKException = RuntimeError
    monkeypatch.setitem(
        sys.modules,
        "tencentcloud.common.exception.tencent_cloud_sdk_exception",
        exception_module,
    )
    ses_client_module = types.ModuleType("ses_client")
    ses_client_module.SesClient = FakeSesClient
    models_module = types.ModuleType("models")
    models_module.SendEmailRequest = FakeSendEmailRequest
    models_module.Template = FakeTemplate
    models_module.Simple = FakeSimple
    ses_module = types.ModuleType("ses")
    ses_version_module = types.ModuleType("v20201002")
    ses_version_module.ses_client = ses_client_module
    ses_version_module.models = models_module
    monkeypatch.setitem(sys.modules, "tencentcloud.ses", ses_module)
    monkeypatch.setitem(sys.modules, "tencentcloud.ses.v20201002", ses_version_module)
    monkeypatch.setitem(sys.modules, "tencentcloud.ses.v20201002.ses_client", ses_client_module)
    monkeypatch.setitem(sys.modules, "tencentcloud.ses.v20201002.models", models_module)

    settings = get_settings()
    settings.tencent_cloud_secret_id = "secret-id"
    settings.tencent_cloud_secret_key = "secret-key"
    settings.tencent_ses_region = "ap-guangzhou"
    settings.tencent_ses_from_email = "KigCraft <noreply@kigcraft.com>"
    settings.tencent_ses_template_id = "12345"

    provider = TencentSesEmailDeliveryProvider(settings)

    await provider.send_verification(
        VerificationEmail(
            email="target@example.com",
            code='12"34',
            purpose="register",
        )
    )

    request = captured["request"]
    assert request.FromEmailAddress == "KigCraft <noreply@kigcraft.com>"
    assert request.Destination == ["target@example.com"]
    assert request.Template.TemplateID == 12345
    assert json.loads(request.Template.TemplateData) == {
        "code": '12"34',
        "purpose": "register",
        "logo_url": "https://kigcraft.com/email-logo.png",
        "logo": "https://kigcraft.com/email-logo.png",
    }


async def test_auth_schema_and_settings_are_available(test_app):
    settings = get_settings()
    email = f"schema-{uuid4()}@example.com"
    session_token_hash = f"session-{uuid4()}"

    assert settings.email_provider == "console"
    assert settings.database_url
    assert settings.auth_session_ttl_hours > 0
    assert settings.email_code_ttl_minutes == 10
    assert settings.email_code_max_attempts == 5
    assert settings.session_cookie_name == "kig_session"

    async with get_sessionmaker()() as session:
        user = User(email=email, role=UserRole.user)
        session.add(user)
        await session.flush()
        session.add(
            EmailVerificationCode(
                email=email,
                purpose=VerificationPurpose.register,
                code_hash="hash",
                expires_at=utc_now(),
                send_ip="127.0.0.1",
            )
        )
        session.add(
            AuthSession(
                user_id=user.id,
                session_token_hash=session_token_hash,
                expires_at=utc_now(),
                created_ip="127.0.0.1",
                user_agent="pytest",
            )
        )
        await session.commit()

    async with get_sessionmaker()() as session:
        result = await session.execute(select(User).where(User.email == email))
        assert result.scalar_one().role == UserRole.user


async def test_register_requires_current_terms_acceptance(async_client):
    response = await async_client.post(
        "/api/auth/register",
        json={
            "email": f"missing-terms-{uuid4()}@example.com",
            "code": "123456",
            "password": "CorrectHorseBattery7!",
        },
    )

    assert response.status_code == 422


async def test_current_terms_version_is_published_for_registration(async_client):
    response = await async_client.get("/api/auth/terms-version")

    assert response.status_code == 200
    assert response.json() == {"version": get_settings().current_terms_version}


async def test_send_code_rejects_registration_for_existing_account(async_client):
    email = await register_current_terms_user(async_client)

    async with get_sessionmaker()() as session:
        before_rows = (
            await session.execute(
                select(EmailVerificationCode).where(EmailVerificationCode.email == email)
            )
        ).scalars().all()

    response = await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "email_already_registered"

    async with get_sessionmaker()() as session:
        after_rows = (
            await session.execute(
                select(EmailVerificationCode).where(EmailVerificationCode.email == email)
            )
        ).scalars().all()
    assert len(after_rows) == len(before_rows)


async def test_existing_account_error_precedes_send_cooldown(async_client, monkeypatch):
    monkeypatch.setenv("EMAIL_CODE_SEND_COOLDOWN_SECONDS", "60")
    get_settings.cache_clear()
    email = await register_current_terms_user(async_client)

    response = await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "email_already_registered"
    get_settings.cache_clear()


@pytest.mark.parametrize("purpose", ["login", "reset_password"])
async def test_send_code_rejects_login_and_reset_for_missing_account(async_client, purpose):
    email = f"missing-{uuid4()}@example.com"
    response = await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": purpose},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "account_not_found"

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(EmailVerificationCode).where(EmailVerificationCode.email == email)
            )
        ).scalars().all()
    assert rows == []


async def test_password_login_rejects_missing_account_with_account_not_found(async_client):
    response = await async_client.post(
        "/api/auth/login/password",
        json={"email": f"missing-password-{uuid4()}@example.com", "password": "NoSuchPass7!"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "account_not_found"


async def test_code_login_rejects_missing_account_after_valid_code(async_client):
    email = f"missing-code-login-{uuid4()}@example.com"
    async with get_sessionmaker()() as session:
        await send_verification_code(session, email, VerificationPurpose.login, "127.0.0.1")

    response = await async_client.post(
        "/api/auth/login/code",
        json={"email": email, "code": "123456"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "account_not_found"


async def test_reset_password_rejects_missing_account_after_valid_code(async_client):
    email = f"missing-reset-{uuid4()}@example.com"
    async with get_sessionmaker()() as session:
        await send_verification_code(session, email, VerificationPurpose.reset_password, "127.0.0.1")

    response = await async_client.post(
        "/api/auth/reset-password",
        json={
            "email": email,
            "code": "123456",
            "password": "NewMissingPass7!",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "account_not_found"


async def test_register_rejects_stale_terms_version(async_client):
    email = f"stale-terms-{uuid4()}@example.com"
    assert (
        await async_client.post(
            "/api/auth/send-code",
            json={"email": email, "purpose": "register"},
        )
    ).status_code == 200

    response = await async_client.post(
        "/api/auth/register",
        json={
            "email": email,
            "code": "123456",
            "password": "CorrectHorseBattery7!",
            "terms_accepted": True,
            "terms_version": "2026-07-10",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "terms_version_outdated"


async def test_authenticated_user_can_update_privacy_quality_consent(async_client):
    email = await register_current_terms_user(async_client)

    response = await async_client.patch(
        "/api/auth/privacy-preferences",
        json={"generation_quality_improvement_consent": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == email
    assert body["terms_version"] == CURRENT_TERMS_VERSION
    assert body["terms_accepted_at"]
    assert body["generation_quality_improvement_consent"] is True
    assert body["generation_quality_improvement_consent_updated_at"]


async def test_privacy_preferences_requires_an_authenticated_user(async_client):
    response = await async_client.patch(
        "/api/auth/privacy-preferences",
        json={"generation_quality_improvement_consent": True},
    )

    assert response.status_code == 401


async def test_privacy_preferences_rejects_extra_fields(async_client):
    await register_current_terms_user(async_client)

    response = await async_client.patch(
        "/api/auth/privacy-preferences",
        json={
            "generation_quality_improvement_consent": True,
            "unexpected": True,
        },
    )

    assert response.status_code == 422


async def test_register_login_logout_and_current_user(async_client):
    email = f"person-{uuid4()}@example.com"
    send_response = await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )
    assert send_response.status_code == 200

    register_response = await async_client.post(
        "/api/auth/register",
        json={
            "email": email,
            "code": "123456",
            "password": "CorrectHorseBattery7!",
            "terms_accepted": True,
            "terms_version": CURRENT_TERMS_VERSION,
        },
    )
    assert register_response.status_code == 200
    assert register_response.json()["email"] == email
    assert register_response.json()["role"] == "user"

    me_response = await async_client.get("/api/auth/me")
    assert me_response.status_code == 200
    me_body = me_response.json()
    assert me_body["email"] == email
    assert me_body["daily_generation_remaining"] == 10
    assert me_body["daily_generation_reset_at"]

    logout_response = await async_client.post("/api/auth/logout")
    assert logout_response.status_code == 200

    assert (await async_client.get("/api/auth/me")).status_code == 401

    login_response = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": "CorrectHorseBattery7!"},
    )
    assert login_response.status_code == 200
    assert (await async_client.get("/api/auth/me")).json()["email"] == email


async def test_code_login_and_password_reset(async_client):
    email = f"code-{uuid4()}@example.com"
    await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )
    assert (
        await async_client.post(
            "/api/auth/register",
            json={
                "email": email,
                "code": "123456",
                "password": "InitialPass7!",
                "terms_accepted": True,
                "terms_version": CURRENT_TERMS_VERSION,
            },
        )
    ).status_code == 200
    await async_client.post("/api/auth/logout")

    await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "login"},
    )
    code_login = await async_client.post(
        "/api/auth/login/code",
        json={"email": email, "code": "123456"},
    )
    assert code_login.status_code == 200
    await async_client.post("/api/auth/logout")

    await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "reset_password"},
    )
    reset = await async_client.post(
        "/api/auth/reset-password",
        json={
            "email": email,
            "code": "123456",
            "password": "NewPassphrase7!",
        },
    )
    assert reset.status_code == 200

    old_login = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": "InitialPass7!"},
    )
    assert old_login.status_code == 401

    new_login = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": "NewPassphrase7!"},
    )
    assert new_login.status_code == 200


async def test_verification_code_storage_and_send_failure_cleanup(async_client, monkeypatch):
    settings = get_settings()
    stored_email = f"stored-{uuid4()}@example.com"
    failure_email = f"failure-{uuid4()}@example.com"
    rate_email = f"rate-{uuid4()}@example.com"

    send_response = await async_client.post(
        "/api/auth/send-code",
        json={"email": stored_email, "purpose": "register"},
    )
    assert send_response.status_code == 200

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(EmailVerificationCode).where(
                    EmailVerificationCode.email == stored_email
                )
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].code_hash != "123456"
    assert "123456" not in rows[0].code_hash

    class FailingProvider:
        async def send_verification(self, message) -> None:
            raise EmailDeliveryError("simulated_delivery_failure")

    from app.auth import email_codes as email_codes_module

    original_email_provider_factory = email_codes_module.get_email_delivery_provider
    monkeypatch.setattr(
        "app.auth.email_codes.get_email_delivery_provider",
        lambda _settings: FailingProvider(),
    )

    async with get_sessionmaker()() as session:
        with pytest.raises(EmailDeliveryError, match="simulated_delivery_failure"):
            await send_verification_code(
                session,
                failure_email,
                VerificationPurpose.register,
                "127.0.0.1",
            )

    async with get_sessionmaker()() as session:
        failure_rows = (
            await session.execute(
                select(EmailVerificationCode).where(
                    EmailVerificationCode.email == failure_email
                )
            )
        ).scalars().all()

    assert failure_rows == []

    monkeypatch.setattr(
        "app.auth.email_codes.get_email_delivery_provider",
        original_email_provider_factory,
    )

    for _ in range(settings.email_code_send_max_per_email):
        response = await async_client.post(
            "/api/auth/send-code",
            json={"email": rate_email, "purpose": "register"},
        )
        assert response.status_code == 200

    rate_limited = await async_client.post(
        "/api/auth/send-code",
        json={"email": rate_email, "purpose": "register"},
    )
    assert rate_limited.status_code == 429
    assert rate_limited.json()["detail"] == "verification_code_rate_limited"
    assert int(rate_limited.headers["retry-after"]) >= 1


async def test_verification_code_send_enforces_cooldown(async_client, monkeypatch):
    monkeypatch.setenv("EMAIL_CODE_SEND_COOLDOWN_SECONDS", "60")
    get_settings.cache_clear()
    settings = get_settings()
    email = f"cooldown-{uuid4()}@example.com"

    first = await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )
    assert first.status_code == 200

    second = await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )
    assert second.status_code == 429
    assert second.json()["detail"] == "verification_code_rate_limited"
    assert int(second.headers["retry-after"]) <= settings.email_code_send_cooldown_seconds


async def test_verification_code_send_enforces_ip_limit(async_client, monkeypatch):
    monkeypatch.setenv("EMAIL_CODE_SEND_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("EMAIL_CODE_SEND_MAX_PER_EMAIL", "10")
    monkeypatch.setenv("EMAIL_CODE_SEND_MAX_PER_IP", "2")
    get_settings.cache_clear()
    headers = {"x-forwarded-for": "203.0.113.20"}

    for _ in range(2):
        response = await async_client.post(
            "/api/auth/send-code",
            headers=headers,
            json={"email": f"ip-send-{uuid4()}@example.com", "purpose": "register"},
        )
        assert response.status_code == 200

    limited = await async_client.post(
        "/api/auth/send-code",
        headers=headers,
        json={"email": f"ip-send-{uuid4()}@example.com", "purpose": "register"},
    )
    assert limited.status_code == 429
    assert limited.json()["detail"] == "verification_code_rate_limited"
    assert int(limited.headers["retry-after"]) >= 1


@pytest.mark.parametrize("registered", [False, True], ids=["missing-account", "wrong-password"])
async def test_password_login_rate_limited_after_failures(async_client, monkeypatch, registered):
    monkeypatch.setenv("AUTH_LOGIN_FAILURE_MAX_PER_EMAIL", "2")
    monkeypatch.setenv("AUTH_LOGIN_FAILURE_MAX_PER_IP", "20")
    monkeypatch.setenv("AUTH_LOGIN_FAILURE_WINDOW_SECONDS", "300")
    get_settings.cache_clear()
    email = f"login-limit-{uuid4()}@example.com"
    if registered:
        await register_current_terms_user(async_client, email=email)
        await async_client.post("/api/auth/logout")

    for _ in range(2):
        response = await async_client.post(
            "/api/auth/login/password",
            json={"email": email, "password": "wrong", "captcha_token": "test-token"},
        )
        assert response.status_code == (401 if registered else 404)
        assert response.json()["detail"] == (
            "invalid_email_or_password" if registered else "account_not_found"
        )

    limited = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": "wrong", "captcha_token": "test-token"},
    )
    assert limited.status_code == 429
    assert limited.json()["detail"] == "login_rate_limited"
    assert int(limited.headers["retry-after"]) >= 1

    async with get_sessionmaker()() as session:
        failures = (
            await session.execute(select(AuthLoginFailure).where(AuthLoginFailure.email == email))
        ).scalars().all()
    assert len(failures) == 2


async def test_password_login_ip_limit_survives_unrelated_success(async_client, monkeypatch):
    monkeypatch.setenv("AUTH_LOGIN_FAILURE_MAX_PER_EMAIL", "20")
    monkeypatch.setenv("AUTH_LOGIN_FAILURE_MAX_PER_IP", "2")
    monkeypatch.setenv("AUTH_LOGIN_FAILURE_WINDOW_SECONDS", "300")
    get_settings.cache_clear()
    headers = {"x-forwarded-for": "203.0.113.30"}
    first_bad_email = f"ip-login-a-{uuid4()}@example.com"

    first_failure = await async_client.post(
        "/api/auth/login/password",
        headers=headers,
        json={"email": first_bad_email, "password": "wrong", "captcha_token": "test-token"},
    )
    assert first_failure.status_code == 404
    assert first_failure.json()["detail"] == "account_not_found"

    good_email = f"ip-login-good-{uuid4()}@example.com"
    password = "CorrectHorseBattery7!"
    assert (
        await async_client.post(
            "/api/auth/send-code",
            json={"email": good_email, "purpose": "register"},
        )
    ).status_code == 200
    assert (
        await async_client.post(
            "/api/auth/register",
            json={
                "email": good_email,
                "code": "123456",
                "password": password,
                "terms_accepted": True,
                "terms_version": CURRENT_TERMS_VERSION,
            },
        )
    ).status_code == 200
    await async_client.post("/api/auth/logout")

    successful_login = await async_client.post(
        "/api/auth/login/password",
        headers=headers,
        json={"email": good_email, "password": password, "captcha_token": "test-token"},
    )
    assert successful_login.status_code == 200

    async with get_sessionmaker()() as session:
        first_failure_count = await session.scalar(
            select(AuthLoginFailure).where(AuthLoginFailure.email == first_bad_email)
        )
    assert first_failure_count is not None

    second_failure = await async_client.post(
        "/api/auth/login/password",
        headers=headers,
        json={
            "email": f"ip-login-b-{uuid4()}@example.com",
            "password": "wrong",
            "captcha_token": "test-token",
        },
    )
    assert second_failure.status_code == 404
    assert second_failure.json()["detail"] == "account_not_found"

    limited = await async_client.post(
        "/api/auth/login/password",
        headers=headers,
        json={
            "email": f"ip-login-c-{uuid4()}@example.com",
            "password": "wrong",
            "captcha_token": "test-token",
        },
    )
    assert limited.status_code == 429
    assert limited.json()["detail"] == "login_rate_limited"


async def test_password_hash_and_session_cookie_security(async_client):
    settings = get_settings()
    email = f"security-{uuid4()}@example.com"

    assert (
        await async_client.post(
            "/api/auth/send-code",
            json={"email": email, "purpose": "register"},
        )
    ).status_code == 200

    register_response = await async_client.post(
        "/api/auth/register",
        json={
            "email": email,
            "code": "123456",
            "password": "SecurityPass7!",
            "terms_accepted": True,
            "terms_version": CURRENT_TERMS_VERSION,
        },
    )
    assert register_response.status_code == 200
    assert "HttpOnly" in register_response.headers["set-cookie"]

    raw_session_token = async_client.cookies.get(settings.session_cookie_name)
    assert raw_session_token

    async with get_sessionmaker()() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        auth_session = (
            await session.execute(select(AuthSession).where(AuthSession.user_id == user.id))
        ).scalar_one()

    assert user.password_hash != "SecurityPass7!"
    assert verify_password("SecurityPass7!", user.password_hash)
    assert auth_session.session_token_hash != raw_session_token
    assert raw_session_token not in auth_session.session_token_hash


async def test_reset_password_revokes_active_session(async_client):
    settings = get_settings()
    email = f"reset-{uuid4()}@example.com"

    assert (
        await async_client.post(
            "/api/auth/send-code",
            json={"email": email, "purpose": "register"},
        )
    ).status_code == 200
    assert (
        await async_client.post(
            "/api/auth/register",
            json={
                "email": email,
                "code": "123456",
                "password": "InitialReset7!",
                "terms_accepted": True,
                "terms_version": CURRENT_TERMS_VERSION,
            },
        )
    ).status_code == 200

    active_cookie = async_client.cookies.get(settings.session_cookie_name)
    assert active_cookie

    assert (
        await async_client.post(
            "/api/auth/send-code",
            json={"email": email, "purpose": "reset_password"},
        )
    ).status_code == 200
    reset_response = await async_client.post(
        "/api/auth/reset-password",
        json={
            "email": email,
            "code": "123456",
            "password": "UpdatedReset7!",
        },
    )
    assert reset_response.status_code == 200

    stale_client = async_client.__class__(
        transport=async_client._transport,
        base_url=str(async_client.base_url),
        cookies={settings.session_cookie_name: active_cookie},
    )
    async with stale_client:
        stale_me = await stale_client.get("/api/auth/me")
    assert stale_me.status_code == 401

    async with get_sessionmaker()() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        sessions = (
            await session.execute(select(AuthSession).where(AuthSession.user_id == user.id))
        ).scalars().all()

    assert sessions
    assert all(session_row.revoked_at is not None for session_row in sessions)


async def test_banned_user_cannot_login_or_use_session(async_client):
    settings = get_settings()
    email = f"banned-{uuid4()}@example.com"

    assert (
        await async_client.post(
            "/api/auth/send-code",
            json={"email": email, "purpose": "register"},
        )
    ).status_code == 200
    assert (
        await async_client.post(
            "/api/auth/register",
            json={
                "email": email,
                "code": "123456",
                "password": "BanTarget7!",
                "terms_accepted": True,
                "terms_version": CURRENT_TERMS_VERSION,
            },
        )
    ).status_code == 200

    session_cookie = async_client.cookies.get(settings.session_cookie_name)
    assert session_cookie

    async with get_sessionmaker()() as session:
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one()
        user.is_banned = True
        await session.commit()

    banned_me = await async_client.get("/api/auth/me")
    assert banned_me.status_code == 403

    stale_client = async_client.__class__(
        transport=async_client._transport,
        base_url=str(async_client.base_url),
        cookies={settings.session_cookie_name: session_cookie},
    )
    async with stale_client:
        stale_me = await stale_client.get("/api/auth/me")
    assert stale_me.status_code == 403

    await async_client.post("/api/auth/logout")

    banned_login = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": "BanTarget7!"},
    )
    assert banned_login.status_code == 403


async def test_change_password_updates_hash_keeps_current_revokes_others(async_client):
    settings = get_settings()
    email = f"change-pw-{uuid4()}@example.com"
    old_password = "OldPassword7!"
    new_password = "NewPassword7!"

    assert (
        await async_client.post(
            "/api/auth/send-code",
            json={"email": email, "purpose": "register"},
        )
    ).status_code == 200
    assert (
        await async_client.post(
            "/api/auth/register",
            json={
                "email": email,
                "code": "123456",
                "password": old_password,
                "terms_accepted": True,
                "terms_version": CURRENT_TERMS_VERSION,
            },
        )
    ).status_code == 200

    current_cookie = async_client.cookies.get(settings.session_cookie_name)
    assert current_cookie

    other_client = async_client.__class__(
        transport=async_client._transport,
        base_url=str(async_client.base_url),
    )
    async with other_client:
        login_other = await other_client.post(
            "/api/auth/login/password",
            json={"email": email, "password": old_password},
        )
        assert login_other.status_code == 200
        other_cookie = other_client.cookies.get(settings.session_cookie_name)
        assert other_cookie
        assert other_cookie != current_cookie

    change_response = await async_client.post(
        "/api/auth/change-password",
        json={"current_password": old_password, "new_password": new_password},
    )
    assert change_response.status_code == 200
    assert change_response.json() == {"ok": True}

    me_response = await async_client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email

    stale_client = async_client.__class__(
        transport=async_client._transport,
        base_url=str(async_client.base_url),
        cookies={settings.session_cookie_name: other_cookie},
    )
    async with stale_client:
        stale_me = await stale_client.get("/api/auth/me")
    assert stale_me.status_code == 401

    await async_client.post("/api/auth/logout")
    old_login = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": old_password},
    )
    assert old_login.status_code == 401

    new_login = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": new_password},
    )
    assert new_login.status_code == 200


async def test_change_password_rejects_wrong_current_password(async_client):
    email = f"change-pw-wrong-{uuid4()}@example.com"
    await register_current_terms_user(async_client, email=email)

    response = await async_client.post(
        "/api/auth/change-password",
        json={"current_password": "WrongPassword7!", "new_password": "NewPassword7!"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_current_password"


async def test_change_password_requires_auth(async_client):
    response = await async_client.post(
        "/api/auth/change-password",
        json={"current_password": "Anything7!", "new_password": "NewPassword7!"},
    )
    assert response.status_code == 401


async def test_change_password_rejects_unchanged_password(async_client):
    email = f"change-pw-same-{uuid4()}@example.com"
    password = "SamePassword7!"

    assert (
        await async_client.post(
            "/api/auth/send-code",
            json={"email": email, "purpose": "register"},
        )
    ).status_code == 200
    assert (
        await async_client.post(
            "/api/auth/register",
            json={
                "email": email,
                "code": "123456",
                "password": password,
                "terms_accepted": True,
                "terms_version": CURRENT_TERMS_VERSION,
            },
        )
    ).status_code == 200

    response = await async_client.post(
        "/api/auth/change-password",
        json={"current_password": password, "new_password": password},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "password_unchanged"

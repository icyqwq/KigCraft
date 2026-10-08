import sys
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth import admin_cli
from app.auth.admin_cli import create_or_update_admin, promote_admin, revoke_sessions
from app.auth.models import UserRole
from app.auth.passwords import verify_password
from app.core.time import utc_now
from app.core.config import get_settings
from app.db.models import (
    AuditCounter,
    AuthSession,
    GenerationJob,
    Project,
    User,
    UserGenerationQuotaWindow,
)
from app.db.session import get_sessionmaker
from app.generation.job_store import job_store
from app.generation.quota import consume_generation_quota
from app.generation.schemas import CreateJobRequest


async def _register_user(async_client, email: str, password: str) -> dict:
    send_response = await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )
    assert send_response.status_code == 200

    register_response = await async_client.post(
        "/api/auth/register",
        json={"email": email, "code": "123456", "password": password, "terms_accepted": True, "terms_version": get_settings().current_terms_version},
    )
    assert register_response.status_code == 200
    assert register_response.json()["email"] == email
    assert register_response.json()["role"] == "user"
    return register_response.json()


async def login_admin(async_client, email="api-admin@example.com"):
    await create_or_update_admin(email, "AdminPassphrase7!")
    response = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": "AdminPassphrase7!"},
    )
    assert response.status_code == 200
    return response.json()


def _drain_coroutine(coro):
    try:
        coro.send(None)
    except StopIteration as exc:
        return exc.value
    raise AssertionError("Coroutine unexpectedly yielded control")


async def test_admin_cli_creates_admin_user(test_app):
    email = f"admin-{uuid4()}@example.com"

    await create_or_update_admin(email, "AdminPassphrase7!")

    async with get_sessionmaker()() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()

    assert user.role == UserRole.admin
    assert user.email_verified_at is not None
    assert verify_password("AdminPassphrase7!", user.password_hash)


async def test_admin_cli_rejects_invalid_create_email_before_writing(test_app):
    invalid_email = f"not-an-email-{uuid4()}"
    with pytest.raises(SystemExit, match="invalid_email"):
        await create_or_update_admin(invalid_email, "AdminPassphrase7!")

    async with get_sessionmaker()() as session:
        result = await session.execute(select(User).where(User.email == invalid_email))
        assert result.scalar_one_or_none() is None


async def test_admin_dependency_rejects_non_admin(async_client):
    email = f"plain-{uuid4()}@example.com"

    await _register_user(async_client, email, "PlainPassphrase7!")

    response = await async_client.get("/api/admin/users")

    assert response.status_code == 403


async def test_admin_can_manage_users(async_client):
    managed_email = f"managed-{uuid4()}@example.com"
    managed = await _register_user(async_client, managed_email, "ManagedPassphrase7!")
    user_id = managed["id"]
    await job_store.create(
        CreateJobRequest(character_session_id="managed-session", free_text="managed job"),
        provider="fixture",
        user_id=user_id,
    )
    await async_client.post("/api/auth/logout")
    await login_admin(async_client)

    list_response = await async_client.get(f"/api/admin/users?query={managed_email}")
    assert list_response.status_code == 200
    listed_user = list_response.json()["users"][0]
    assert listed_user["email"] == managed_email
    assert listed_user["task_count"] == 1
    assert listed_user["daily_generation_limit"] == 20
    assert listed_user["daily_generation_limit_override"] is None
    assert listed_user["daily_generation_used"] == 0
    assert listed_user["daily_generation_remaining"] == 20
    assert listed_user["daily_generation_reset_at"]

    detail_response = await async_client.get(f"/api/admin/users/{user_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == user_id

    ban_response = await async_client.patch(
        f"/api/admin/users/{user_id}/ban",
        json={"is_banned": True},
    )
    assert ban_response.status_code == 200
    assert ban_response.json()["is_banned"] is True

    role_response = await async_client.patch(
        f"/api/admin/users/{user_id}/role",
        json={"role": "admin"},
    )
    assert role_response.status_code == 200
    assert role_response.json()["role"] == "admin"


async def test_admin_can_revoke_user_sessions(async_client):
    managed_email = f"managed-revoke-{uuid4()}@example.com"

    async with get_sessionmaker()() as session:
        user = User(email=managed_email, role=UserRole.user)
        session.add(user)
        await session.flush()
        session.add(
            AuthSession(
                user_id=user.id,
                session_token_hash=f"active-{uuid4()}",
                expires_at=utc_now() + timedelta(hours=4),
                created_ip="127.0.0.1",
                user_agent="pytest-admin-endpoint",
            )
        )
        await session.commit()
        user_id = user.id

    await login_admin(async_client, f"revoke-admin-{uuid4()}@example.com")

    response = await async_client.post(f"/api/admin/users/{user_id}/revoke-sessions")

    assert response.status_code == 200
    assert response.json()["revoked_sessions"] == 1

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(select(AuthSession).where(AuthSession.user_id == user_id))
        ).scalars().all()
    assert rows[0].revoked_at is not None


async def test_admin_cannot_lock_out_own_account(async_client):
    admin = await login_admin(async_client, f"self-lockout-admin-{uuid4()}@example.com")
    admin_user_id = admin["id"]

    role_response = await async_client.patch(
        f"/api/admin/users/{admin_user_id}/role",
        json={"role": "user"},
    )
    assert role_response.status_code == 409
    assert role_response.json()["detail"] == "admin_self_role_change_forbidden"

    ban_response = await async_client.patch(
        f"/api/admin/users/{admin_user_id}/ban",
        json={"is_banned": True},
    )
    assert ban_response.status_code == 409
    assert ban_response.json()["detail"] == "admin_self_ban_forbidden"

    revoke_response = await async_client.post(
        f"/api/admin/users/{admin_user_id}/revoke-sessions"
    )
    assert revoke_response.status_code == 409
    assert revoke_response.json()["detail"] == "admin_self_session_revoke_forbidden"

    me_response = await async_client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert {
        "id": admin_user_id,
        "email": admin["email"],
        "role": "admin",
        "is_banned": False,
    }.items() <= me_response.json().items()

    async with get_sessionmaker()() as session:
        current_admin = await session.get(User, admin_user_id)
        auth_sessions = (
            await session.execute(select(AuthSession).where(AuthSession.user_id == admin_user_id))
        ).scalars().all()

    assert current_admin is not None
    assert current_admin.role == UserRole.admin
    assert current_admin.is_banned is False
    assert auth_sessions
    assert all(session_row.revoked_at is None for session_row in auth_sessions)


async def test_admin_role_update_rejects_invalid_role_payload(async_client):
    managed = await _register_user(
        async_client,
        f"invalid-role-{uuid4()}@example.com",
        "ManagedPassphrase7!",
    )
    await async_client.post("/api/auth/logout")
    await login_admin(async_client, f"invalid-role-admin-{uuid4()}@example.com")

    response = await async_client.patch(
        f"/api/admin/users/{managed['id']}/role",
        json={"role": "owner"},
    )

    assert response.status_code == 422
    assert any(error["loc"][-1] == "role" for error in response.json()["detail"])


async def test_cors_preflight_allows_admin_patch_methods(monkeypatch: pytest.MonkeyPatch):
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.com")
    get_settings.cache_clear()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/api/admin/users/user-id/role",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "PATCH",
            },
        )

    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]


async def test_admin_audit_summary_includes_legacy_and_current_counts(async_client):
    managed = await _register_user(
        async_client,
        f"audit-managed-{uuid4()}@example.com",
        "ManagedPassphrase7!",
    )
    await job_store.create(
        CreateJobRequest(character_session_id="audit-managed-session", free_text="summary"),
        provider="fixture",
        user_id=managed["id"],
    )
    await async_client.post("/api/auth/logout")

    async with get_sessionmaker()() as session:
        for key, value in {"total_calls": 7, "succeeded": 5}.items():
            counter = await session.get(AuditCounter, key)
            if counter is None:
                session.add(AuditCounter(key=key, value=value))
            else:
                counter.value = value
        await session.commit()

    await login_admin(async_client, "audit-admin@example.com")

    response = await async_client.get("/api/admin/audit/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["total_users"] >= 2
    assert body["total_jobs"] == 1
    assert body["total_calls"] == 8
    assert body["legacy_counters"]["total_calls"] == 7
    assert body["legacy_counters"]["succeeded"] == 5
    assert body["job_counts"]["queued"] == 1
    assert body["quota_policy"] == {
        "window_hours": 5,
        "window_limit": 8,
        "parallel_generation_limit": 4,
        "codex_usage_min_remaining_percent": 25,
        "daily_generation_limit": 20,
        "daily_generation_custom_limit_default": 20,
        "daily_generation_reset_hour": 6,
    }


async def test_admin_can_manage_user_daily_generation_quota(async_client):
    managed = await _register_user(
        async_client,
        f"quota-managed-{uuid4()}@example.com",
        "ManagedPassphrase7!",
    )
    await async_client.post("/api/auth/logout")
    async with get_sessionmaker()() as session:
        managed_user = await session.get(User, managed["id"])
        assert managed_user is not None
        consumed_state = await consume_generation_quota(session, managed_user)
        await session.commit()
    assert consumed_state.remaining == 9

    await login_admin(async_client, f"quota-admin-{uuid4()}@example.com")

    set_response = await async_client.patch(
        f"/api/admin/users/{managed['id']}/quota/daily-generation-limit",
        json={"limit": 100},
    )
    assert set_response.status_code == 200
    assert set_response.json()["daily_generation_limit_override"] == 100
    assert set_response.json()["daily_generation_limit"] == 100
    assert set_response.json()["daily_generation_remaining"] == 100

    clear_response = await async_client.patch(
        f"/api/admin/users/{managed['id']}/quota/daily-generation-limit",
        json={"limit": None},
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["daily_generation_limit_override"] is None
    assert clear_response.json()["daily_generation_limit"] == 20

    add_response = await async_client.post(
        f"/api/admin/users/{managed['id']}/quota/daily-generation/adjust",
        json={"delta": 15},
    )
    assert add_response.status_code == 200
    assert add_response.json()["daily_generation_balance"] == 115
    assert add_response.json()["daily_generation_remaining"] == 115

    subtract_response = await async_client.post(
        f"/api/admin/users/{managed['id']}/quota/daily-generation/adjust",
        json={"delta": -200},
    )
    assert subtract_response.status_code == 200
    assert subtract_response.json()["daily_generation_balance"] == 0
    assert subtract_response.json()["daily_generation_remaining"] == 0


async def test_admin_users_list_is_paginated_and_sortable(async_client):
    search_prefix = f"sortable-{uuid4()}-"
    async with get_sessionmaker()() as session:
        low_user = User(
            email=f"{search_prefix}low@example.com",
            email_verified_at=utc_now(),
            role=UserRole.user,
            daily_generation_balance=5,
        )
        high_user = User(
            email=f"{search_prefix}high@example.com",
            email_verified_at=utc_now(),
            role=UserRole.user,
            daily_generation_balance=40,
        )
        session.add_all([low_user, high_user])
        await session.flush()
        low_project = Project(id=str(uuid4()), user_id=low_user.id, name="Low")
        high_project_a = Project(id=str(uuid4()), user_id=high_user.id, name="High A")
        high_project_b = Project(id=str(uuid4()), user_id=high_user.id, name="High B")
        session.add_all([low_project, high_project_a, high_project_b])
        await session.flush()
        session.add_all(
            [
                GenerationJob(project_id=low_project.id, user_id=low_user.id),
                GenerationJob(project_id=high_project_a.id, user_id=high_user.id),
                GenerationJob(project_id=high_project_b.id, user_id=high_user.id),
            ]
        )
        await session.commit()

    await login_admin(async_client, f"page-admin-{uuid4()}@example.com")

    generation_response = await async_client.get(
        "/api/admin/users",
        params={
            "query": search_prefix,
            "limit": 1,
            "sort_by": "generation_count",
            "sort_dir": "desc",
        },
    )
    assert generation_response.status_code == 200
    generation_body = generation_response.json()
    assert generation_body["total"] == 2
    assert generation_body["limit"] == 1
    assert generation_body["offset"] == 0
    assert generation_body["has_more"] is True
    assert generation_body["users"][0]["email"] == high_user.email
    assert generation_body["users"][0]["generation_count"] == 2

    remaining_response = await async_client.get(
        "/api/admin/users",
        params={
            "query": search_prefix,
            "limit": 2,
            "sort_by": "remaining_quota",
            "sort_dir": "asc",
        },
    )
    assert remaining_response.status_code == 200
    remaining_users = remaining_response.json()["users"]
    assert [user["email"] for user in remaining_users] == [low_user.email, high_user.email]
    assert [user["daily_generation_remaining"] for user in remaining_users] == [20, 40]


async def test_admin_can_reset_daily_generation_quota(async_client):
    managed_a = await _register_user(
        async_client,
        f"quota-reset-a-{uuid4()}@example.com",
        "ManagedPassphrase7!",
    )
    await async_client.post("/api/auth/logout")
    managed_b = await _register_user(
        async_client,
        f"quota-reset-b-{uuid4()}@example.com",
        "ManagedPassphrase7!",
    )
    async with get_sessionmaker()() as session:
        user_a = await session.get(User, managed_a["id"])
        user_b = await session.get(User, managed_b["id"])
        assert user_a is not None
        assert user_b is not None
        await consume_generation_quota(session, user_a)
        await consume_generation_quota(session, user_b)
        await session.commit()

    await async_client.post("/api/auth/logout")
    await login_admin(async_client, f"quota-reset-admin-{uuid4()}@example.com")

    single_response = await async_client.post(
        f"/api/admin/users/{managed_a['id']}/quota/daily-generation/reset"
    )
    assert single_response.status_code == 200
    assert single_response.json()["reset_count"] == 1

    async with get_sessionmaker()() as session:
        remaining_after_single = int(
            await session.scalar(select(func.count()).select_from(UserGenerationQuotaWindow)) or 0
        )
    assert remaining_after_single == 1

    global_response = await async_client.post("/api/admin/quota/daily-generation/reset")
    assert global_response.status_code == 200
    assert global_response.json()["reset_count"] == 1

    async with get_sessionmaker()() as session:
        remaining_after_global = int(
            await session.scalar(select(func.count()).select_from(UserGenerationQuotaWindow)) or 0
        )
    assert remaining_after_global == 0


async def test_admin_audit_summary_counts_generating_jobs_as_active(async_client):
    async with get_sessionmaker()() as session:
        managed_user = User(
            email=f"generating-managed-{uuid4()}@example.com",
            email_verified_at=utc_now(),
            role=UserRole.user,
        )
        session.add(managed_user)
        await session.commit()
        managed_user_id = managed_user.id

    job = await job_store.create(
        CreateJobRequest(
            character_session_id="generating-managed-session",
            free_text="summary generating",
        ),
        provider="fixture",
        user_id=managed_user_id,
    )
    await job_store.update(
        job.id,
        status="generating",
        progress=45,
        phase_label="generating",
    )
    await async_client.post("/api/auth/logout")

    await login_admin(async_client, f"generating-admin-{uuid4()}@example.com")

    response = await async_client.get("/api/admin/audit/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["job_counts"]["generating"] == 1
    assert body["active_users"] == 1
    assert body["parallel_slots_used"] == 1


async def test_promote_admin_upgrades_existing_user(test_app):
    original_email = f"member-{uuid4()}@example.com"

    async with get_sessionmaker()() as session:
        user = User(email=original_email, role=UserRole.user)
        session.add(user)
        await session.commit()

    await promote_admin(f"  {original_email.upper()}  ")

    async with get_sessionmaker()() as session:
        result = await session.execute(select(User).where(User.email == original_email))
        promoted_user = result.scalar_one()

    assert promoted_user.role == UserRole.admin


async def test_promote_admin_rejects_invalid_email_before_changing_user(test_app):
    email = f"member-invalid-promote-{uuid4()}@example.com"

    async with get_sessionmaker()() as session:
        user = User(email=email, role=UserRole.user)
        session.add(user)
        await session.commit()

    with pytest.raises(SystemExit, match="invalid_email"):
        await promote_admin("not-an-email")

    async with get_sessionmaker()() as session:
        result = await session.execute(select(User).where(User.email == email))
        unchanged_user = result.scalar_one()

    assert unchanged_user.role == UserRole.user


async def test_revoke_sessions_marks_active_sessions_revoked(test_app):
    email = f"revoke-{uuid4()}@example.com"
    before = utc_now() - timedelta(minutes=5)

    async with get_sessionmaker()() as session:
        user = User(email=email, role=UserRole.user)
        session.add(user)
        await session.flush()

        active_session = AuthSession(
            user_id=user.id,
            session_token_hash=f"active-{uuid4()}",
            expires_at=utc_now() + timedelta(hours=4),
            created_ip="127.0.0.1",
            user_agent="pytest-active",
        )
        already_revoked = AuthSession(
            user_id=user.id,
            session_token_hash=f"revoked-{uuid4()}",
            expires_at=utc_now() + timedelta(hours=4),
            created_ip="127.0.0.1",
            user_agent="pytest-revoked",
            revoked_at=before,
        )
        session.add_all([active_session, already_revoked])
        await session.commit()

    await revoke_sessions(email)

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(AuthSession)
                .join(User, AuthSession.user_id == User.id)
                .where(User.email == email)
                .order_by(AuthSession.session_token_hash)
            )
        ).scalars().all()

    assert len(rows) == 2
    assert rows[0].revoked_at is not None
    assert rows[1].revoked_at == before


async def test_revoke_sessions_rejects_invalid_email_before_revoking(test_app):
    email = f"revoke-invalid-{uuid4()}@example.com"

    async with get_sessionmaker()() as session:
        user = User(email=email, role=UserRole.user)
        session.add(user)
        await session.flush()
        session.add(
            AuthSession(
                user_id=user.id,
                session_token_hash=f"active-invalid-{uuid4()}",
                expires_at=utc_now() + timedelta(hours=4),
                created_ip="127.0.0.1",
                user_agent="pytest-invalid-email",
            )
        )
        await session.commit()

    with pytest.raises(SystemExit, match="invalid_email"):
        await revoke_sessions("not-an-email")

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(AuthSession)
                .join(User, AuthSession.user_id == User.id)
                .where(User.email == email)
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].revoked_at is None


def test_admin_cli_main_prompts_for_password_and_runs_create(monkeypatch: pytest.MonkeyPatch):
    called: dict[str, str] = {}

    async def fake_create_or_update_admin(email: str, password: str) -> None:
        called["email"] = email
        called["password"] = password

    monkeypatch.setattr(sys, "argv", ["admin_cli", "create-admin", "--email", "root@example.com"])
    monkeypatch.setattr(admin_cli.getpass, "getpass", lambda prompt: "AdminPassphrase7!")
    monkeypatch.setattr(admin_cli, "create_or_update_admin", fake_create_or_update_admin)
    monkeypatch.setattr(admin_cli.asyncio, "run", _drain_coroutine)

    admin_cli.main()

    assert called == {
        "email": "root@example.com",
        "password": "AdminPassphrase7!",
    }


def test_admin_cli_main_rejects_short_interactive_password(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", ["admin_cli", "create-admin", "--email", "root@example.com"])
    monkeypatch.setattr(admin_cli.getpass, "getpass", lambda prompt: "short")

    with pytest.raises(SystemExit, match="password_too_short"):
        admin_cli.main()

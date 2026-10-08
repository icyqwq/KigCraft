from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.audit.router import QuotaPolicy, save_quota_policy
from app.auth.models import UserRole
from app.core.config import get_settings
from app.db.models import User, UserGenerationQuotaWindow
from app.db.session import get_sessionmaker
from app.generation.quota import (
    GenerationDailyQuotaExceeded,
    consume_generation_quota,
    current_generation_quota_window,
    get_generation_quota_state,
    reset_generation_quota,
)


async def _register_user(async_client, email: str | None = None) -> dict:
    user_email = email or f"quota-{uuid4().hex}@example.com"
    send_response = await async_client.post(
        "/api/auth/send-code",
        json={"email": user_email, "purpose": "register"},
    )
    assert send_response.status_code == 200
    register_response = await async_client.post(
        "/api/auth/register",
        json={"email": user_email, "code": "123456", "password": "UserPassphrase7!",
              "terms_accepted": True, "terms_version": get_settings().current_terms_version},
    )
    assert register_response.status_code == 200
    return register_response.json()


async def _create_user(
    *,
    role: UserRole = UserRole.user,
    limit_override: int | None = None,
    balance: int | None = None,
) -> str:
    async with get_sessionmaker()() as session:
        user = User(
            email=f"quota-{uuid4().hex}@example.com",
            role=role,
            daily_generation_limit_override=limit_override,
            daily_generation_balance=balance,
        )
        session.add(user)
        await session.commit()
        return user.id


async def _quota_row_count() -> int:
    async with get_sessionmaker()() as session:
        return int(
            await session.scalar(select(func.count()).select_from(UserGenerationQuotaWindow)) or 0
        )


async def test_daily_quota_window_starts_today_after_six(test_app):
    window_start_at, reset_at = current_generation_quota_window(
        datetime(2026, 7, 9, 1, 30, tzinfo=UTC),
        reset_hour=6,
    )

    assert window_start_at == datetime(2026, 7, 8, 22, 0, tzinfo=UTC)
    assert reset_at.isoformat() == "2026-07-10T06:00:00+08:00"


async def test_daily_quota_window_starts_yesterday_before_six(test_app):
    window_start_at, reset_at = current_generation_quota_window(
        datetime(2026, 7, 8, 21, 30, tzinfo=UTC),
        reset_hour=6,
    )

    assert window_start_at == datetime(2026, 7, 7, 22, 0, tzinfo=UTC)
    assert reset_at.isoformat() == "2026-07-09T06:00:00+08:00"


def test_default_quota_policy_values() -> None:
    policy = QuotaPolicy()

    assert policy.daily_generation_limit == 20
    assert policy.daily_generation_custom_limit_default == 20
    assert policy.parallel_generation_limit == 4


async def test_non_admin_consumes_until_daily_limit(test_app):
    save_quota_policy(QuotaPolicy(daily_generation_limit=2))
    user_id = await _create_user()
    now = datetime(2026, 7, 9, 2, 0, tzinfo=UTC)

    async with get_sessionmaker()() as session:
        user = await session.get(User, user_id)
        assert user is not None

        first = await consume_generation_quota(session, user, now)
        second = await consume_generation_quota(session, user, now)
        with pytest.raises(GenerationDailyQuotaExceeded) as exc_info:
            await consume_generation_quota(session, user, now)

    assert first.used == 1
    assert first.remaining == 1
    assert second.used == 2
    assert second.remaining == 0
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "generation_daily_quota_exceeded"
    assert exc_info.value.detail["limit"] == 2


async def test_custom_daily_limit_override_sets_effective_limit(test_app):
    save_quota_policy(QuotaPolicy(daily_generation_limit=20))
    user_id = await _create_user(limit_override=100)
    now = datetime(2026, 7, 9, 2, 0, tzinfo=UTC)

    async with get_sessionmaker()() as session:
        user = await session.get(User, user_id)
        assert user is not None

        state = await consume_generation_quota(session, user, now)

    assert state.limit == 100
    assert state.used == 1
    assert state.remaining == 99


async def test_null_daily_limit_override_uses_global_limit(test_app):
    save_quota_policy(QuotaPolicy(daily_generation_limit=7))
    user_id = await _create_user(limit_override=None)

    async with get_sessionmaker()() as session:
        user = await session.get(User, user_id)
        assert user is not None

        state = await get_generation_quota_state(session, user, datetime(2026, 7, 9, 2, 0, tzinfo=UTC))

    assert state.limit == 7
    assert state.used == 0
    assert state.remaining == 7


async def test_new_daily_window_tops_up_only_when_balance_below_limit(test_app):
    save_quota_policy(QuotaPolicy(daily_generation_limit=20))
    low_balance_user_id = await _create_user(balance=3)
    high_balance_user_id = await _create_user(balance=30)
    now = datetime(2026, 7, 9, 2, 0, tzinfo=UTC)

    async with get_sessionmaker()() as session:
        low_balance_user = await session.get(User, low_balance_user_id)
        high_balance_user = await session.get(User, high_balance_user_id)
        assert low_balance_user is not None
        assert high_balance_user is not None

        low_state = await get_generation_quota_state(session, low_balance_user, now)
        high_state = await get_generation_quota_state(session, high_balance_user, now)

    assert low_state.limit == 20
    assert low_state.remaining == 20
    assert low_state.used == 0
    assert high_state.limit == 20
    assert high_state.remaining == 30
    assert high_state.used == 0


async def test_same_daily_window_does_not_top_up_after_consumption(test_app):
    save_quota_policy(QuotaPolicy(daily_generation_limit=2))
    user_id = await _create_user()
    now = datetime(2026, 7, 9, 2, 0, tzinfo=UTC)

    async with get_sessionmaker()() as session:
        user = await session.get(User, user_id)
        assert user is not None

        await consume_generation_quota(session, user, now)
        state = await get_generation_quota_state(session, user, now)

    assert state.limit == 2
    assert state.used == 1
    assert state.remaining == 1


async def test_admin_is_exempt_and_does_not_create_quota_row(test_app):
    save_quota_policy(QuotaPolicy(daily_generation_limit=1))
    user_id = await _create_user(role=UserRole.admin)

    async with get_sessionmaker()() as session:
        user = await session.get(User, user_id)
        assert user is not None

        first = await consume_generation_quota(session, user, datetime(2026, 7, 9, 2, 0, tzinfo=UTC))
        second = await consume_generation_quota(session, user, datetime(2026, 7, 9, 2, 0, tzinfo=UTC))

    assert first.exempt is True
    assert second.exempt is True
    assert await _quota_row_count() == 0


async def test_global_reset_clears_only_active_window(test_app):
    save_quota_policy(QuotaPolicy(daily_generation_limit=5))
    user_a = await _create_user()
    user_b = await _create_user()
    active_now = datetime(2026, 7, 9, 2, 0, tzinfo=UTC)
    previous_window_now = datetime(2026, 7, 8, 2, 0, tzinfo=UTC)

    async with get_sessionmaker()() as session:
        for user_id, now in [(user_a, active_now), (user_b, active_now), (user_a, previous_window_now)]:
            user = await session.get(User, user_id)
            assert user is not None
            await consume_generation_quota(session, user, now)
        result = await reset_generation_quota(session, now=active_now)
        await session.commit()

    assert result.reset_count == 2
    assert await _quota_row_count() == 1


async def test_user_reset_clears_only_target_user_active_window(test_app):
    save_quota_policy(QuotaPolicy(daily_generation_limit=5))
    user_a = await _create_user()
    user_b = await _create_user()
    now = datetime(2026, 7, 9, 2, 0, tzinfo=UTC)

    async with get_sessionmaker()() as session:
        for user_id in [user_a, user_b]:
            user = await session.get(User, user_id)
            assert user is not None
            await consume_generation_quota(session, user, now)
        result = await reset_generation_quota(session, user_id=user_a, now=now)
        await session.commit()

    assert result.reset_count == 1
    assert await _quota_row_count() == 1


async def test_generation_create_consumes_and_blocks_when_daily_limit_exhausted(async_client):
    save_quota_policy(QuotaPolicy(daily_generation_limit=1))
    await _register_user(async_client)

    first = await async_client.post(
        "/api/generation/jobs",
        json={"free_text": "first job", "reference_keys": [], "requirement_ids": []},
    )
    second = await async_client.post(
        "/api/generation/jobs",
        json={"free_text": "second job", "reference_keys": [], "requirement_ids": []},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "generation_daily_quota_exceeded"
    assert second.json()["detail"]["limit"] == 1


async def test_generation_validation_failure_does_not_consume_daily_quota(async_client):
    save_quota_policy(QuotaPolicy(daily_generation_limit=1))
    await _register_user(async_client)

    failed = await async_client.post(
        "/api/generation/jobs",
        json={
            "free_text": "missing reference",
            "reference_keys": ["uploaded:references/missing/reference.png"],
            "requirement_ids": [],
        },
    )

    assert failed.status_code == 400
    assert failed.json()["detail"] == "At least one valid reference image is required"
    assert await _quota_row_count() == 0


async def test_legacy_generation_create_consumes_daily_quota(async_client):
    save_quota_policy(QuotaPolicy(daily_generation_limit=1))
    await _register_user(async_client)

    first = await async_client.post(
        "/api/generation/projects/project-a/jobs",
        json={
            "project_id": "project-a",
            "free_text": "legacy first",
            "chip_ids": [],
            "reference_keys": [],
        },
    )
    second = await async_client.post(
        "/api/generation/projects/project-a/jobs",
        json={
            "project_id": "project-a",
            "free_text": "legacy second",
            "chip_ids": [],
            "reference_keys": [],
        },
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "generation_daily_quota_exceeded"

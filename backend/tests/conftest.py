import os

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest_asyncio.fixture
async def test_app(monkeypatch: pytest.MonkeyPatch, tmp_path) -> FastAPI:
    from datetime import UTC, datetime

    from app.generation.codex_app_server import CodexUsageSnapshot

    async def unavailable_snapshot(_settings):
        return CodexUsageSnapshot(
            checked_at=datetime.now(UTC),
            available=False,
            source="unavailable",
            error="Codex usage status is unavailable",
        )

    monkeypatch.setattr("app.generation.codex_usage.get_codex_usage_snapshot", unavailable_snapshot)
    from app.core.config import get_settings
    from app.album.router import clear_album_items
    from app.audit.router import clear_audit_state
    from app.db.session import (
        ensure_database_schema,
        get_engine,
        reset_session_factory,
    )
    from app.db.models import (
        AlbumItem,
        AuditCounter,
        AuthLoginFailure,
        EmailVerificationCode,
        UserGenerationQuotaWindow,
    )
    from app.db.session import get_sessionmaker
    from app.generation.events import clear_events
    from app.generation.router import clear_jobs_async
    from app.main import create_app
    from app.references.router import clear_uploaded_references
    from sqlalchemy import delete

    monkeypatch.setenv("GENERATION_PROVIDER", "fixture")
    monkeypatch.setenv("CODEX_SESSION_CLEANUP_ENABLED", "false")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "true")
    monkeypatch.setenv("ADMIN_AUDIT_ENABLED", "true")
    monkeypatch.setenv("REFERENCE_UPLOAD_DIR", str(tmp_path / "references"))
    monkeypatch.setenv("CODEX_WORKSPACE_DIR", str(tmp_path / "codex"))
    monkeypatch.setenv("CODEX_OUTPUT_DIR", str(tmp_path / "generated"))
    monkeypatch.setenv("MOCK_OUTPUT_DIR", str(tmp_path / "mock-outputs"))
    monkeypatch.setenv("GENERATION_AUDIT_DB_PATH", str(tmp_path / "generation_audit.sqlite3"))
    monkeypatch.setenv("PROVIDER_PRESET_PATH", str(tmp_path / "provider-preset.json"))
    monkeypatch.setenv("ADMIN_QUOTA_POLICY_PATH", str(tmp_path / "audit_quota_policy.json"))
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    monkeypatch.setenv("EMAIL_CODE_SEND_MAX_PER_IP", "100")
    monkeypatch.setenv("EMAIL_CODE_SEND_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("AUTH_LOGIN_FAILURE_MAX_PER_IP", "100")
    monkeypatch.setenv("CAPTCHA_ENABLED", "false")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("SESSION_COOKIE_NAME", "kig_session")

    # Test-only fallback for documented Windows-host pytest runs when the shell
    # does not already provide DATABASE_URL or KIG_TEST_DATABASE_URL.
    database_url = os.environ.get("DATABASE_URL") or os.environ.get("KIG_TEST_DATABASE_URL")
    if not database_url:
        database_url = "postgresql+asyncpg://kig:kig@127.0.0.1:15432/kig_preview"
    monkeypatch.setenv("DATABASE_URL", database_url)

    get_settings.cache_clear()
    reset_session_factory()
    clear_audit_state()
    clear_album_items()
    clear_events()
    clear_uploaded_references()
    await ensure_database_schema()
    async with get_sessionmaker()() as session:
        await session.execute(delete(AlbumItem))
        await session.execute(delete(AuditCounter))
        await session.execute(delete(AuthLoginFailure))
        await session.execute(delete(EmailVerificationCode))
        await session.execute(delete(UserGenerationQuotaWindow))
        await session.commit()
    await clear_jobs_async()
    app = create_app()
    yield app
    await clear_jobs_async()
    await get_engine().dispose()
    reset_session_factory()


@pytest_asyncio.fixture(autouse=True)
async def reset_verification_send_rate_limits():
    from app.core.redis_client import flush_verification_send_rate_limits

    await flush_verification_send_rate_limits()
    yield
    await flush_verification_send_rate_limits()


@pytest_asyncio.fixture
async def async_client(test_app: FastAPI):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield client

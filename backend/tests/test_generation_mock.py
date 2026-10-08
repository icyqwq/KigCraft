import os
import uuid
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.db.models import ReferenceAsset
from app.db.session import get_sessionmaker


async def register_user(client: AsyncClient) -> dict:
    email = f"generation-mock-{uuid.uuid4().hex}@example.com"
    send_response = await client.post(
        "/api/auth/send-code", json={"email": email, "purpose": "register"}
    )
    assert send_response.status_code == 200
    response = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "code": "123456",
            "password": "UserPassphrase7!",
            "terms_accepted": True,
            "terms_version": get_settings().current_terms_version,
        },
    )
    assert response.status_code == 200
    return response.json()


async def create_tracked_reference_asset(
    user_id: str,
    object_key: str,
    *,
    kind: str = "front",
    content: bytes = b"png-image",
) -> None:
    reference_path = (
        resolve_repo_path(get_settings().reference_upload_dir)
        / Path(object_key).relative_to("references")
    )
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(content)

    async with get_sessionmaker()() as session:
        existing = (
            await session.execute(
                select(ReferenceAsset).where(ReferenceAsset.object_key == object_key)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                ReferenceAsset(
                    user_id=user_id,
                    kind=kind,
                    object_key=object_key,
                    file_name=Path(object_key).name,
                    mime_type="image/png",
                )
            )
        else:
            existing.user_id = user_id
            existing.kind = kind
            existing.file_name = Path(object_key).name
            existing.mime_type = "image/png"
        await session.commit()


def configure_database(monkeypatch) -> None:
    database_url = os.environ.get("KIG_TEST_DATABASE_URL") or (
        "postgresql+asyncpg://kig:kig@127.0.0.1:15432/kig_preview"
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    monkeypatch.setenv("CAPTCHA_ENABLED", "false")
    monkeypatch.setenv("EMAIL_CODE_SEND_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("EMAIL_CODE_SEND_MAX_PER_IP", "100")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("SESSION_COOKIE_NAME", "kig_session")


async def test_fixture_generation_is_rejected_without_explicit_allowance(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.db.session import ensure_database_schema, reset_session_factory
    from app.main import create_app

    monkeypatch.setenv("GENERATION_PROVIDER", "fixture")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "false")
    configure_database(monkeypatch)
    get_settings.cache_clear()
    reset_session_factory()
    await ensure_database_schema()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/generation/projects/project-1/jobs",
            json={
                "project_id": "project-1",
                "free_text": "make expression shy",
                "chip_ids": ["cute_round_face"],
                "reference_keys": ["references/front.png"],
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "real_generation_provider_not_configured: GENERATION_PROVIDER=fixture"


async def test_fixture_detail_analysis_is_rejected_without_explicit_allowance(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.db.session import ensure_database_schema, reset_session_factory
    from app.main import create_app

    monkeypatch.setenv("GENERATION_PROVIDER", "fixture")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "false")
    configure_database(monkeypatch)
    get_settings.cache_clear()
    reset_session_factory()
    await ensure_database_schema()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/generation/detail-analysis",
            json={
                "free_text": "keep expression",
                "reference_keys": ["front:references/upload-a/front.webp"],
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "real_generation_provider_not_configured: GENERATION_PROVIDER=fixture"


async def test_fixture_generation_is_always_rejected_in_production(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.db.session import ensure_database_schema, reset_session_factory
    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GENERATION_PROVIDER", "fixture")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "true")
    configure_database(monkeypatch)
    get_settings.cache_clear()
    reset_session_factory()
    await ensure_database_schema()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/generation/detail-analysis",
            json={
                "free_text": "keep expression",
                "reference_keys": ["front:references/upload-a/front.webp"],
            },
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "fixture_generation_disabled_in_production: GENERATION_PROVIDER=fixture"


async def test_mock_generation_returns_front_candidate_when_allowed(test_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        user = await register_user(client)
        await create_tracked_reference_asset(user["id"], "references/front.png")
        response = await client.post(
            "/generation/projects/project-1/jobs",
            json={
                "project_id": "project-1",
                "free_text": "make expression shy",
                "chip_ids": ["cute_round_face"],
                "reference_keys": ["references/front.png"],
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "succeeded"
    assert data["progress"] == 100
    assert len(data["outputs"]) == 1
    assert data["outputs"][0]["width"] == 800
    assert data["outputs"][0]["height"] == 1100


async def test_unknown_generation_job_returns_404(test_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        await register_user(client)
        response = await client.get("/generation/jobs/missing")
    assert response.status_code == 404


async def test_unknown_generation_events_return_404(test_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        await register_user(client)
        response = await client.get("/generation/jobs/missing/events")
    assert response.status_code == 404

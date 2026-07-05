from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def test_fixture_generation_is_rejected_without_explicit_allowance(monkeypatch) -> None:
    from app.core.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("GENERATION_PROVIDER", "fixture")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "false")
    get_settings.cache_clear()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    from app.main import create_app

    monkeypatch.setenv("GENERATION_PROVIDER", "fixture")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "false")
    get_settings.cache_clear()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("GENERATION_PROVIDER", "fixture")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "true")
    get_settings.cache_clear()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
        response = await client.get("/generation/jobs/missing")
    assert response.status_code == 404


async def test_unknown_generation_events_return_404(test_app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        response = await client.get("/generation/jobs/missing/events")
    assert response.status_code == 404

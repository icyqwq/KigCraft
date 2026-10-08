from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import GenerationJob, User
from app.db.session import ensure_database_schema, get_sessionmaker
from app.generation.detail_analysis import DetailAnalysisProviderRequest, DetailAnalysisProviderResult
from app.generation.job_store import StoredOutput, job_store
from app.generation.presets import ActiveProviderPresetStore, resolve_preset, snapshot_for_preset
from app.generation.provider import ImageGenerationProvider, get_provider_for_preset
from app.generation.schemas import CreateJobRequest
from app.generation.retired import RetiredProviderError


async def _repository_user() -> str:
    await ensure_database_schema()
    user_id = str(uuid4())
    async with get_sessionmaker()() as session:
        session.add(
            User(
                id=user_id,
                email=f"provider-snapshot-{uuid4().hex}@example.com",
                email_verified_at=utc_now(),
            )
        )
        await session.commit()
    return user_id


async def _authenticated_user(async_client) -> dict:
    email = f"provider-snapshot-endpoint-{uuid4().hex}@example.com"
    await async_client.post(
        "/api/auth/send-code", json={"email": email, "purpose": "register"}
    )
    response = await async_client.post(
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


async def _remove_provider_snapshot(job_id: str) -> None:
    snapshot_fields = snapshot_for_preset(resolve_preset("codex_full"))
    async with get_sessionmaker()() as session:
        job = await session.get(GenerationJob, job_id)
        assert job is not None
        job.prompt_payload = {
            key: value
            for key, value in job.prompt_payload.items()
            if key not in snapshot_fields
        }
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["fixture", "mock"])
async def test_new_fixture_or_mock_job_gets_fixed_provider_snapshot(async_client, provider: str):
    user = await _authenticated_user(async_client)

    job = await job_store.create(
        CreateJobRequest(character_session_id=f"{provider}-snapshot", free_text=""),
        provider=provider,
        user_id=user["id"],
    )

    assert {
        key: job.prompt_payload[key]
        for key in snapshot_for_preset(resolve_preset("codex_full"))
    } == snapshot_for_preset(resolve_preset("codex_full"))


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["fixture", "mock"])
async def test_cancel_legacy_snapshotless_fixture_or_mock_job_is_safe(async_client, provider: str):
    user = await _authenticated_user(async_client)
    job = await job_store.create(
        CreateJobRequest(character_session_id=f"legacy-{provider}-cancel", free_text=""),
        provider=provider,
        user_id=user["id"],
    )
    await _remove_provider_snapshot(job.id)

    response = await async_client.post(f"/api/generation/jobs/{job.id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["fixture", "mock"])
async def test_accept_legacy_snapshotless_fixture_or_mock_job_is_safe(async_client, provider: str):
    user = await _authenticated_user(async_client)
    job = await job_store.create(
        CreateJobRequest(character_session_id=f"legacy-{provider}-accept", free_text=""),
        provider=provider,
        user_id=user["id"],
    )
    await _remove_provider_snapshot(job.id)
    await job_store.set_outputs(
        job.id,
        [
            StoredOutput(
                index=1,
                object_key="fixture/candidate-1.webp",
                image_url="/api/static/fixtures/kigurumi-candidate-1.webp",
            )
        ],
    )

    response = await async_client.post(
        f"/api/generation/jobs/{job.id}/accept", json={"output_index": 1}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_retired_snapshot_cannot_create_a_new_job(test_app):
    user_id = await _repository_user()
    snapshot = snapshot_for_preset(resolve_preset("qwen_seedream"))
    with pytest.raises(RetiredProviderError):
        await job_store.create(
            CreateJobRequest(character_session_id="retired", free_text="unchanged"),
            provider="qwen_seedream", user_id=user_id, provider_snapshot=snapshot)


@pytest.mark.parametrize("preset", ["qwen_codex", "qwen_seedream"])
def test_retired_snapshot_never_falls_back_even_with_fixture_transport(preset, monkeypatch):
    monkeypatch.setattr(get_settings(), "generation_provider", "fixture")
    with pytest.raises(RetiredProviderError):
        get_provider_for_preset(snapshot_for_preset(resolve_preset(preset)))

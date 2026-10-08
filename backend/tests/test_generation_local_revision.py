from __future__ import annotations

import json
import os
from io import BytesIO
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.core.config import get_settings
from app.db.session import ensure_database_schema, reset_session_factory
from app.generation.job_store import job_store
from app.generation.provider import ImageGenerationProvider, ProviderOutput, get_generation_provider
from app.generation.router import clear_jobs_async
from app.main import create_app


def image_bytes(size: tuple[int, int] = (4, 4), color: tuple[int, int, int, int] = (10, 20, 30, 255)) -> bytes:
    image = Image.new("RGBA", size, color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def mask_bytes(size: tuple[int, int] = (4, 4), filled: bool = True) -> bytes:
    image = Image.new("L", size, 0)
    if filled:
        image.putpixel((1, 1), 255)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest_asyncio.fixture(autouse=True)
async def clean_jobs(monkeypatch, tmp_path):
    database_url = os.environ.get("KIG_TEST_DATABASE_URL") or (
        "postgresql+asyncpg://kig:kig@127.0.0.1:15432/kig_preview"
    )
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    monkeypatch.setenv("CAPTCHA_ENABLED", "false")
    monkeypatch.setenv("EMAIL_CODE_SEND_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("REFERENCE_UPLOAD_DIR", str(tmp_path / "refs"))
    monkeypatch.setenv("CODEX_WORKSPACE_DIR", str(tmp_path / "codex"))
    monkeypatch.setenv("CODEX_OUTPUT_DIR", str(tmp_path / "generated"))
    monkeypatch.setenv("PROVIDER_PRESET_PATH", str(tmp_path / "provider-preset.json"))
    monkeypatch.setenv("ADMIN_QUOTA_POLICY_PATH", str(tmp_path / "quota-policy.json"))
    monkeypatch.setenv("GENERATION_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite3"))
    from app.generation import router
    original_resolve = router.resolve_repo_path
    monkeypatch.setattr(router, "resolve_repo_path", lambda path: (
        tmp_path / "local-revisions" if str(path) == "runtime/local-revisions"
        else original_resolve(path)
    ))
    submitted_jobs = []
    monkeypatch.setattr(router.generation_queue, "submit_job",
                        lambda job_id, provider: submitted_jobs.append(job_id))
    monkeypatch.setenv("EMAIL_CODE_SEND_MAX_PER_IP", "100")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("SESSION_COOKIE_NAME", "kig_session")
    get_settings.cache_clear()
    reset_session_factory()
    await ensure_database_schema()
    await clear_jobs_async()
    yield submitted_jobs
    await clear_jobs_async()
    get_settings.cache_clear()


async def make_app(monkeypatch, **env_overrides: str):
    monkeypatch.setenv("GENERATION_PROVIDER", "codex")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "true")
    monkeypatch.setenv("CODEX_USAGE_CHECK_ENABLED", "false")
    for key, value in env_overrides.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    reset_session_factory()
    await ensure_database_schema()
    return create_app()


async def register_user(client: AsyncClient) -> None:
    email = f"local-revision-{uuid.uuid4().hex}@example.com"
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


class SeedreamRouteProvider(ImageGenerationProvider):
    name = "seedream"

    async def generate(self, job_id: str, prompt_payload: dict) -> list[ProviderOutput]:
        return [
            ProviderOutput(
                index=1,
                object_key=f"seedream/{job_id}/outputs/candidate-1.png",
                image_url=f"/api/generated/seedream/{job_id}/outputs/candidate-1.png",
                width=4,
                height=4,
            )
        ]


async def test_local_revision_rejects_size_mismatch(monkeypatch):
    app = await make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/api/generation/local-revision-jobs",
            data={"metadata": json.dumps({"edit_note": "fix mouth", "selected_reference_keys": []})},
            files={
                "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
                "mask_image": ("mask.png", mask_bytes((5, 4)), "image/png"),
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "local_revision_size_mismatch"


async def test_local_revision_rejects_empty_mask(monkeypatch):
    app = await make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/api/generation/local-revision-jobs",
            data={"metadata": json.dumps({"edit_note": "fix mouth", "selected_reference_keys": []})},
            files={
                "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
                "mask_image": ("mask.png", mask_bytes((4, 4), filled=False), "image/png"),
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "local_revision_mask_empty"


async def test_local_revision_creates_front_local_revision_job(monkeypatch, tmp_path, clean_jobs):
    app = await make_app(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/api/generation/local-revision-jobs",
            data={
                "metadata": json.dumps(
                    {
                        "character_session_id": "session-a",
                        "edit_note": "make the mouth smaller",
                        "selected_reference_keys": [],
                        "reference_descriptions": [],
                        "locale": "zh-CN",
                    }
                )
            },
            files={
                "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
                "mask_image": ("mask.png", mask_bytes((4, 4)), "image/png"),
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["generation_mode"] == "front_local_revision"
    assert body["character_session_id"] == "session-a"
    assert clean_jobs == [body["id"]]


async def test_local_revision_rejects_retired_provider_without_codex_usage_check(monkeypatch, tmp_path):
    app = await make_app(
        monkeypatch,
        CODEX_USAGE_CHECK_ENABLED="true",
        CAPTCHA_ENABLED="false",
        EMAIL_CODE_SEND_COOLDOWN_SECONDS="0",
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )
    provider = SeedreamRouteProvider()
    app.dependency_overrides[get_generation_provider] = lambda: provider

    async def fail_if_checked(*_args, **_kwargs):
        raise AssertionError("Seedream must not run the Codex usage check")

    monkeypatch.setattr("app.generation.router.ensure_codex_usage_allows_generation", fail_if_checked)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await register_user(client)
            response = await client.post(
                "/api/generation/local-revision-jobs",
                data={"metadata": json.dumps({"edit_note": "fix mouth", "selected_reference_keys": []})},
                files={
                    "base_image": ("base.png", image_bytes((1024, 1024)), "image/png"),
                    "mask_image": ("mask.png", mask_bytes((1024, 1024)), "image/png"),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == "legacy_provider_retired"


async def test_seedream_local_revision_rejects_non_1024_inputs_before_quota(
    monkeypatch, tmp_path
):
    app = await make_app(
        monkeypatch,
        CAPTCHA_ENABLED="false",
        EMAIL_CODE_SEND_COOLDOWN_SECONDS="0",
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )
    app.dependency_overrides[get_generation_provider] = lambda: SeedreamRouteProvider()

    async def fail_if_quota_consumed(*_args, **_kwargs):
        raise AssertionError("Seedream dimension validation must run before quota consumption")

    async def fail_if_job_created(*_args, **_kwargs):
        raise AssertionError("Seedream dimension validation must run before job creation")

    monkeypatch.setattr(
        "app.generation.router.consume_generation_quota", fail_if_quota_consumed
    )
    monkeypatch.setattr(job_store, "create", fail_if_job_created)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await register_user(client)
            response = await client.post(
                "/api/generation/local-revision-jobs",
                data={"metadata": json.dumps({"edit_note": "fix mouth", "selected_reference_keys": []})},
                files={
                    "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
                    "mask_image": ("mask.png", mask_bytes((4, 4)), "image/png"),
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["detail"] == "legacy_provider_retired"


async def test_local_revision_uses_safe_session_path(monkeypatch, tmp_path):
    app = await make_app(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/api/generation/local-revision-jobs",
            data={
                "metadata": json.dumps(
                    {
                        "character_session_id": "../../escape",
                        "edit_note": "make the mouth smaller",
                        "selected_reference_keys": [],
                    }
                )
            },
            files={
                "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
                "mask_image": ("mask.png", mask_bytes((4, 4)), "image/png"),
            },
        )

    assert response.status_code == 200
    assert response.json()["character_session_id"] == "escape"


async def test_local_revision_rejects_malformed_metadata(monkeypatch):
    app = await make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/api/generation/local-revision-jobs",
            data={"metadata": json.dumps({"edit_note": "fix mouth", "selected_reference_keys": "bad"})},
            files={
                "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
                "mask_image": ("mask.png", mask_bytes((4, 4)), "image/png"),
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "local_revision_metadata_invalid"


async def test_local_revision_rejects_selected_reference_keys(monkeypatch):
    app = await make_app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/api/generation/local-revision-jobs",
            data={
                "metadata": json.dumps(
                    {
                        "edit_note": "fix mouth",
                        "selected_reference_keys": ["front:references/upload-1/front.webp"],
                    }
                )
            },
            files={
                "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
                "mask_image": ("mask.png", mask_bytes((4, 4)), "image/png"),
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "local_revision_selected_references_unsupported"
    assert await job_store.list() == []


async def test_local_revision_accepts_uploaded_references(monkeypatch, tmp_path):
    app = await make_app(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/api/generation/local-revision-jobs",
            data={
                "metadata": json.dumps(
                    {
                        "edit_note": "copy this mouth",
                        "selected_reference_keys": [],
                        "uploaded_reference_descriptions": ["mouth reference"],
                    }
                )
            },
            files=[
                ("base_image", ("base.png", image_bytes((4, 4)), "image/png")),
                ("mask_image", ("mask.png", mask_bytes((4, 4)), "image/png")),
                ("reference_files", ("mouth.png", image_bytes((2, 2)), "image/png")),
            ],
        )

    assert response.status_code == 200
    job = await job_store.get(response.json()["id"])
    assert job is not None
    uploaded_key = f"supplemental:references/local-revisions/{job.character_session_id}/{job.id}/reference-1.png"
    assert uploaded_key in job.prompt_payload["reference_keys"]
    assert {
        "reference_key": uploaded_key,
        "description": "mouth reference",
    } in job.prompt_payload["reference_descriptions"]
    assert (tmp_path / "refs" / "local-revisions" / job.character_session_id / job.id / "reference-1.png").is_file()


async def test_local_revision_rejects_more_than_one_uploaded_reference(monkeypatch, tmp_path):
    app = await make_app(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/api/generation/local-revision-jobs",
            data={"metadata": json.dumps({"edit_note": "copy mouth", "selected_reference_keys": []})},
            files=[
                ("base_image", ("base.png", image_bytes((4, 4)), "image/png")),
                ("mask_image", ("mask.png", mask_bytes((4, 4)), "image/png")),
                ("reference_files", ("ref-1.png", image_bytes((2, 2)), "image/png")),
                ("reference_files", ("ref-2.png", image_bytes((2, 2)), "image/png")),
            ],
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "local_revision_too_many_references"
    assert await job_store.list() == []


async def test_local_revision_rejects_invalid_uploaded_reference_without_creating_job(monkeypatch, tmp_path):
    app = await make_app(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await register_user(client)
        response = await client.post(
            "/api/generation/local-revision-jobs",
            data={"metadata": json.dumps({"edit_note": "copy mouth", "selected_reference_keys": []})},
            files=[
                ("base_image", ("base.png", image_bytes((4, 4)), "image/png")),
                ("mask_image", ("mask.png", mask_bytes((4, 4)), "image/png")),
                ("reference_files", ("bad.png", b"not an image", "image/png")),
            ],
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "local_revision_reference_invalid"
    assert await job_store.list() == []

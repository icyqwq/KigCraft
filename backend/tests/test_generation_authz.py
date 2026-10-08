import json
import uuid

import pytest
from sqlalchemy import select
from pathlib import Path
from types import SimpleNamespace

from httpx import AsyncClient

from app.auth.admin_cli import create_or_update_admin
from app.auth.models import UserRole
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.db.models import ReferenceAsset
from app.db.session import get_sessionmaker
from app.generation.job_store import StoredOutput, job_store
from app.generation.queue import generation_queue
from app.generation.router import list_jobs
from app.generation.schemas import CreateJobRequest


async def register_user(async_client: AsyncClient, email: str | None = None) -> dict:
    user_email = email or f"user-{uuid.uuid4().hex}@example.com"
    await async_client.post(
        "/api/auth/send-code",
        json={"email": user_email, "purpose": "register"},
    )
    response = await async_client.post(
        "/api/auth/register",
        json={
            "terms_accepted": True,
            "terms_version": get_settings().current_terms_version,
            "email": user_email,
            "code": "123456",
            "password": "UserPassphrase7!",
        },
    )
    assert response.status_code == 200
    return response.json()


async def login_admin(async_client: AsyncClient) -> dict:
    email = f"admin-{uuid.uuid4().hex}@example.com"
    password = "AdminPassphrase7!"
    await create_or_update_admin(email, password)
    response = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()


async def create_owned_job(user_id: str, *, with_output: bool = False) -> str:
    job = await job_store.create(
        CreateJobRequest(
            character_session_id=f"authz-{uuid.uuid4().hex}",
            free_text="owner job",
            reference_keys=[],
            requirement_ids=[],
        ),
        provider="fixture",
        user_id=user_id,
    )
    if with_output:
        await job_store.set_outputs(
            job.id,
            [
                StoredOutput(
                    index=1,
                    object_key=f"outputs/{job.id}/candidate-1.webp",
                    image_url=f"/api/generated/{job.id}/candidate-1.webp",
                )
            ],
        )
    return job.id


async def test_generation_requires_login(async_client: AsyncClient):
    response = await async_client.post(
        "/api/generation/jobs",
        json={"free_text": "make it cute", "reference_keys": [], "requirement_ids": []},
    )
    assert response.status_code == 401


async def test_admin_history_lists_only_own_jobs(monkeypatch):
    list_calls: list[dict] = []

    async def fake_list_page(**kwargs):
        list_calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(job_store, "list_page", fake_list_page)

    await list_jobs(
        current_user=SimpleNamespace(id="admin-user-id", role=UserRole.admin),
        status=None,
        generation_mode=None,
        limit=50,
        offset=0,
    )

    assert list_calls == [
        {
            "user_id": "admin-user-id",
            "status": None,
            "generation_mode": None,
            "limit": 50,
            "offset": 0,
        }
    ]


async def test_user_can_create_and_list_only_own_jobs(async_client: AsyncClient):
    user_a = await register_user(async_client)
    create_response = await async_client.post(
        "/api/generation/jobs",
        json={"free_text": "owner a job", "reference_keys": [], "requirement_ids": []},
    )
    assert create_response.status_code == 200
    job_id = create_response.json()["id"]

    list_response = await async_client.get("/api/generation/jobs")
    assert list_response.status_code == 200
    listed_jobs = list_response.json()["jobs"]
    assert [job["id"] for job in listed_jobs] == [job_id]
    assert listed_jobs[0]["character_session_id"] == create_response.json()["character_session_id"]
    assert listed_jobs[0]["created_at"]
    assert listed_jobs[0]["updated_at"]

    await async_client.post("/api/auth/logout")
    await register_user(async_client)

    other_get = await async_client.get(f"/api/generation/jobs/{job_id}")
    assert other_get.status_code == 404

    other_list = await async_client.get("/api/generation/jobs")
    assert other_list.status_code == 200
    assert other_list.json()["jobs"] == []
    assert user_a["email"].endswith("@example.com")


async def test_generation_events_deny_anonymous_and_non_owner_but_allow_admin(
    async_client: AsyncClient,
):
    owner = await register_user(async_client)
    job_id = await create_owned_job(owner["id"])

    await async_client.post("/api/auth/logout")
    anonymous_response = await async_client.get(f"/api/generation/jobs/{job_id}/events")
    assert anonymous_response.status_code == 401

    await register_user(async_client)
    other_response = await async_client.get(f"/api/generation/jobs/{job_id}/events")
    assert other_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await login_admin(async_client)
    admin_response = await async_client.get(f"/api/generation/jobs/{job_id}/events")
    assert admin_response.status_code == 200
    assert [event["type"] for event in admin_response.json()] == ["queued"]


async def test_generation_job_detail_denies_non_owner_but_allows_admin(
    async_client: AsyncClient,
):
    owner = await register_user(async_client)
    job_id = await create_owned_job(owner["id"])

    await async_client.post("/api/auth/logout")
    await register_user(async_client)
    other_response = await async_client.get(f"/api/generation/jobs/{job_id}")
    assert other_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await login_admin(async_client)
    admin_response = await async_client.get(f"/api/generation/jobs/{job_id}")
    assert admin_response.status_code == 200
    assert admin_response.json()["id"] == job_id


async def test_accept_deny_anonymous_and_non_owner_but_allow_admin(
    async_client: AsyncClient,
):
    owner = await register_user(async_client)
    job_id = await create_owned_job(owner["id"], with_output=True)

    await async_client.post("/api/auth/logout")
    anonymous_response = await async_client.post(
        f"/api/generation/jobs/{job_id}/accept",
        json={"output_index": 1},
    )
    assert anonymous_response.status_code == 401

    await register_user(async_client)
    other_response = await async_client.post(
        f"/api/generation/jobs/{job_id}/accept",
        json={"output_index": 1},
    )
    assert other_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await login_admin(async_client)
    admin_response = await async_client.post(
        f"/api/generation/jobs/{job_id}/accept",
        json={"output_index": 1},
    )
    assert admin_response.status_code == 200
    assert admin_response.json()["status"] == "accepted"
    assert admin_response.json()["accepted_output_index"] == 1


async def test_cancel_deny_anonymous_and_non_owner_but_allow_admin(
    async_client: AsyncClient,
):
    owner = await register_user(async_client)
    job_id = await create_owned_job(owner["id"])

    await async_client.post("/api/auth/logout")
    anonymous_response = await async_client.post(f"/api/generation/jobs/{job_id}/cancel")
    assert anonymous_response.status_code == 401

    await register_user(async_client)
    other_response = await async_client.post(f"/api/generation/jobs/{job_id}/cancel")
    assert other_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await login_admin(async_client)
    admin_response = await async_client.post(f"/api/generation/jobs/{job_id}/cancel")
    assert admin_response.status_code == 200
    assert admin_response.json()["status"] == "cancelled"


async def test_generated_assets_require_owner_or_admin(async_client: AsyncClient):
    owner = await register_user(async_client)
    job_id = await create_owned_job(owner["id"])
    object_key = f"owned/{job_id}/candidate.webp"
    output_path = resolve_repo_path(get_settings().codex_output_dir) / Path(*object_key.split("/"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"webp")
    await job_store.set_outputs(
        job_id,
        [
            StoredOutput(
                index=1,
                object_key=object_key,
                image_url=f"/api/generated/{object_key}",
            )
        ],
    )

    owner_response = await async_client.get(f"/api/generated/{object_key}")
    assert owner_response.status_code == 200
    assert owner_response.headers["content-type"].startswith("image/webp")

    await async_client.post("/api/auth/logout")
    anonymous_response = await async_client.get(f"/api/generated/{object_key}")
    assert anonymous_response.status_code == 404

    await register_user(async_client)
    other_response = await async_client.get(f"/api/generated/{object_key}")
    assert other_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await login_admin(async_client)
    admin_response = await async_client.get(f"/api/generated/{object_key}")
    assert admin_response.status_code == 200


async def test_generated_assets_allow_owner_and_admin_when_image_url_path_differs_from_object_key(
    async_client: AsyncClient,
):
    owner = await register_user(async_client)
    job_id = await create_owned_job(owner["id"])
    object_key = f"codex/{job_id}/outputs/candidate-1.webp"
    relative_url_path = f"session-a/{job_id}/outputs/candidate-1.webp"
    output_path = resolve_repo_path(get_settings().codex_output_dir) / Path(*relative_url_path.split("/"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"webp")
    await job_store.set_outputs(
        job_id,
        [
            StoredOutput(
                index=1,
                object_key=object_key,
                image_url=f"/api/generated/{relative_url_path}",
            )
        ],
    )

    owner_response = await async_client.get(f"/api/generated/{relative_url_path}")
    assert owner_response.status_code == 200
    assert owner_response.headers["content-type"].startswith("image/webp")

    await async_client.post("/api/auth/logout")
    await register_user(async_client)
    other_response = await async_client.get(f"/api/generated/{relative_url_path}")
    assert other_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await login_admin(async_client)
    admin_response = await async_client.get(f"/api/generated/{relative_url_path}")
    assert admin_response.status_code == 200


async def test_generated_assets_hide_unregistered_and_escaped_paths(async_client: AsyncClient):
    await register_user(async_client)
    generated_root = resolve_repo_path(get_settings().codex_output_dir)
    orphan_path = generated_root / "orphan" / "candidate.webp"
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_bytes(b"webp")

    missing_row_response = await async_client.get("/api/generated/orphan/candidate.webp")
    assert missing_row_response.status_code == 404

    escaped_response = await async_client.get("/api/generated/../secret.webp")
    assert escaped_response.status_code == 404


async def test_reference_upload_requires_login(async_client: AsyncClient):
    response = await async_client.post(
        "/api/references",
        data={"kind": "front"},
        files={"file": ("front.webp", b"webp-image", "image/webp")},
    )

    assert response.status_code == 401


async def test_reference_assets_require_owner_or_admin_for_uploaded_asset(
    async_client: AsyncClient,
):
    await register_user(async_client)
    upload_response = await async_client.post(
        "/api/references",
        data={"kind": "front"},
        files={"file": ("front.webp", b"webp-image", "image/webp")},
    )
    assert upload_response.status_code == 200
    object_key = upload_response.json()["object_key"]

    owner_response = await async_client.get(f"/api/references/{object_key}")
    assert owner_response.status_code == 200
    assert owner_response.headers["content-type"].startswith("image/webp")

    await async_client.post("/api/auth/logout")
    anonymous_response = await async_client.get(f"/api/references/{object_key}")
    assert anonymous_response.status_code == 401

    await register_user(async_client)
    other_response = await async_client.get(f"/api/references/{object_key}")
    assert other_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await login_admin(async_client)
    admin_response = await async_client.get(f"/api/references/{object_key}")
    assert admin_response.status_code == 200


async def test_reference_assets_deny_job_prompt_without_upload_record(
    async_client: AsyncClient,
):
    owner = await register_user(async_client)
    object_key = "references/legacy/front.webp"
    reference_path = (
        resolve_repo_path(get_settings().reference_upload_dir)
        / Path(object_key).relative_to("references")
    )
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(b"webp-image")
    await job_store.create(
        CreateJobRequest(
            character_session_id=f"reference-authz-{uuid.uuid4().hex}",
            free_text="use legacy reference",
            reference_keys=[f"front:{object_key}"],
            requirement_ids=[],
        ),
        provider="fixture",
        user_id=owner["id"],
    )

    owner_response = await async_client.get(f"/api/references/{object_key}")
    assert owner_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await register_user(async_client)
    other_response = await async_client.get(f"/api/references/{object_key}")
    assert other_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await login_admin(async_client)
    admin_response = await async_client.get(f"/api/references/{object_key}")
    assert admin_response.status_code == 200


async def test_reference_assets_do_not_allow_forged_job_prompt_legacy_reference(
    async_client: AsyncClient,
):
    await register_user(async_client)
    object_key = "references/legacy-forged/front.webp"
    reference_path = (
        resolve_repo_path(get_settings().reference_upload_dir)
        / Path(object_key).relative_to("references")
    )
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(b"webp-image")

    await async_client.post("/api/auth/logout")
    other = await register_user(async_client)
    await job_store.create(
        CreateJobRequest(
            character_session_id=f"reference-forged-{uuid.uuid4().hex}",
            free_text="use forged legacy reference",
            reference_keys=[f"front:{object_key}"],
            requirement_ids=[],
        ),
        provider="fixture",
        user_id=other["id"],
    )

    response = await async_client.get(f"/api/references/{object_key}")
    assert response.status_code == 404


async def test_detail_analysis_denies_other_users_uploaded_reference(
    async_client: AsyncClient,
):
    await register_user(async_client)
    upload_response = await async_client.post(
        "/api/references",
        data={"kind": "front"},
        files={"file": ("front.webp", b"webp-image", "image/webp")},
    )
    assert upload_response.status_code == 200
    object_key = upload_response.json()["object_key"]

    await async_client.post("/api/auth/logout")
    await register_user(async_client)

    response = await async_client.post(
        "/api/generation/detail-analysis",
        json={
            "free_text": "inspect this reference",
            "reference_keys": [f"front:{object_key}"],
        },
    )

    assert response.status_code == 404


async def test_generation_job_denies_other_users_uploaded_reference_without_creating_job(
    async_client: AsyncClient,
    monkeypatch,
):
    await register_user(async_client)
    upload_response = await async_client.post(
        "/api/references",
        data={"kind": "front"},
        files={"file": ("front.webp", b"webp-image", "image/webp")},
    )
    assert upload_response.status_code == 200
    object_key = upload_response.json()["object_key"]

    submit_calls: list[tuple[str, str]] = []

    def fake_submit_job(job_id: str, provider) -> None:
        submit_calls.append((job_id, provider.name))

    monkeypatch.setattr(generation_queue, "submit_job", fake_submit_job)

    await async_client.post("/api/auth/logout")
    await register_user(async_client)

    response = await async_client.post(
        "/api/generation/jobs",
        json={
            "free_text": "use another user's uploaded reference",
            "reference_keys": [f"front:{object_key}"],
            "requirement_ids": [],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one valid reference image is required"
    assert submit_calls == []

    jobs_response = await async_client.get("/api/generation/jobs")
    assert jobs_response.status_code == 200
    assert jobs_response.json()["jobs"] == []


async def test_generation_job_rejects_untracked_uploaded_reference_without_creating_job(
    async_client: AsyncClient,
    monkeypatch,
):
    await register_user(async_client)
    object_key = "references/untracked/front.webp"
    reference_path = (
        resolve_repo_path(get_settings().reference_upload_dir)
        / Path(object_key).relative_to("references")
    )
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(b"webp-image")

    submit_calls: list[tuple[str, str]] = []

    def fake_submit_job(job_id: str, provider) -> None:
        submit_calls.append((job_id, provider.name))

    monkeypatch.setattr(generation_queue, "submit_job", fake_submit_job)

    response = await async_client.post(
        "/api/generation/jobs",
        json={
            "free_text": "use untracked uploaded reference",
            "reference_keys": [f"front:{object_key}"],
            "requirement_ids": [],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one valid reference image is required"
    assert submit_calls == []

    jobs_response = await async_client.get("/api/generation/jobs")
    assert jobs_response.status_code == 200
    assert jobs_response.json()["jobs"] == []


async def test_legacy_generation_job_denies_other_users_uploaded_reference_without_creating_job(
    async_client: AsyncClient,
    monkeypatch,
):
    await register_user(async_client)
    upload_response = await async_client.post(
        "/api/references",
        data={"kind": "front"},
        files={"file": ("front.webp", b"webp-image", "image/webp")},
    )
    assert upload_response.status_code == 200
    object_key = upload_response.json()["object_key"]

    run_calls: list[tuple[str, str]] = []

    async def fake_run_job(job_id: str, provider):
        run_calls.append((job_id, provider.name))

    monkeypatch.setattr(generation_queue, "run_job", fake_run_job)

    await async_client.post("/api/auth/logout")
    await register_user(async_client)

    response = await async_client.post(
        "/api/generation/projects/session-legacy/jobs",
        json={
            "project_id": "session-legacy",
            "free_text": "use another user's uploaded reference",
            "chip_ids": [],
            "reference_keys": [f"front:{object_key}"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one valid reference image is required"
    assert run_calls == []

    jobs_response = await async_client.get("/api/generation/jobs")
    assert jobs_response.status_code == 200
    assert jobs_response.json()["jobs"] == []


async def test_legacy_generation_job_rejects_untracked_uploaded_reference_without_creating_job(
    async_client: AsyncClient,
    monkeypatch,
):
    await register_user(async_client)
    object_key = "references/untracked-legacy/front.webp"
    reference_path = (
        resolve_repo_path(get_settings().reference_upload_dir)
        / Path(object_key).relative_to("references")
    )
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference_path.write_bytes(b"webp-image")

    run_calls: list[tuple[str, str]] = []

    async def fake_run_job(job_id: str, provider):
        run_calls.append((job_id, provider.name))

    monkeypatch.setattr(generation_queue, "run_job", fake_run_job)

    response = await async_client.post(
        "/api/generation/projects/session-legacy/jobs",
        json={
            "project_id": "session-legacy",
            "free_text": "use untracked uploaded reference",
            "chip_ids": [],
            "reference_keys": [f"front:{object_key}"],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "At least one valid reference image is required"
    assert run_calls == []

    jobs_response = await async_client.get("/api/generation/jobs")
    assert jobs_response.status_code == 200
    assert jobs_response.json()["jobs"] == []



@pytest.mark.parametrize("legacy", [False, True], ids=["current", "legacy"])
@pytest.mark.parametrize("unavailable", ["foreign", "untracked", "missing"])
async def test_mixed_references_only_submit_owned_available_inputs(
    async_client, monkeypatch, legacy, unavailable,
):
    original_owner = await register_user(async_client)
    upload = await async_client.post(
        "/api/references", data={"kind": "front"},
        files={"file": ("front.webp", b"private-reference", "image/webp")},
    )
    assert upload.status_code == 200
    unavailable_key = upload.json()["object_key"]
    unavailable_path = (
        resolve_repo_path(get_settings().reference_upload_dir)
        / Path(unavailable_key).relative_to("references")
    )
    if unavailable == "foreign":
        await async_client.post("/api/auth/logout")
        await register_user(async_client)
    elif unavailable == "untracked":
        async with get_sessionmaker()() as session:
            record = await session.scalar(select(ReferenceAsset).where(
                ReferenceAsset.object_key == unavailable_key))
            await session.delete(record)
            await session.commit()
    else:
        unavailable_path.unlink()

    owned_upload = await async_client.post(
        "/api/references", data={"kind": "front"},
        files={"file": ("owned.webp", b"owned-reference", "image/webp")},
    )
    assert owned_upload.status_code == 200
    owned_key = "front:" + owned_upload.json()["object_key"]
    rejected_key = "front:" + unavailable_key
    submitted = []

    def submit(job_id, provider):
        submitted.append(job_id)

    async def run(job_id, provider):
        submitted.append(job_id)

    monkeypatch.setattr(generation_queue, "submit_job", submit)
    monkeypatch.setattr(generation_queue, "run_job", run)
    payload = {"free_text": "use my reference", "reference_keys": [owned_key, rejected_key]}
    if legacy:
        endpoint = "/api/generation/projects/mixed-reference-session/jobs"
        payload.update(project_id="mixed-reference-session", chip_ids=[])
    else:
        endpoint = "/api/generation/jobs"
        payload.update(reference_descriptions=[
            {"reference_key": owned_key, "description": "keep owned"},
            {"reference_key": rejected_key, "description": "UNAVAILABLE_DESCRIPTION"},
        ], detail_lock={"crops": [
            {"reference_key": owned_key, "kind": "eyes", "description": "owned crop"},
            {"reference_key": rejected_key, "kind": "eyes", "description": "UNAVAILABLE_CROP"},
        ]})
    response = await async_client.post(endpoint, json=payload)
    assert response.status_code == 200
    job_id = response.json()["id"]
    assert submitted == [job_id]
    job = await job_store.get(job_id)
    assert job is not None
    assert job.prompt_payload["reference_keys"] == [owned_key]
    serialized = json.dumps(job.prompt_payload)
    assert unavailable_key not in serialized
    assert "UNAVAILABLE_DESCRIPTION" not in serialized
    assert "UNAVAILABLE_CROP" not in serialized
    if not legacy:
        assert job.prompt_payload["reference_descriptions"] == [
            {"reference_key": owned_key, "description": "keep owned"},
        ]
        assert [crop["reference_key"] for crop in job.prompt_payload["detail_lock"]["crops"]] == [owned_key]

    async with get_sessionmaker()() as session:
        record = await session.scalar(select(ReferenceAsset).where(
            ReferenceAsset.object_key == unavailable_key))
    if unavailable == "foreign":
        assert record is not None
        assert record.user_id == original_owner["id"]
        assert unavailable_path.read_bytes() == b"private-reference"
    else:
        assert record is None
        if unavailable == "untracked":
            assert unavailable_path.read_bytes() == b"private-reference"

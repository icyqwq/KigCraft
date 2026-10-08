import io
import json
import uuid

from httpx import AsyncClient
from PIL import Image

from app.auth.admin_cli import create_or_update_admin
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.core.time import utc_now
from app.db.models import AlbumItem, User
from app.db.session import ensure_database_schema, get_sessionmaker
from app.audit.router import _usage_trend_summary
from app.generation.job_store import StoredEvent, StoredJob, StoredOutput, job_store
from app.generation.schemas import CreateJobRequest
from app.generation.usage import TokenUsage


def stored_job_for_trend(
    *,
    job_id: str,
    status: str,
    updated_at: str,
    output_count: int = 0,
    total_tokens: int | None = None,
) -> StoredJob:
    return StoredJob(
        id=job_id,
        character_session_id=f"session-{job_id}",
        generation_mode="front_design",
        expected_output_count=1,
        status=status,
        progress=100,
        queue_position=None,
        phase_label=status,
        provider="codex",
        prompt_payload={},
        token_usage=TokenUsage(total_tokens=total_tokens) if total_tokens is not None else None,
        outputs=[
            StoredOutput(
                index=index + 1,
                object_key=f"outputs/{job_id}-{index}.webp",
                image_url=f"/api/generated/{job_id}-{index}.webp",
            )
            for index in range(output_count)
        ],
        events=[
            StoredEvent(
                sequence=1,
                type=status,
                progress=100,
                message=status,
                created_at=updated_at,
            )
        ],
    )


async def create_audit_user() -> str:
    await ensure_database_schema()
    user_id = str(uuid.uuid4())
    async with get_sessionmaker()() as session:
        session.add(
            User(
                id=user_id,
                email=f"audit-{uuid.uuid4().hex}@example.com",
                email_verified_at=utc_now(),
            )
        )
        await session.commit()
    return user_id


async def login_audit_admin(async_client: AsyncClient) -> dict:
    email = f"audit-admin-{uuid.uuid4().hex}@example.com"
    password = "AdminPassphrase7!"
    await create_or_update_admin(email, password)
    response = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()


async def register_album_user(async_client: AsyncClient, email: str | None = None) -> dict:
    user_email = email or f"album-{uuid.uuid4().hex}@example.com"
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


async def test_audit_summary_includes_quota_and_job_counts(async_client: AsyncClient):
    user_id = await create_audit_user()
    queued = await job_store.create(
        CreateJobRequest(character_session_id="user-a", free_text="queued"),
        provider="fixture",
        user_id=user_id,
    )
    succeeded = await job_store.create(
        CreateJobRequest(character_session_id="user-b", free_text="done"),
        provider="fixture",
        user_id=user_id,
    )
    await job_store.update(
        succeeded.id,
        status="succeeded",
        progress=100,
        phase_label="done",
    )
    failed = await job_store.create(
        CreateJobRequest(character_session_id="user-c", free_text="failed"),
        provider="fixture",
        user_id=user_id,
    )
    await job_store.update(
        failed.id,
        status="failed",
        progress=100,
        phase_label="failed",
    )

    await login_audit_admin(async_client)

    response = await async_client.get("/api/audit/summary")

    assert response.status_code == 200
    summary = response.json()
    assert summary["total_users"] == 3
    assert summary["active_users"] == 1
    assert summary["job_counts"]["queued"] == 1
    assert summary["job_counts"]["succeeded"] == 1
    assert summary["job_counts"]["failed"] == 1
    assert summary["queue_length"] == 1
    assert summary["running_job_count"] == 0
    assert summary["total_calls"] == 3
    assert summary["success_rate"] == 0.3333
    assert summary["failure_rate"] == 0.3333
    assert summary["parallel_slots_used"] == 0
    assert summary["codex_quota"]["available"] is False
    assert summary["quota_policy"] == {
        "window_hours": 5,
        "window_limit": 8,
        "parallel_generation_limit": 4,
        "codex_usage_min_remaining_percent": 25,
        "daily_generation_limit": 20,
        "daily_generation_custom_limit_default": 20,
        "daily_generation_reset_hour": 6,
    }
    assert queued.id


def test_usage_trend_summary_returns_recent_two_week_daily_buckets():
    points = _usage_trend_summary(
        [
            stored_job_for_trend(
                job_id="old",
                status="succeeded",
                updated_at="2026-06-20T12:00:00+00:00",
                output_count=1,
                total_tokens=10,
            ),
            stored_job_for_trend(
                job_id="first-day",
                status="succeeded",
                updated_at="2026-06-27T10:00:00+00:00",
                output_count=2,
                total_tokens=40,
            ),
            stored_job_for_trend(
                job_id="same-day-failed",
                status="failed",
                updated_at="2026-07-02T18:00:00+00:00",
            ),
            stored_job_for_trend(
                job_id="last-day",
                status="generating",
                updated_at="2026-07-10T08:00:00+00:00",
                total_tokens=7,
            ),
        ]
    )

    assert len(points) == 14
    assert [point.label for point in points] == [
        "06-27",
        "06-28",
        "06-29",
        "06-30",
        "07-01",
        "07-02",
        "07-03",
        "07-04",
        "07-05",
        "07-06",
        "07-07",
        "07-08",
        "07-09",
        "07-10",
    ]
    assert points[0].calls == 1
    assert points[0].succeeded == 1
    assert points[0].images == 2
    assert points[0].tokens == 40
    assert points[5].calls == 1
    assert points[5].failed == 1
    assert points[-1].calls == 1
    assert points[-1].active == 1
    assert points[-1].tokens == 7


async def test_audit_summary_counts_generating_jobs_as_running(async_client: AsyncClient):
    user_id = await create_audit_user()
    generating = await job_store.create(
        CreateJobRequest(character_session_id="user-running", free_text="running"),
        provider="fixture",
        user_id=user_id,
    )
    await job_store.update(
        generating.id,
        status="generating",
        progress=45,
        phase_label="generating",
    )

    await login_audit_admin(async_client)

    response = await async_client.get("/api/audit/summary")

    assert response.status_code == 200
    summary = response.json()
    assert summary["queue_length"] == 0
    assert summary["running_job_count"] == 1
    assert summary["parallel_slots_used"] == 1
    assert summary["job_counts"]["generating"] == 1


async def test_quota_policy_can_be_updated(async_client: AsyncClient):
    await login_audit_admin(async_client)

    response = await async_client.patch(
        "/api/admin/quota-policy",
        json={
            "window_hours": 12,
            "window_limit": 20,
            "parallel_generation_limit": 3,
            "codex_usage_min_remaining_percent": 30,
            "daily_generation_limit": 25,
            "daily_generation_custom_limit_default": 100,
            "daily_generation_reset_hour": 6,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "window_hours": 12,
        "window_limit": 20,
        "parallel_generation_limit": 3,
        "codex_usage_min_remaining_percent": 30,
        "daily_generation_limit": 25,
        "daily_generation_custom_limit_default": 100,
        "daily_generation_reset_hour": 6,
    }

    summary_response = await async_client.get("/api/audit/summary")
    assert summary_response.json()["quota_policy"]["parallel_generation_limit"] == 3


async def test_quota_policy_persists_after_audit_state_reset(async_client: AsyncClient):
    from app.audit import router as audit_router

    await login_audit_admin(async_client)

    update_response = await async_client.patch(
        "/api/audit/quota-policy",
        json={
            "window_hours": 24,
            "window_limit": 18,
            "parallel_generation_limit": 5,
            "codex_usage_min_remaining_percent": 15,
            "daily_generation_limit": 18,
            "daily_generation_custom_limit_default": 120,
            "daily_generation_reset_hour": 6,
        },
    )
    assert update_response.status_code == 200

    audit_router.clear_audit_state()

    summary_response = await async_client.get("/api/audit/summary")

    assert summary_response.status_code == 200
    assert summary_response.json()["quota_policy"] == {
        "window_hours": 24,
        "window_limit": 18,
        "parallel_generation_limit": 5,
        "codex_usage_min_remaining_percent": 15,
        "daily_generation_limit": 18,
        "daily_generation_custom_limit_default": 120,
        "daily_generation_reset_hour": 6,
    }


async def test_codex_usage_gate_uses_persisted_admin_threshold(async_client: AsyncClient, monkeypatch):
    from datetime import UTC, datetime

    from fastapi import HTTPException

    from app.generation import codex_usage
    from app.generation.codex_app_server import CodexRateLimitWindow, CodexUsageSnapshot
    from app.generation.codex_usage import ensure_codex_usage_allows_generation

    settings = get_settings()
    settings.codex_usage_check_enabled = True

    async def fake_usage_snapshot(_settings):
        return CodexUsageSnapshot(
            checked_at=datetime.now(UTC),
            available=True,
            primary=CodexRateLimitWindow(used_percent=88, remaining_percent=12),
            source="test",
        )

    monkeypatch.setattr(codex_usage, "get_codex_usage_snapshot", fake_usage_snapshot)
    await login_audit_admin(async_client)

    update_response = await async_client.patch(
        "/api/audit/quota-policy",
        json={
            "window_hours": 5,
            "window_limit": 8,
            "parallel_generation_limit": 8,
            "codex_usage_min_remaining_percent": 10,
            "daily_generation_limit": 20,
            "daily_generation_custom_limit_default": 100,
            "daily_generation_reset_hour": 6,
        },
    )
    assert update_response.status_code == 200

    await ensure_codex_usage_allows_generation(settings)

    update_response = await async_client.patch(
        "/api/audit/quota-policy",
        json={
            "window_hours": 5,
            "window_limit": 8,
            "parallel_generation_limit": 8,
            "codex_usage_min_remaining_percent": 20,
        },
    )
    assert update_response.status_code == 200

    try:
        await ensure_codex_usage_allows_generation(settings)
    except HTTPException as exc:
        assert exc.status_code == 429
        assert exc.detail["remaining_percent"] == 12
    else:
        raise AssertionError("expected Codex usage gate to reject low remaining quota")


async def test_audit_summary_includes_token_usage_totals(async_client: AsyncClient):
    user_id = await create_audit_user()
    first = await job_store.create(
        CreateJobRequest(character_session_id="usage-a", free_text="usage-a"),
        provider="codex",
        user_id=user_id,
    )
    second = await job_store.create(
        CreateJobRequest(character_session_id="usage-b", free_text="usage-b"),
        provider="codex",
        user_id=user_id,
    )
    await job_store.set_outputs(
        first.id,
        [
            StoredOutput(index=1, object_key="outputs/a-1.webp", image_url="/api/generated/a-1.webp"),
            StoredOutput(index=2, object_key="outputs/a-2.webp", image_url="/api/generated/a-2.webp"),
        ],
    )
    await job_store.set_outputs(
        second.id,
        [StoredOutput(index=1, object_key="outputs/b-1.webp", image_url="/api/generated/b-1.webp")],
    )
    await job_store.record_token_usage(
        first.id,
        TokenUsage(
            input_tokens=120,
            cached_input_tokens=40,
            output_tokens=30,
            reasoning_output_tokens=8,
            total_tokens=150,
        ),
    )
    await job_store.record_token_usage(
        second.id,
        TokenUsage(
            input_tokens=80,
            cached_input_tokens=10,
            output_tokens=20,
            reasoning_output_tokens=4,
            total_tokens=100,
        ),
    )

    await login_audit_admin(async_client)

    response = await async_client.get("/api/audit/summary")

    assert response.status_code == 200
    assert response.json()["token_usage"] == {
        "jobs_with_usage": 2,
        "input_tokens": 200,
        "cached_input_tokens": 50,
        "output_tokens": 50,
        "reasoning_output_tokens": 12,
        "total_tokens": 250,
    }
    assert response.json()["image_usage"] == {
        "generated_images": 3,
        "jobs_with_outputs": 2,
        "images_with_token_usage": 3,
        "input_tokens_per_image": 66.67,
        "cached_input_tokens_per_image": 16.67,
        "output_tokens_per_image": 16.67,
        "reasoning_output_tokens_per_image": 4,
        "total_tokens_per_image": 83.33,
    }


async def test_audit_generation_jobs_list_includes_references_and_outputs(async_client: AsyncClient):
    user_id = await create_audit_user()
    reference_root = resolve_repo_path(get_settings().reference_upload_dir)
    reference_file = reference_root / "upload-a" / "front.webp"
    reference_file.parent.mkdir(parents=True, exist_ok=True)
    reference_file.write_bytes(b"reference-image")
    job = await job_store.create(
        CreateJobRequest(
            character_session_id="session-records",
            free_text="make eyes softer",
            reference_descriptions=[
                {"reference_key": "front:references/upload-a/front.webp", "description": "front view"}
            ],
            reference_keys=["front:references/upload-a/front.webp"],
            requirement_ids=["soft_eyes"],
        ),
        provider="codex_bridge",
        user_id=user_id,
    )
    await job_store.update(job.id, status="succeeded", progress=100, phase_label="done")
    await job_store.set_outputs(
        job.id,
        [StoredOutput(index=1, object_key="outputs/candidate-1.webp", image_url="/api/generated/job/candidate-1.webp")],
    )
    await job_store.record_token_usage(job.id, TokenUsage(input_tokens=12, output_tokens=8, total_tokens=20))

    assert (await async_client.get("/api/audit/generation-jobs")).status_code == 401
    await login_audit_admin(async_client)

    response = await async_client.get("/api/audit/generation-jobs")

    assert response.status_code == 200
    payload = response.json()
    records = payload["items"]
    assert payload["has_more"] is False
    assert records[0]["id"] == job.id
    assert records[0]["character_session_id"] == "session-records"
    assert records[0]["user_notes"] == "make eyes softer"
    assert records[0]["references"] == [
        {
            "reference_key": "front:references/upload-a/front.webp",
            "image_url": "/api/audit/references/references/upload-a/front.webp",
            "kind": "front",
            "description": "front view",
        }
    ]
    assert records[0]["outputs"][0]["image_url"] == "/api/generated/job/candidate-1.webp"
    assert records[0]["token_usage"]["total_tokens"] == 20

    image_response = await async_client.get(records[0]["references"][0]["image_url"])
    assert image_response.status_code == 200
    assert image_response.content == b"reference-image"


async def test_audit_generation_jobs_exposes_a_safe_failure_reason(async_client: AsyncClient):
    user_id = await create_audit_user()
    job = await job_store.create(
        CreateJobRequest(character_session_id="session-failure", free_text=""),
        provider="fixture",
        user_id=user_id,
    )
    await job_store.update(
        job.id,
        status="failed",
        progress=45,
        phase_label="Generation failed",
        event_type="failed",
        message="upstream_http_error: Seedream request failed with HTTP status 400. token=secret-value",
    )

    await login_audit_admin(async_client)
    response = await async_client.get("/api/audit/generation-jobs")

    assert response.status_code == 200
    record = next(item for item in response.json()["items"] if item["id"] == job.id)
    assert record["failure_reason"].startswith("upstream_http_error")
    assert "secret-value" not in record["failure_reason"]


async def test_admin_generation_jobs_list_is_account_admin_protected(async_client: AsyncClient):
    user_id = await create_audit_user()
    job = await job_store.create(
        CreateJobRequest(character_session_id="admin-global-jobs", free_text="global"),
        provider="fixture",
        user_id=user_id,
    )

    assert (await async_client.get("/api/admin/generation-jobs")).status_code == 401

    await login_audit_admin(async_client)

    response = await async_client.get("/api/admin/generation-jobs")

    assert response.status_code == 200
    assert response.json()[0]["id"] == job.id


async def test_audit_preserves_failed_jobs_with_complete_outputs(async_client: AsyncClient):
    user_id = await create_audit_user()
    job = await job_store.create(
        CreateJobRequest(
            character_session_id="session-complete-output",
            free_text="",
            reference_keys=["front:references/upload-a/front.webp"],
            generation_mode="front_design",
        ),
        provider="codex_bridge",
        user_id=user_id,
    )
    await job_store.set_outputs(
        job.id,
        [StoredOutput(index=1, object_key="outputs/candidate-1.webp", image_url="/api/generated/job/candidate-1.webp")],
    )
    await job_store.update(job.id, status="failed", progress=85, phase_label="failed after output")

    await login_audit_admin(async_client)

    jobs_response = await async_client.get("/api/audit/generation-jobs")
    summary_response = await async_client.get("/api/audit/summary")

    assert jobs_response.status_code == 200
    assert summary_response.status_code == 200
    assert jobs_response.json()["items"][0]["status"] == "failed"
    summary = summary_response.json()
    assert summary["job_counts"]["failed"] == 1
    assert "succeeded" not in summary["job_counts"]
    assert summary["success_rate"] == 0
    assert summary["failure_rate"] == 1


async def test_audit_generation_jobs_supports_cursor_pagination(async_client: AsyncClient):
    user_id = await create_audit_user()
    created_jobs = []
    for index in range(3):
        job = await job_store.create(
            CreateJobRequest(character_session_id=f"session-{index}", free_text=f"job-{index}"),
            provider="codex",
            user_id=user_id,
        )
        await job_store.update(job.id, status="succeeded", progress=100, phase_label="done")
        created_jobs.append(job)

    await login_audit_admin(async_client)

    first_page = await async_client.get("/api/audit/generation-jobs", params={"limit": 2})
    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert len(first_payload["items"]) == 2
    assert first_payload["has_more"] is True
    assert first_payload["next_cursor"]

    second_page = await async_client.get(
        "/api/audit/generation-jobs",
        params={"limit": 2, "cursor": first_payload["next_cursor"]},
    )
    assert second_page.status_code == 200
    second_payload = second_page.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["has_more"] is False

    returned_ids = [item["id"] for item in first_payload["items"]] + [item["id"] for item in second_payload["items"]]
    assert len(set(returned_ids)) == 3


async def test_audit_summary_requires_admin_session(async_client: AsyncClient):
    response = await async_client.get("/api/audit/summary")

    assert response.status_code == 401


async def test_shared_audit_password_routes_are_removed(async_client: AsyncClient):
    login_response = await async_client.post("/api/audit/login", json={"password": "wrong"})
    session_response = await async_client.get("/api/audit/session")
    logout_response = await async_client.post("/api/audit/logout")

    assert login_response.status_code == 410
    assert login_response.json()["detail"] == "admin_password_login_removed"
    assert session_response.status_code == 410
    assert session_response.json()["detail"] == "admin_password_login_removed"
    assert logout_response.status_code == 410
    assert logout_response.json()["detail"] == "admin_password_login_removed"


async def test_album_items_require_login(async_client: AsyncClient):
    list_response = await async_client.get("/api/album/items")
    save_response = await async_client.post(
        "/api/album/items",
        json={"image_url": "/api/static/fixtures/kigurumi-candidate-1.webp"},
    )
    upload_response = await async_client.post(
        "/api/album/items/file",
        files={"file": ("edited.png", b"not-an-image", "image/png")},
    )

    assert list_response.status_code == 401
    assert save_response.status_code == 401
    assert upload_response.status_code == 401


async def test_album_items_can_be_saved_and_listed_newest_first(async_client: AsyncClient):
    await register_album_user(async_client)

    first_response = await async_client.post(
        "/api/album/items",
        json={
            "image_url": "/api/static/fixtures/kigurumi-candidate-1.webp",
            "recipe": {"face": {"scale": 1.1}},
            "metadata": {"candidate_index": 1},
        },
    )
    second_response = await async_client.post(
        "/api/album/items",
        json={
            "image_url": "/api/static/fixtures/kigurumi-candidate-2.webp",
            "metadata": {"candidate_index": 2},
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    first = first_response.json()
    second = second_response.json()
    assert first["id"] != second["id"]
    assert first["created_at"]
    assert first["recipe"] == {"face": {"scale": 1.1}}

    list_response = await async_client.get("/api/album/items")

    assert list_response.status_code == 200
    items = list_response.json()
    assert [item["id"] for item in items] == [second["id"], first["id"]]
    assert items[0]["image_url"] == "/api/static/fixtures/kigurumi-candidate-2.webp"


async def test_album_items_are_scoped_to_current_user(async_client: AsyncClient):
    owner = await register_album_user(async_client, f"album-owner-{uuid.uuid4().hex}@example.com")
    owner_response = await async_client.post(
        "/api/album/items",
        json={"image_url": "/api/static/fixtures/kigurumi-candidate-1.webp"},
    )
    assert owner_response.status_code == 200
    owner_item = owner_response.json()

    await async_client.post("/api/auth/logout")
    await register_album_user(async_client, f"album-other-{uuid.uuid4().hex}@example.com")
    other_response = await async_client.post(
        "/api/album/items",
        json={"image_url": "/api/static/fixtures/kigurumi-candidate-2.webp"},
    )
    assert other_response.status_code == 200
    other_item = other_response.json()

    other_list_response = await async_client.get("/api/album/items")
    assert other_list_response.status_code == 200
    assert [item["id"] for item in other_list_response.json()] == [other_item["id"]]

    await async_client.post("/api/auth/logout")
    owner_login_response = await async_client.post(
        "/api/auth/login/password",
        json={"email": owner["email"], "password": "UserPassphrase7!"},
    )
    assert owner_login_response.status_code == 200
    owner_list_response = await async_client.get("/api/album/items")
    assert owner_list_response.status_code == 200
    assert [item["id"] for item in owner_list_response.json()] == [owner_item["id"]]


async def test_album_item_can_save_uploaded_edited_image_file(async_client: AsyncClient):
    await register_album_user(async_client)

    image_buffer = io.BytesIO()
    Image.new("RGB", (320, 240), "white").save(image_buffer, format="PNG")
    original_image = image_buffer.getvalue()

    response = await async_client.post(
        "/api/album/items/file",
        data={
            "recipe": '{"face":{"faceWidth":-2.4}}',
            "metadata": '{"source":"editor"}',
        },
        files={"file": ("edited.png", original_image, "image/png")},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["image_url"].startswith("/api/generated/album/")
    assert item["image_url"].endswith("/edited.png")
    assert item["recipe"] == {"face": {"faceWidth": -2.4}}
    assert item["metadata"] == {"source": "editor"}

    file_response = await async_client.get(item["image_url"])
    assert file_response.status_code == 200
    assert file_response.headers["content-type"].startswith("image/png")

    await async_client.post("/api/auth/logout")
    anonymous_file_response = await async_client.get(item["image_url"])
    assert anonymous_file_response.status_code == 404

    await register_album_user(async_client)
    other_file_response = await async_client.get(item["image_url"])
    assert other_file_response.status_code == 404

    await async_client.post("/api/auth/logout")
    await login_audit_admin(async_client)
    admin_file_response = await async_client.get(item["image_url"])
    assert admin_file_response.status_code == 200

    stored_path = (
        resolve_repo_path(get_settings().codex_output_dir)
        / "album"
        / item["id"]
        / "edited.png"
    )
    assert stored_path.is_file()
    assert stored_path.read_bytes() != original_image
    with Image.open(stored_path) as stored_image:
        stored_image.load()
        embedded_payload = json.loads(stored_image.info["kigcraft.edit-recipe"])

    with Image.open(stored_path) as stored_image:
        stored_image.verify()
    assert embedded_payload == {
        "recipe": {"face": {"faceWidth": -2.4}},
        "schema": "kigcraft.edit-recipe",
        "version": 1,
    }


async def test_generated_album_image_access_uses_path_item_owner(
    async_client: AsyncClient,
):
    owner = await register_album_user(
        async_client, f"album-owner-{uuid.uuid4().hex}@example.com"
    )

    image_buffer = io.BytesIO()
    Image.new("RGB", (320, 240), "white").save(image_buffer, format="PNG")
    upload_response = await async_client.post(
        "/api/album/items/file",
        files={"file": ("edited.png", image_buffer.getvalue(), "image/png")},
    )
    assert upload_response.status_code == 200
    owner_item = upload_response.json()
    generated_album_url = owner_item["image_url"]

    owner_file_response = await async_client.get(generated_album_url)
    assert owner_file_response.status_code == 200

    await async_client.post("/api/auth/logout")
    other = await register_album_user(
        async_client, f"album-other-{uuid.uuid4().hex}@example.com"
    )
    claim_response = await async_client.post(
        "/api/album/items",
        json={"image_url": generated_album_url},
    )
    assert claim_response.status_code == 404

    async with get_sessionmaker()() as session:
        stored_owner_item = await session.get(AlbumItem, owner_item["id"])
        assert stored_owner_item is not None
        stored_owner_item.image_url = "/api/static/fixtures/kigurumi-candidate-1.webp"
        session.add(
            AlbumItem(
                id=str(uuid.uuid4()),
                user_id=other["id"],
                image_url=generated_album_url,
                created_at=utc_now(),
            )
        )
        await session.commit()

    other_file_response = await async_client.get(generated_album_url)
    assert other_file_response.status_code == 404

    await async_client.post("/api/auth/logout")
    owner_login_response = await async_client.post(
        "/api/auth/login/password",
        json={"email": owner["email"], "password": "UserPassphrase7!"},
    )
    assert owner_login_response.status_code == 200
    owner_file_response = await async_client.get(generated_album_url)
    assert owner_file_response.status_code == 200

    await async_client.post("/api/auth/logout")
    await login_audit_admin(async_client)
    admin_file_response = await async_client.get(generated_album_url)
    assert admin_file_response.status_code == 200

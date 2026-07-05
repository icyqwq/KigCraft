import io

from httpx import AsyncClient
from PIL import Image

from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.job_store import StoredOutput, job_store
from app.generation.schemas import CreateJobRequest
from app.generation.usage import TokenUsage


async def test_audit_summary_includes_quota_and_job_counts(async_client: AsyncClient):
    queued = job_store.create(
        CreateJobRequest(character_session_id="user-a", free_text="queued"),
        provider="fixture",
    )
    succeeded = job_store.create(
        CreateJobRequest(character_session_id="user-b", free_text="done"),
        provider="fixture",
    )
    job_store.update(
        succeeded.id,
        status="succeeded",
        progress=100,
        phase_label="done",
    )
    failed = job_store.create(
        CreateJobRequest(character_session_id="user-c", free_text="failed"),
        provider="fixture",
    )
    job_store.update(
        failed.id,
        status="failed",
        progress=100,
        phase_label="failed",
    )

    login_response = await async_client.post("/api/audit/login", json={"password": "change-me-admin-audit-password"})
    assert login_response.status_code == 200

    response = await async_client.get("/api/audit/summary")

    assert response.status_code == 200
    summary = response.json()
    assert summary["total_users"] == 3
    assert summary["active_users"] == 1
    assert summary["job_counts"]["queued"] == 1
    assert summary["job_counts"]["succeeded"] == 1
    assert summary["job_counts"]["failed"] == 1
    assert summary["queue_length"] == 1
    assert summary["total_calls"] == 3
    assert summary["success_rate"] == 0.3333
    assert summary["failure_rate"] == 0.3333
    assert summary["parallel_slots_used"] == 1
    assert summary["quota_policy"] == {
        "window_hours": 5,
        "normal_window_limit": 8,
        "premium_unlimited": True,
        "parallel_generation_limit": 8,
    }
    assert queued.id


async def test_quota_policy_can_be_updated(async_client: AsyncClient):
    login_response = await async_client.post("/api/audit/login", json={"password": "change-me-admin-audit-password"})
    assert login_response.status_code == 200

    response = await async_client.patch(
        "/api/audit/quota-policy",
        json={
            "window_hours": 12,
            "normal_window_limit": 20,
            "premium_unlimited": False,
            "parallel_generation_limit": 3,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "window_hours": 12,
        "normal_window_limit": 20,
        "premium_unlimited": False,
        "parallel_generation_limit": 3,
    }

    summary_response = await async_client.get("/api/audit/summary")
    assert summary_response.json()["quota_policy"]["parallel_generation_limit"] == 3


async def test_audit_summary_includes_token_usage_totals(async_client: AsyncClient):
    first = job_store.create(
        CreateJobRequest(character_session_id="usage-a", free_text="usage-a"),
        provider="codex",
    )
    second = job_store.create(
        CreateJobRequest(character_session_id="usage-b", free_text="usage-b"),
        provider="codex",
    )
    job_store.set_outputs(
        first.id,
        [
            StoredOutput(index=1, object_key="outputs/a-1.webp", image_url="/api/generated/a-1.webp"),
            StoredOutput(index=2, object_key="outputs/a-2.webp", image_url="/api/generated/a-2.webp"),
        ],
    )
    job_store.set_outputs(
        second.id,
        [StoredOutput(index=1, object_key="outputs/b-1.webp", image_url="/api/generated/b-1.webp")],
    )
    job_store.record_token_usage(
        first.id,
        TokenUsage(
            input_tokens=120,
            cached_input_tokens=40,
            output_tokens=30,
            reasoning_output_tokens=8,
            total_tokens=150,
        ),
    )
    job_store.record_token_usage(
        second.id,
        TokenUsage(
            input_tokens=80,
            cached_input_tokens=10,
            output_tokens=20,
            reasoning_output_tokens=4,
            total_tokens=100,
        ),
    )

    login_response = await async_client.post("/api/audit/login", json={"password": "change-me-admin-audit-password"})
    assert login_response.status_code == 200

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
    reference_root = resolve_repo_path(get_settings().reference_upload_dir)
    reference_file = reference_root / "upload-a" / "front.webp"
    reference_file.parent.mkdir(parents=True, exist_ok=True)
    reference_file.write_bytes(b"reference-image")
    job = job_store.create(
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
    )
    job_store.update(job.id, status="succeeded", progress=100, phase_label="done")
    job_store.set_outputs(
        job.id,
        [StoredOutput(index=1, object_key="outputs/candidate-1.webp", image_url="/api/generated/job/candidate-1.webp")],
    )
    job_store.record_token_usage(job.id, TokenUsage(input_tokens=12, output_tokens=8, total_tokens=20))

    assert (await async_client.get("/api/audit/generation-jobs")).status_code == 401
    login_response = await async_client.post("/api/audit/login", json={"password": "change-me-admin-audit-password"})
    assert login_response.status_code == 200

    response = await async_client.get("/api/audit/generation-jobs")

    assert response.status_code == 200
    records = response.json()
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


async def test_audit_preserves_failed_jobs_with_complete_outputs(async_client: AsyncClient):
    job = job_store.create(
        CreateJobRequest(
            character_session_id="session-complete-output",
            free_text="",
            reference_keys=["front:references/upload-a/front.webp"],
            generation_mode="front_design",
        ),
        provider="codex_bridge",
    )
    job_store.set_outputs(
        job.id,
        [StoredOutput(index=1, object_key="outputs/candidate-1.webp", image_url="/api/generated/job/candidate-1.webp")],
    )
    job_store.update(job.id, status="failed", progress=85, phase_label="failed after output")

    login_response = await async_client.post("/api/audit/login", json={"password": "change-me-admin-audit-password"})
    assert login_response.status_code == 200

    jobs_response = await async_client.get("/api/audit/generation-jobs")
    summary_response = await async_client.get("/api/audit/summary")

    assert jobs_response.status_code == 200
    assert summary_response.status_code == 200
    assert jobs_response.json()[0]["status"] == "failed"
    summary = summary_response.json()
    assert summary["job_counts"]["failed"] == 1
    assert "succeeded" not in summary["job_counts"]
    assert summary["success_rate"] == 0
    assert summary["failure_rate"] == 1


async def test_audit_summary_requires_admin_session(async_client: AsyncClient):
    response = await async_client.get("/api/audit/summary")

    assert response.status_code == 401


async def test_audit_login_rejects_wrong_password_and_limits_ip(async_client: AsyncClient):
    for attempt in range(5):
        response = await async_client.post("/api/audit/login", json={"password": "wrong"})
        assert response.status_code == (429 if attempt == 4 else 401)

    response = await async_client.post("/api/audit/login", json={"password": "change-me-admin-audit-password"})

    assert response.status_code == 429


async def test_album_items_can_be_saved_and_listed_newest_first(async_client: AsyncClient):
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


async def test_album_item_can_save_uploaded_edited_image_file(async_client: AsyncClient):
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
    assert file_response.content != original_image
    Image.open(io.BytesIO(file_response.content)).verify()

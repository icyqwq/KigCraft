from __future__ import annotations

import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import get_settings
from app.generation.job_store import job_store
from app.generation.router import clear_jobs
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


@pytest.fixture(autouse=True)
def clean_jobs():
    get_settings.cache_clear()
    clear_jobs()
    yield
    clear_jobs()
    get_settings.cache_clear()


def make_client(monkeypatch, **env_overrides: str) -> TestClient:
    monkeypatch.setenv("GENERATION_PROVIDER", "codex")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "true")
    monkeypatch.setenv("CODEX_USAGE_CHECK_ENABLED", "false")
    for key, value in env_overrides.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    return TestClient(create_app())


def test_local_revision_rejects_size_mismatch(monkeypatch):
    client = make_client(monkeypatch)

    response = client.post(
        "/api/generation/local-revision-jobs",
        data={"metadata": json.dumps({"edit_note": "fix mouth", "selected_reference_keys": []})},
        files={
            "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
            "mask_image": ("mask.png", mask_bytes((5, 4)), "image/png"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "local_revision_size_mismatch"


def test_local_revision_rejects_empty_mask(monkeypatch):
    client = make_client(monkeypatch)

    response = client.post(
        "/api/generation/local-revision-jobs",
        data={"metadata": json.dumps({"edit_note": "fix mouth", "selected_reference_keys": []})},
        files={
            "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
            "mask_image": ("mask.png", mask_bytes((4, 4), filled=False), "image/png"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "local_revision_mask_empty"


def test_local_revision_creates_front_local_revision_job(monkeypatch, tmp_path):
    client = make_client(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    response = client.post(
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


def test_local_revision_uses_safe_session_path(monkeypatch, tmp_path):
    client = make_client(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    response = client.post(
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


def test_local_revision_rejects_malformed_metadata(monkeypatch):
    client = make_client(monkeypatch)

    response = client.post(
        "/api/generation/local-revision-jobs",
        data={"metadata": json.dumps({"edit_note": "fix mouth", "selected_reference_keys": "bad"})},
        files={
            "base_image": ("base.png", image_bytes((4, 4)), "image/png"),
            "mask_image": ("mask.png", mask_bytes((4, 4)), "image/png"),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "local_revision_metadata_invalid"


def test_local_revision_rejects_selected_reference_keys(monkeypatch):
    client = make_client(monkeypatch)

    response = client.post(
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
    assert job_store.list() == []


def test_local_revision_accepts_uploaded_references(monkeypatch, tmp_path):
    client = make_client(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    response = client.post(
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
    job = job_store.get(response.json()["id"])
    assert job is not None
    uploaded_key = f"supplemental:references/local-revisions/{job.character_session_id}/{job.id}/reference-1.png"
    assert uploaded_key in job.prompt_payload["reference_keys"]
    assert {
        "reference_key": uploaded_key,
        "description": "mouth reference",
    } in job.prompt_payload["reference_descriptions"]
    assert (tmp_path / "refs" / "local-revisions" / job.character_session_id / job.id / "reference-1.png").is_file()


def test_local_revision_rejects_more_than_one_uploaded_reference(monkeypatch, tmp_path):
    client = make_client(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    response = client.post(
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
    assert job_store.list() == []


def test_local_revision_rejects_invalid_uploaded_reference_without_creating_job(monkeypatch, tmp_path):
    client = make_client(
        monkeypatch,
        REFERENCE_UPLOAD_DIR=str(tmp_path / "refs"),
        CODEX_WORKSPACE_DIR=str(tmp_path / "codex"),
        CODEX_OUTPUT_DIR=str(tmp_path / "generated"),
        GENERATION_AUDIT_DB_PATH=str(tmp_path / "audit.sqlite3"),
    )

    response = client.post(
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
    assert job_store.list() == []

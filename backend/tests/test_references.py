from pathlib import Path
import shutil
import uuid

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def references_client(monkeypatch):
    from app.core.config import get_settings
    from app.main import create_app

    reference_root = (
        Path(__file__).resolve().parents[2]
        / "runtime"
        / "test-references"
        / str(uuid.uuid4())
    )
    monkeypatch.setenv("REFERENCE_UPLOAD_DIR", str(reference_root))
    get_settings.cache_clear()
    app = create_app()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, reference_root
    finally:
        shutil.rmtree(reference_root, ignore_errors=True)


async def test_reference_upload_stores_image_and_returns_object_key(references_client):
    client, reference_root = references_client

    response = await client.post(
        "/api/references",
        data={"kind": "front"},
        files={"file": ("front.webp", b"webp-image", "image/webp")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["file_name"] == "front.webp"
    assert payload["object_key"].startswith("references/")
    assert payload["object_key"].endswith("/front.webp")

    stored_path = reference_root / Path(payload["object_key"]).relative_to("references")
    assert stored_path.read_bytes() == b"webp-image"


async def test_reference_upload_rejects_non_image_files(references_client):
    client, _ = references_client

    response = await client.post(
        "/api/references",
        data={"kind": "front"},
        files={"file": ("prompt.txt", b"not image", "text/plain")},
    )

    assert response.status_code == 400

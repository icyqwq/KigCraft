import uuid
from pathlib import Path

from httpx import AsyncClient

from app.core.config import get_settings


async def register_user(async_client: AsyncClient) -> None:
    email = f"reference-{uuid.uuid4().hex}@example.com"
    await async_client.post(
        "/api/auth/send-code",
        json={"email": email, "purpose": "register"},
    )
    response = await async_client.post(
        "/api/auth/register",
        json={
            "terms_accepted": True,
            "terms_version": get_settings().current_terms_version,
            "email": email,
            "code": "123456",
            "password": "UserPassphrase7!",
        },
    )
    assert response.status_code == 200


async def test_reference_upload_stores_image_and_returns_object_key(
    async_client: AsyncClient,
):
    await register_user(async_client)

    response = await async_client.post(
        "/api/references",
        data={"kind": "front"},
        files={"file": ("front.webp", b"webp-image", "image/webp")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["file_name"] == "front.webp"
    assert payload["object_key"].startswith("references/")
    assert payload["object_key"].endswith("/front.webp")

    stored_path = Path(get_settings().reference_upload_dir) / Path(
        payload["object_key"]
    ).relative_to("references")
    assert stored_path.read_bytes() == b"webp-image"


async def test_reference_upload_rejects_non_image_files(async_client: AsyncClient):
    await register_user(async_client)

    response = await async_client.post(
        "/api/references",
        data={"kind": "front"},
        files={"file": ("prompt.txt", b"not image", "text/plain")},
    )

    assert response.status_code == 400

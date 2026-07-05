import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def test_app(monkeypatch: pytest.MonkeyPatch, tmp_path) -> FastAPI:
    from app.core.config import get_settings
    from app.album.router import clear_album_items
    from app.audit.router import clear_audit_state
    from app.generation.events import clear_events
    from app.generation.router import clear_jobs
    from app.main import create_app
    from app.references.router import clear_uploaded_references

    monkeypatch.setenv("GENERATION_PROVIDER", "fixture")
    monkeypatch.setenv("ALLOW_FIXTURE_GENERATION", "true")
    monkeypatch.setenv("ADMIN_AUDIT_ENABLED", "true")
    monkeypatch.setenv("REFERENCE_UPLOAD_DIR", str(tmp_path / "references"))
    monkeypatch.setenv("CODEX_WORKSPACE_DIR", str(tmp_path / "codex"))
    monkeypatch.setenv("CODEX_OUTPUT_DIR", str(tmp_path / "generated"))
    monkeypatch.setenv("MOCK_OUTPUT_DIR", str(tmp_path / "mock-outputs"))
    monkeypatch.setenv("GENERATION_AUDIT_DB_PATH", str(tmp_path / "generation_audit.sqlite3"))
    get_settings.cache_clear()
    clear_audit_state()
    clear_album_items()
    clear_jobs()
    clear_events()
    clear_uploaded_references()
    return create_app()


@pytest.fixture
async def async_client(test_app: FastAPI):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield client

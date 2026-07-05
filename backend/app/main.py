from pathlib import Path, PurePosixPath

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.album.router import router as album_router
from app.audit.router import router as audit_router
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.router import router as generation_router
from app.prompts.router import router as prompts_router
from app.references.router import router as references_router


IMAGE_MEDIA_TYPES = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Kig Preview API", version="0.1.0")
    cors_allowed_origins = _csv_values(settings.cors_allowed_origins)
    if cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization"],
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    fixture_dir = Path(settings.fixture_dir)
    if not fixture_dir.is_absolute():
        fixture_dir = Path(__file__).resolve().parent.parent / fixture_dir
    fixture_static_kwargs = {"directory": str(fixture_dir), "check_dir": False}
    app.mount(
        "/api/static/fixtures",
        StaticFiles(**fixture_static_kwargs),
        name="api_fixture_static",
    )
    app.mount(
        "/static/fixtures",
        StaticFiles(**fixture_static_kwargs),
        name="fixture_static",
    )
    generated_prefixes = _generated_public_prefixes(settings.generated_public_prefix)

    async def generated_asset(asset_path: str) -> FileResponse:
        file_path = _resolve_generated_asset(settings.codex_output_dir, asset_path)
        return FileResponse(file_path, media_type=_image_media_type(file_path))

    for generated_prefix in generated_prefixes:
        app.add_api_route(
            f"{generated_prefix}/{{asset_path:path}}",
            generated_asset,
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )

    app.include_router(prompts_router)
    app.include_router(generation_router)
    app.include_router(audit_router)
    app.include_router(album_router)
    app.include_router(references_router)
    app.include_router(prompts_router, prefix="/api")
    app.include_router(generation_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    app.include_router(album_router, prefix="/api")
    app.include_router(references_router, prefix="/api")

    return app

def _resolve_generated_asset(output_dir: str, asset_path: str) -> Path:
    root = resolve_repo_path(output_dir).resolve()
    normalized = asset_path.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    if posix_path.is_absolute() or any(part == ".." for part in posix_path.parts):
        raise HTTPException(status_code=404, detail="Generated asset not found")
    candidate = (root / Path(*posix_path.parts)).resolve()
    if candidate.suffix.lower() not in IMAGE_MEDIA_TYPES:
        raise HTTPException(status_code=404, detail="Generated asset not found")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Generated asset not found") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Generated asset not found")
    return candidate


def _image_media_type(file_path: Path) -> str:
    return IMAGE_MEDIA_TYPES.get(file_path.suffix.lower(), "application/octet-stream")


def _generated_public_prefixes(public_prefix: str) -> list[str]:
    normalized = f"/{public_prefix.strip('/')}"
    prefixes = [normalized]
    if normalized.startswith("/api/"):
        prefixes.append(normalized.removeprefix("/api"))
    return prefixes


def _csv_values(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


app = create_app()


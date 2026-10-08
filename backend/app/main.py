from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from app.admin.access_requests import observed_response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import UserRole
from app.auth.sessions import _hash_token
from app.album.router import router as album_router
from app.auth.router import router as auth_router
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.core.time import utc_now
from app.db.models import AlbumItem, AuthSession, GenerationJob, GenerationOutput, User
from app.db.session import get_session
from app.prompts.router import router as prompts_router
from app.references.router import router as references_router
from app.design.router import router as v2_router
from app.design.service import ApplicationError


IMAGE_MEDIA_TYPES = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # V1 is retired: schema changes belong to the explicit V2 migration job, never to application startup.
    from app.db.v2_schema import require_v2_schema
    await require_v2_schema()
    from app.admin.access_collector import collector_lifespan
    sampler = None
    settings = get_settings()
    if settings.app_env == "production" and settings.codex_usage_sample_seconds > 0:
        import asyncio
        from app.generation.codex_usage_history import sample_forever
        sampler = asyncio.create_task(sample_forever(settings, settings.codex_usage_sample_seconds))
    try:
        async with collector_lifespan(_app):
            yield
    finally:
        if sampler is not None:
            sampler.cancel()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Kig Preview API", version="0.1.0", lifespan=_lifespan)

    @app.exception_handler(StarletteHTTPException)
    async def observed_http_error(request: Request, exc: StarletteHTTPException):
        return observed_response(request, await http_exception_handler(request, exc))

    @app.exception_handler(RequestValidationError)
    async def observed_validation_error(request: Request, exc: RequestValidationError):
        return observed_response(request, await request_validation_exception_handler(request, exc))

    from app.generation.retired import RetiredProviderError

    @app.exception_handler(RetiredProviderError)
    async def retired_provider(_request: Request, _exc: RetiredProviderError):
        return JSONResponse(status_code=503, content={"detail": "legacy_provider_retired"})

    from app.assets.repository import StorageUnavailable

    @app.exception_handler(StorageUnavailable)
    async def storage_unavailable(_request: Request, _exc: StorageUnavailable):
        return JSONResponse(status_code=503, content={"code": "asset_storage_unavailable", "retry_action": "retry"})

    @app.exception_handler(ApplicationError)
    async def v2_error(_request: Request, exc: ApplicationError):
        status = 404 if exc.code == "not_found" else 409 if "conflict" in exc.code or exc.code in {"design_busy","design_list_changed","design_delete_unconfirmed","question_expired","design_limit_reached"} else 400
        if exc.code in {"new_operations_disabled", "runtime_not_configured", "wallet_not_migrated", "generation_paused", "runtime_quarantined", "asset_storage_not_configured", "asset_storage_configuration_mismatch"}:
            status = 503
        if exc.code in {"local_test_account_required", "rollout_not_enabled"}:
            status = 403
        if exc.code == "submission_rate_limited":
            return observed_response(_request, JSONResponse(status_code=429, headers={"Retry-After": str(exc.retry_after)},
                content={"code": exc.code, "message": exc.code, "retry_action": "wait",
                         "retry_after_seconds": exc.retry_after, "operation_id": None, "quota_outcome": "unchanged"}))
        if exc.code == "quota_insufficient":
            status = 429
        return observed_response(_request, JSONResponse(status_code=status, content={"code": exc.code, "message": exc.code,
                            "retry_action": "refresh", "operation_id": None, "quota_outcome": "unchanged"}))

    from app.admin.v2_router import router as v2_admin_router
    app.include_router(v2_admin_router)
    app.include_router(v2_admin_router, prefix="/api")
    app.include_router(v2_router)
    app.include_router(v2_router, prefix="/api")
    cors_allowed_origins = _csv_values(settings.cors_allowed_origins)
    if cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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

    async def generated_asset(
        asset_path: str,
        current_user: User = Depends(_get_generated_asset_user),
        session: AsyncSession = Depends(get_session),
    ) -> FileResponse:
        object_key = _normalized_generated_asset_path(asset_path)
        file_path = _resolve_generated_asset(settings.codex_output_dir, object_key)
        await _ensure_generated_asset_access(
            object_key=object_key,
            image_urls=_generated_asset_image_urls(object_key, settings.generated_public_prefix),
            current_user=current_user,
            session=session,
        )
        return FileResponse(file_path, media_type=_image_media_type(file_path))

    for generated_prefix in generated_prefixes:
        app.add_api_route(
            f"{generated_prefix}/{{asset_path:path}}",
            generated_asset,
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )

    shared_routers = [prompts_router, album_router, auth_router, references_router]
    # No V1 route is mounted (generation, history or otherwise): nothing can start or read a V1 task.
    # The operator panel reads V2 data (admin/v2_jobs.py) and is the only admin surface.
    from app.admin.router import router as admin_router
    from app.audit.router import router as audit_router
    from app.codex_profiles.router import router as codex_profiles_router
    selected_routers = [*shared_routers, admin_router, audit_router, codex_profiles_router]
    for router in selected_routers:
        app.include_router(router)
        app.include_router(router, prefix="/api")

    return app


async def _get_generated_asset_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        request.state.access_auth_failure = "unauthenticated"
        raise HTTPException(status_code=404, detail="Generated asset not found")
    result = await session.execute(
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(AuthSession.session_token_hash == _hash_token(token))
        .where(AuthSession.revoked_at.is_(None))
        .where(AuthSession.expires_at >= utc_now())
    )
    user = result.scalar_one_or_none()
    if user is None or user.is_banned:
        request.state.access_auth_failure = "unauthenticated" if user is None else "forbidden"
        raise HTTPException(status_code=404, detail="Generated asset not found")
    request.state.v2_actor_id = user.id
    return user


async def _ensure_generated_asset_access(
    *,
    object_key: str,
    image_urls: list[str],
    current_user: User,
    session: AsyncSession,
) -> None:
    result = await session.execute(
        select(GenerationJob.user_id)
        .join(GenerationOutput, GenerationOutput.job_id == GenerationJob.id)
        .where(
            or_(
                GenerationOutput.object_key == object_key,
                GenerationOutput.image_url.in_(image_urls),
            )
        )
        .limit(1)
    )
    owner_id = result.scalar_one_or_none()
    if owner_id is None:
        album_item_id = _generated_album_item_id(object_key)
        if album_item_id is not None:
            album_result = await session.execute(
                select(AlbumItem.user_id)
                .where(AlbumItem.id == album_item_id)
                .limit(1)
            )
            owner_id = album_result.scalar_one_or_none()
        else:
            album_result = await session.execute(
                select(AlbumItem.user_id)
                .where(AlbumItem.image_url.in_(image_urls))
                .limit(1)
            )
            owner_id = album_result.scalar_one_or_none()
    if owner_id is None:
        raise HTTPException(status_code=404, detail="Generated asset not found")
    if current_user.role != UserRole.admin and owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Generated asset not found")


def _resolve_generated_asset(output_dir: str, asset_path: str) -> Path:
    root = resolve_repo_path(output_dir).resolve()
    normalized = _normalized_generated_asset_path(asset_path)
    posix_path = PurePosixPath(normalized)
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


def _normalized_generated_asset_path(asset_path: str) -> str:
    normalized = asset_path.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    if (
        posix_path.is_absolute()
        or any(part == ".." for part in posix_path.parts)
        or not posix_path.parts
    ):
        raise HTTPException(status_code=404, detail="Generated asset not found")
    return "/".join(posix_path.parts)


def _image_media_type(file_path: Path) -> str:
    return IMAGE_MEDIA_TYPES.get(file_path.suffix.lower(), "application/octet-stream")


def _generated_asset_image_urls(asset_path: str, public_prefix: str) -> list[str]:
    normalized_asset = _normalized_generated_asset_path(asset_path)
    image_urls = [f"/api/generated/{normalized_asset}"]
    configured_url = f"/{public_prefix.strip('/')}/{normalized_asset}"
    if configured_url not in image_urls:
        image_urls.append(configured_url)
    return image_urls


def _generated_album_item_id(asset_path: str) -> str | None:
    normalized_asset = _normalized_generated_asset_path(asset_path)
    parts = PurePosixPath(normalized_asset).parts
    if len(parts) < 3 or parts[0] != "album" or not parts[1]:
        return None
    return parts[1]


def _generated_public_prefixes(public_prefix: str) -> list[str]:
    normalized = f"/{public_prefix.strip('/')}"
    prefixes = [normalized]
    if normalized.startswith("/api/"):
        prefixes.append(normalized.removeprefix("/api"))
    return prefixes


def _csv_values(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


app = create_app()


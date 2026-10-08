import asyncio
from dataclasses import dataclass
from io import BytesIO
import json
import logging
import re
import shutil
import time
from collections import defaultdict, deque
from app.core.client_ip import resolve_client_ip
from pathlib import Path
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from PIL import Image
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import UserRole
from app.auth.sessions import get_current_user
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.db.models import ReferenceAsset, User
from app.db.session import get_session
from app.generation.codex_usage import ensure_codex_usage_allows_generation
from app.generation.detail_analysis import (
    DetailAnalysisProviderRequest,
    DetailAnalysisProviderResult,
    DetailCropOut,
    DetailFeature,
    ensure_user_requirement_feature,
    filter_head_detail_analysis_result,
    persist_detail_crops,
    reference_key_to_relative_path,
)
from app.generation.job_store import StoredEvent, StoredJob, StoredOutput, job_store
from app.generation.local_edit import (
    LocalEditValidationError,
    save_local_edit_inputs,
    validate_local_edit_images,
)
from app.generation.provider import (
    ImageGenerationProvider,
    ReferenceRejectedError,
    get_provider_for_preset,
    get_generation_provider,
    request_generation_cancel,
)
from app.generation.presets import ActiveProviderPresetStore, resolve_preset, snapshot_for_preset
from app.generation.retired import reject_retired
from app.generation.quota import consume_generation_quota
from app.generation.queue import generation_queue
from app.generation.schemas import (
    CreateJobRequest,
    DEFAULT_LOCALE,
    GenerationEventOut,
    GenerationJobOut,
    GenerationOutputOut,
    GenerationQueueStatusOut,
    ReferenceDescriptionIn,
    normalize_locale,
)
from app.prompts.router import resolve_requirement_prompt_texts
from app.prompts.safety import sanitize_user_text
from app.references.router import (
    _ensure_reference_asset_access,
    _ensure_uploaded_reference_keys_access,
    _reference_key_object_key,
    _resolve_reference_asset,
)

router = APIRouter(prefix="/generation", tags=["generation"])
logger = logging.getLogger("uvicorn.error")
LOCAL_REVISION_MAX_REFERENCE_FILES = 1
LOCAL_REVISION_MAX_REFERENCE_BYTES = 8 * 1024 * 1024
LOCAL_REVISION_MAX_REFERENCE_PIXELS = 4_000_000
LOCAL_REVISION_MAX_REFERENCE_SIDE = 3000
HIGH_LOAD_PENDING_QUEUE_THRESHOLD = 6
_create_job_attempts: dict[str, deque[float]] = defaultdict(deque)


class LegacyCreateJobRequest(BaseModel):
    project_id: str
    free_text: str = Field(default="", max_length=2000)
    chip_ids: list[str] = Field(default_factory=list)
    reference_keys: list[str] = Field(default_factory=list)


class LegacyJobOut(BaseModel):
    id: str
    project_id: str
    status: str
    progress: int
    outputs: list[dict[str, Any]] = Field(default_factory=list)


class AcceptOutputRequest(BaseModel):
    output_index: int = Field(ge=1, le=4)


class GenerationJobListOut(BaseModel):
    jobs: list[GenerationJobOut]
    total: int = 0
    limit: int = 50
    offset: int = 0


class DetailAnalysisRequest(BaseModel):
    character_session_id: str | None = None
    free_text: str = Field(default="", max_length=2000)
    locale: str = Field(default=DEFAULT_LOCALE, max_length=16)
    requirement_ids: list[str] = Field(default_factory=list)
    reference_keys: list[str] = Field(default_factory=list)
    reference_descriptions: list[ReferenceDescriptionIn] = Field(default_factory=list)


class DetailAnalysisOut(BaseModel):
    analysis_id: str
    features: list[DetailFeature] = Field(default_factory=list)
    crops: list[DetailCropOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LocalRevisionMetadata(BaseModel):
    character_session_id: str | None = None
    edit_note: str = Field(default="", max_length=2000)
    locale: str = Field(default=DEFAULT_LOCALE, max_length=16)
    selected_reference_keys: list[str] = Field(default_factory=list)
    reference_descriptions: list[ReferenceDescriptionIn] = Field(default_factory=list)
    uploaded_reference_descriptions: list[str] = Field(default_factory=list)
    recipe: dict[str, Any] = Field(default_factory=dict)


@dataclass
class LocalRevisionReferenceUpload:
    description: str
    image: Image.Image


async def clear_jobs_async() -> None:
    await generation_queue.cancel_all_and_wait()
    await job_store.clear()
    _create_job_attempts.clear()


def job_to_out(job: StoredJob) -> GenerationJobOut:
    return GenerationJobOut(
        id=job.id,
        character_session_id=job.character_session_id,
        generation_mode=job.generation_mode,
        expected_output_count=job.expected_output_count,
        status=job.status,
        progress=job.progress,
        queue_position=job.queue_position,
        phase_label=job.phase_label,
        provider=job.provider,
        created_at=job.created_at,
        updated_at=job.updated_at,
        accepted_output_index=job.accepted_output_index,
        token_usage=job.token_usage.to_dict() if job.token_usage else None,
        outputs=[output_to_out(output) for output in job.outputs],
    )


def output_to_out(output: StoredOutput) -> GenerationOutputOut:
    return GenerationOutputOut(
        index=output.index,
        object_key=output.object_key,
        image_url=output.image_url,
        width=output.width,
        height=output.height,
        landmarks=output.landmarks,
    )


def event_to_out(event: StoredEvent) -> GenerationEventOut:
    return GenerationEventOut(
        sequence=event.sequence,
        type=event.type,
        progress=event.progress,
        message=event.message,
        created_at=event.created_at,
        payload=event.payload,
    )


def legacy_job_to_out(job: StoredJob, project_id: str) -> LegacyJobOut:
    return LegacyJobOut(
        id=job.id,
        project_id=project_id,
        status=job.status,
        progress=job.progress,
        outputs=[
            {
                "index": output.index,
                "object_key": output.object_key,
                "width": output.width,
                "height": output.height,
            }
            for output in job.outputs
        ],
    )


async def _get_accessible_job(job_id: str, current_user: User) -> StoredJob:
    job = await job_store.get(job_id)
    if job is None or (
        current_user.role != UserRole.admin and job.user_id != current_user.id
    ):
        raise HTTPException(status_code=404, detail="Generation job not found")
    return job


@router.post("/detail-analysis", response_model=DetailAnalysisOut)
async def analyze_generation_details(
    payload: DetailAnalysisRequest,
    current_user: User = Depends(get_current_user),
    provider: ImageGenerationProvider = Depends(get_generation_provider),
    session: AsyncSession = Depends(get_session),
) -> DetailAnalysisOut:
    provider, _, _ = _provider_for_new_operation(provider)
    _ensure_real_generation_provider(provider)
    reference_keys = [key.strip() for key in payload.reference_keys if key.strip()]
    if not reference_keys:
        raise HTTPException(status_code=400, detail="At least one reference image is required")
    await _ensure_detail_analysis_reference_access(
        reference_keys,
        current_user=current_user,
        session=session,
    )

    analysis_id = str(uuid.uuid4())
    character_session_id = payload.character_session_id or str(uuid.uuid4())
    settings = get_settings()
    reference_root = resolve_repo_path(settings.reference_upload_dir)
    source_paths = _snapshot_detail_analysis_references(
        reference_keys,
        analysis_id=analysis_id,
        reference_root=reference_root,
    )
    try:
        provider_result: DetailAnalysisProviderResult = await provider.analyze_reference_details(
            DetailAnalysisProviderRequest(
                analysis_id=analysis_id,
                character_session_id=character_session_id,
                free_text=payload.free_text,
                locale=normalize_locale(payload.locale),
                requirement_texts=resolve_requirement_prompt_texts(payload.requirement_ids),
                reference_keys=reference_keys,
                reference_descriptions=[
                    {
                        "reference_key": item.reference_key.strip(),
                        "description": item.description.strip(),
                    }
                    for item in payload.reference_descriptions
                    if item.description.strip()
                ],
            )
        )
    except ReferenceRejectedError as exc:
        raise HTTPException(status_code=400, detail=exc.reason) from None
    except Exception as exc:
        logger.exception("Detail analysis provider failed analysis_id=%s", analysis_id)
        raise HTTPException(status_code=502, detail=_provider_failure_detail("detail_analysis", exc)) from None

    normalized_locale = normalize_locale(payload.locale)
    provider_result = filter_head_detail_analysis_result(provider_result)
    provider_result = ensure_user_requirement_feature(
        provider_result,
        free_text=sanitize_user_text(payload.free_text),
        locale=normalized_locale,
    )
    crops, crop_warnings = persist_detail_crops(
        analysis_id=analysis_id,
        provider_crops=provider_result.crops,
        reference_root=reference_root,
        public_prefix="/api/references",
        source_paths=source_paths,
    )
    await _record_detail_crop_assets(crops, current_user=current_user, session=session)
    return DetailAnalysisOut(
        analysis_id=analysis_id,
        features=provider_result.features,
        crops=crops,
        warnings=[*provider_result.warnings, *crop_warnings],
    )


@router.post("/jobs", response_model=GenerationJobOut)
async def create_job(
    payload: CreateJobRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    provider: ImageGenerationProvider = Depends(get_generation_provider),
    session: AsyncSession = Depends(get_session),
) -> GenerationJobOut:
    provider, provider_id, provider_snapshot = _provider_for_new_operation(provider)
    _enforce_create_job_rate_limit(request)
    _ensure_real_generation_provider(provider)
    if provider.name in {"codex", "codex_bridge"}:
        await ensure_codex_usage_allows_generation(get_settings())
    stale_object_keys = await _ensure_uploaded_reference_keys_access(
        payload.reference_keys,
        current_user=current_user,
        session=session,
    )
    payload = _drop_stale_reference_keys(payload, stale_object_keys)
    quota_state = await consume_generation_quota(session, current_user)
    try:
        job = await job_store.create(
            payload,
            provider=provider_id,
            user_id=current_user.id,
            provider_snapshot=provider_snapshot,
            quota_window_start_at=quota_state.window_start_at if not quota_state.exempt else None,
        )
        generation_queue.submit_job(job.id, provider)
        await session.commit()
    except Exception as exc:
        logger.exception("Generation job creation failed provider=%s", provider.name)
        raise HTTPException(status_code=503, detail=_provider_failure_detail("generation_create", exc)) from None
    return job_to_out(job)


@router.post("/local-revision-jobs", response_model=GenerationJobOut)
async def create_local_revision_job(
    request: Request,
    metadata: str = Form(...),
    base_image: UploadFile = File(...),
    mask_image: UploadFile = File(...),
    reference_files: list[UploadFile] = File(default=[]),
    current_user: User = Depends(get_current_user),
    provider: ImageGenerationProvider = Depends(get_generation_provider),
    session: AsyncSession = Depends(get_session),
) -> GenerationJobOut:
    provider, provider_id, provider_snapshot = _provider_for_new_operation(provider)
    _enforce_create_job_rate_limit(request)
    _ensure_real_generation_provider(provider)
    if provider.name != "codex":
        raise HTTPException(status_code=503, detail="local_revision_provider_unsupported")
    if provider.name == "codex":
        await ensure_codex_usage_allows_generation(get_settings())

    parsed = _parse_local_revision_metadata(metadata)
    if parsed.selected_reference_keys or parsed.reference_descriptions:
        raise HTTPException(status_code=400, detail="local_revision_selected_references_unsupported")

    base_bytes = await base_image.read()
    mask_bytes = await mask_image.read()
    try:
        local_info = validate_local_edit_images(base_bytes, mask_bytes)
    except LocalEditValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc
    uploaded_references = await _validate_local_revision_references(
        reference_files,
        parsed.uploaded_reference_descriptions,
    )
    quota_state = await consume_generation_quota(session, current_user)
    try:
        job = await job_store.create(
            CreateJobRequest(
                character_session_id=_safe_local_revision_segment(parsed.character_session_id)
                if parsed.character_session_id
                else None,
                free_text=parsed.edit_note,
                locale=parsed.locale,
                reference_keys=[],
                reference_descriptions=[],
                generation_mode="front_local_revision",
            ),
            provider=provider_id,
            user_id=current_user.id,
            provider_snapshot=provider_snapshot,
            quota_window_start_at=quota_state.window_start_at if not quota_state.exempt else None,
        )
    except Exception as exc:
        logger.exception("Local revision job creation failed provider=%s", provider.name)
        raise HTTPException(status_code=503, detail=_provider_failure_detail("generation_create", exc)) from None

    local_root = resolve_repo_path("runtime/local-revisions") / job.character_session_id / job.id
    try:
        local_info = save_local_edit_inputs(base_bytes, mask_bytes, local_root)
    except LocalEditValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc

    uploaded_reference_keys, uploaded_reference_descriptions = _persist_local_revision_references(
        uploaded_references,
        character_session_id=job.character_session_id,
        job_id=job.id,
    )
    patched_job = await job_store.patch_prompt_payload(
        job.id,
        {
            "reference_descriptions": [item.model_dump() for item in uploaded_reference_descriptions],
            "reference_keys": uploaded_reference_keys,
            "local_edit": {
                "base_image_path": str(local_root / "base.png"),
                "mask_image_path": str(local_root / "mask.png"),
                "edit_note": sanitize_user_text(parsed.edit_note),
                "base_width": local_info.width,
                "base_height": local_info.height,
                "feather_radius_px": 6,
            }
        },
    )
    if patched_job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")

    try:
        generation_queue.submit_job(job.id, provider)
        await session.commit()
    except Exception as exc:
        logger.exception("Local revision job enqueue failed job_id=%s provider=%s", job.id, provider.name)
        raise HTTPException(status_code=503, detail=_provider_failure_detail("generation_create", exc)) from None
    return job_to_out(patched_job)


@router.get("/queue-status", response_model=GenerationQueueStatusOut)
async def get_queue_status() -> GenerationQueueStatusOut:
    status = generation_queue.status()
    return GenerationQueueStatusOut(
        pending_count=status.pending_count,
        active_count=status.active_count,
        parallelism=status.parallelism,
        high_load=status.pending_count > HIGH_LOAD_PENDING_QUEUE_THRESHOLD,
    )


@router.get("/jobs", response_model=GenerationJobListOut)
async def list_jobs(
    current_user: User = Depends(get_current_user),
    status: str | None = Query(default=None, min_length=1, max_length=80),
    generation_mode: str | None = Query(default=None, min_length=1, max_length=80),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> GenerationJobListOut:
    jobs, total = await job_store.list_page(
        user_id=current_user.id,
        status=status,
        generation_mode=generation_mode,
        limit=limit,
        offset=offset,
    )
    return GenerationJobListOut(
        jobs=[job_to_out(job) for job in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=GenerationJobOut)
async def get_job(job_id: str, current_user: User = Depends(get_current_user)) -> GenerationJobOut:
    job = await _get_accessible_job(job_id, current_user)
    return job_to_out(job)


@router.get("/jobs/{job_id}/events", response_model=list[GenerationEventOut])
async def get_job_events(
    job_id: str, current_user: User = Depends(get_current_user)
) -> list[GenerationEventOut]:
    job = await _get_accessible_job(job_id, current_user)
    return [event_to_out(event) for event in job.events]


@router.post("/jobs/{job_id}/accept", response_model=GenerationJobOut)
async def accept_job_output(
    job_id: str,
    payload: AcceptOutputRequest,
    current_user: User = Depends(get_current_user),
) -> GenerationJobOut:
    await _get_accessible_job(job_id, current_user)

    job = await job_store.accept_output(job_id, payload.output_index)
    if job is None:
        raise HTTPException(status_code=400, detail="Candidate output is not available yet")

    asyncio.create_task(
        request_generation_cancel(
            _provider_for_existing_job_cancel(job), job.id, job.prompt_payload
        )
    )
    return job_to_out(job)


@router.post("/jobs/{job_id}/cancel", response_model=GenerationJobOut)
async def cancel_job(
    job_id: str, current_user: User = Depends(get_current_user)
) -> GenerationJobOut:
    await _get_accessible_job(job_id, current_user)

    job = await job_store.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")

    generation_queue.cancel_job(job.id)
    asyncio.create_task(
        request_generation_cancel(
            _provider_for_existing_job_cancel(job), job.id, job.prompt_payload
        )
    )
    return job_to_out(job)


@router.post("/projects/{project_id}/jobs", response_model=LegacyJobOut)
async def create_legacy_project_job(
    project_id: str,
    payload: LegacyCreateJobRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    provider: ImageGenerationProvider = Depends(get_generation_provider),
    session: AsyncSession = Depends(get_session),
) -> LegacyJobOut:
    provider, provider_id, provider_snapshot = _provider_for_new_operation(provider)
    _enforce_create_job_rate_limit(request)
    _ensure_real_generation_provider(provider)
    if provider.name in {"codex", "codex_bridge"}:
        await ensure_codex_usage_allows_generation(get_settings())
    stale_object_keys = await _ensure_uploaded_reference_keys_access(
        payload.reference_keys,
        current_user=current_user,
        session=session,
    )
    valid_reference_keys = [
        key
        for key in payload.reference_keys
        if _reference_key_object_key(key) not in stale_object_keys
    ]
    if payload.reference_keys and not valid_reference_keys:
        raise HTTPException(status_code=400, detail="At least one valid reference image is required")
    quota_state = await consume_generation_quota(session, current_user)
    try:
        job = await job_store.create(
            CreateJobRequest(
                character_session_id=project_id,
                free_text=payload.free_text,
                requirement_ids=payload.chip_ids,
                reference_keys=valid_reference_keys,
            ),
            provider=provider_id,
            user_id=current_user.id,
            provider_snapshot=provider_snapshot,
            quota_window_start_at=quota_state.window_start_at if not quota_state.exempt else None,
        )
        if provider.name != "fixture":
            generation_queue.submit_job(job.id, provider)
        await session.commit()
    except Exception as exc:
        logger.exception("Legacy generation job creation failed provider=%s project_id=%s", provider.name, project_id)
        raise HTTPException(status_code=503, detail=_provider_failure_detail("generation_create", exc)) from None

    if provider.name != "fixture":
        return legacy_job_to_out(job, project_id)

    await generation_queue.run_job(job.id, provider)
    stored_job = await job_store.get(job.id)
    if stored_job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return legacy_job_to_out(stored_job, project_id)


def _ensure_real_generation_provider(provider: ImageGenerationProvider) -> None:
    if provider.name not in {"fixture", "mock"}:
        return
    settings = get_settings()
    configured_provider = settings.generation_provider
    if settings.app_env.strip().lower() == "production":
        raise HTTPException(
            status_code=503,
            detail=f"fixture_generation_disabled_in_production: GENERATION_PROVIDER={configured_provider}",
        )
    if settings.allow_fixture_generation:
        return
    raise HTTPException(
        status_code=503,
        detail=f"real_generation_provider_not_configured: GENERATION_PROVIDER={configured_provider}",
    )


def _provider_for_new_operation(
    injected_provider: ImageGenerationProvider,
) -> tuple[ImageGenerationProvider, str, dict[str, str]]:
    """Resolve a provider once, keeping explicit fixture/mock test transports intact."""
    settings = get_settings()
    reject_retired(injected_provider.name)
    active = ActiveProviderPresetStore(Path(settings.provider_preset_path)).get()
    active_snapshot = snapshot_for_preset(resolve_preset(active.preset_id))

    if injected_provider.name in {"fixture", "mock"}:
        return injected_provider, active.preset_id, active_snapshot

    configured_provider = get_generation_provider()
    # Dependency overrides are intentionally retained for fixture/mock test runs.
    if (
        settings.generation_provider in {"fixture", "mock"}
        or injected_provider.name != configured_provider.name
    ):
        preset_id = "codex_full"
        return (
            injected_provider,
            injected_provider.name,
            snapshot_for_preset(resolve_preset(preset_id)),
        )

    return get_provider_for_preset(active_snapshot), active.preset_id, active_snapshot


def _provider_for_existing_job_cancel(job: StoredJob) -> ImageGenerationProvider | str:
    """Keep fixture/mock cancellation local, including legacy jobs without snapshots."""
    reject_retired(job.provider, job.prompt_payload)
    if job.provider in {"fixture", "mock"}:
        return job.provider
    return get_provider_for_preset(job.prompt_payload)


def _provider_failure_detail(operation: str, exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return f"{operation}_provider_failed: {message}"


def _drop_stale_reference_keys(
    payload: CreateJobRequest,
    stale_object_keys: set[str],
) -> CreateJobRequest:
    if not stale_object_keys:
        return payload

    reference_keys = [
        key
        for key in payload.reference_keys
        if _reference_key_object_key(key) not in stale_object_keys
    ]
    if not reference_keys:
        raise HTTPException(status_code=400, detail="At least one valid reference image is required")

    reference_descriptions = [
        item
        for item in payload.reference_descriptions
        if _reference_key_object_key(item.reference_key) not in stale_object_keys
    ]
    detail_lock = payload.detail_lock
    if detail_lock is not None:
        detail_lock = detail_lock.model_copy(
            update={
                "crops": [
                    crop
                    for crop in detail_lock.crops
                    if _reference_key_object_key(crop.reference_key) not in stale_object_keys
                ]
            }
        )
    return payload.model_copy(
        update={
            "reference_keys": reference_keys,
            "reference_descriptions": reference_descriptions,
            "detail_lock": detail_lock,
        }
    )


def _snapshot_detail_analysis_references(
    reference_keys: list[str],
    *,
    analysis_id: str,
    reference_root: Path,
) -> dict[str, Path]:
    snapshots: dict[str, Path] = {}
    source_dir = reference_root / analysis_id / "sources"
    for index, reference_key in enumerate(reference_keys, start=1):
        normalized_key = reference_key.strip()
        relative_source = reference_key_to_relative_path(normalized_key)
        if relative_source is None:
            continue
        source_path = (reference_root / relative_source).resolve()
        source_path.relative_to(reference_root.resolve())
        if not source_path.is_file():
            continue
        suffix = source_path.suffix.lower() or ".img"
        snapshot_path = source_dir / f"reference-{index}{suffix}"
        try:
            source_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, snapshot_path)
        except OSError as exc:
            logger.warning(
                "Skipped detail analysis reference snapshot analysis_id=%s reference_key=%s error=%s",
                analysis_id,
                normalized_key,
                exc,
            )
            continue
        snapshots[normalized_key] = snapshot_path
        if ":" in normalized_key:
            snapshots[normalized_key.split(":", 1)[1]] = snapshot_path
    return snapshots


async def _ensure_detail_analysis_reference_access(
    reference_keys: list[str],
    *,
    current_user: User,
    session: AsyncSession,
) -> None:
    for reference_key in reference_keys:
        object_key = _reference_key_object_key(reference_key)
        if object_key is None:
            raise HTTPException(status_code=404, detail="Reference asset not found")
        await _ensure_reference_asset_access(
            object_key=object_key,
            current_user=current_user,
            session=session,
        )
        _resolve_reference_asset(object_key)


async def _record_detail_crop_assets(
    crops: list[DetailCropOut],
    *,
    current_user: User,
    session: AsyncSession,
) -> None:
    if not crops:
        return
    for crop in crops:
        session.add(
            ReferenceAsset(
                user_id=current_user.id,
                kind="detail",
                object_key=crop.object_key,
                file_name=Path(crop.object_key).name,
                mime_type="image/webp",
            )
        )
    await session.commit()


def _parse_local_revision_metadata(raw_metadata: str) -> LocalRevisionMetadata:
    try:
        payload = json.loads(raw_metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="local_revision_metadata_invalid") from exc
    try:
        return LocalRevisionMetadata.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="local_revision_metadata_invalid") from exc


def _safe_local_revision_segment(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return sanitized or str(uuid.uuid4())


async def _validate_local_revision_references(
    files: list[UploadFile],
    descriptions: list[str],
) -> list[LocalRevisionReferenceUpload]:
    if len(files) > LOCAL_REVISION_MAX_REFERENCE_FILES:
        raise HTTPException(status_code=400, detail="local_revision_too_many_references")

    references: list[LocalRevisionReferenceUpload] = []
    for index, upload in enumerate(files, start=1):
        content = await upload.read()
        if len(content) > LOCAL_REVISION_MAX_REFERENCE_BYTES:
            raise HTTPException(status_code=400, detail="local_revision_reference_too_large")

        try:
            image = Image.open(BytesIO(content))
            width, height = image.size
            pixels = width * height
            if (
                width <= 0
                or height <= 0
                or width > LOCAL_REVISION_MAX_REFERENCE_SIDE
                or height > LOCAL_REVISION_MAX_REFERENCE_SIDE
                or pixels > LOCAL_REVISION_MAX_REFERENCE_PIXELS
            ):
                raise HTTPException(status_code=400, detail="local_revision_reference_too_large")
            image.load()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail="local_revision_reference_invalid") from exc

        description = sanitize_user_text(descriptions[index - 1] if index - 1 < len(descriptions) else "").strip()
        references.append(LocalRevisionReferenceUpload(description=description, image=image.convert("RGBA")))

    return references


def _persist_local_revision_references(
    references: list[LocalRevisionReferenceUpload],
    *,
    character_session_id: str,
    job_id: str,
) -> tuple[list[str], list[ReferenceDescriptionIn]]:
    if not references:
        return [], []

    reference_root = resolve_repo_path(get_settings().reference_upload_dir)
    target_dir = reference_root / "local-revisions" / character_session_id / job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    reference_keys: list[str] = []
    reference_descriptions: list[ReferenceDescriptionIn] = []

    for index, reference in enumerate(references, start=1):
        path = target_dir / f"reference-{index}.png"
        reference.image.save(path, format="PNG")
        object_key = f"references/local-revisions/{character_session_id}/{job_id}/reference-{index}.png"
        reference_key = f"supplemental:{object_key}"
        reference_keys.append(reference_key)

        if reference.description:
            reference_descriptions.append(
                ReferenceDescriptionIn(reference_key=reference_key, description=reference.description)
            )

    return reference_keys, reference_descriptions


def _enforce_create_job_rate_limit(request: Request) -> None:
    settings = get_settings()
    window_seconds = max(1, settings.generation_create_rate_limit_window_seconds)
    max_requests = max(1, settings.generation_create_rate_limit_max_requests)
    now = time.monotonic()
    client_ip = resolve_client_ip(request, settings.trusted_proxy_hosts)
    attempts = _create_job_attempts[client_ip]

    while attempts and now - attempts[0] > window_seconds:
        attempts.popleft()

    if len(attempts) >= max_requests:
        retry_after_seconds = max(1, int(window_seconds - (now - attempts[0])))
        raise HTTPException(
            status_code=429,
            detail="generation_rate_limited",
            headers={"Retry-After": str(retry_after_seconds)},
        )

    attempts.append(now)

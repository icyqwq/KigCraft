import asyncio
from dataclasses import dataclass
from io import BytesIO
import json
import logging
import re
import shutil
import time
from collections import defaultdict, deque
from ipaddress import ip_address, ip_network
from pathlib import Path
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from PIL import Image
from pydantic import BaseModel, Field, ValidationError

from app.core.config import get_settings
from app.core.paths import resolve_repo_path
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
    get_generation_provider,
    request_generation_cancel,
)
from app.generation.queue import generation_queue
from app.generation.schemas import (
    CreateJobRequest,
    DEFAULT_LOCALE,
    GenerationEventOut,
    GenerationJobOut,
    GenerationOutputOut,
    ReferenceDescriptionIn,
    normalize_locale,
)
from app.prompts.router import resolve_requirement_prompt_texts
from app.prompts.safety import sanitize_user_text

router = APIRouter(prefix="/generation", tags=["generation"])
logger = logging.getLogger("uvicorn.error")
LOCAL_REVISION_MAX_REFERENCE_FILES = 1
LOCAL_REVISION_MAX_REFERENCE_BYTES = 8 * 1024 * 1024
LOCAL_REVISION_MAX_REFERENCE_PIXELS = 4_000_000
LOCAL_REVISION_MAX_REFERENCE_SIDE = 3000
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


def clear_jobs() -> None:
    job_store.clear()
    generation_queue.clear_pending()
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


@router.post("/detail-analysis", response_model=DetailAnalysisOut)
async def analyze_generation_details(
    payload: DetailAnalysisRequest,
    provider: ImageGenerationProvider = Depends(get_generation_provider),
) -> DetailAnalysisOut:
    _ensure_real_generation_provider(provider)
    reference_keys = [key.strip() for key in payload.reference_keys if key.strip()]
    if not reference_keys:
        raise HTTPException(status_code=400, detail="At least one reference image is required")

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
    provider: ImageGenerationProvider = Depends(get_generation_provider),
) -> GenerationJobOut:
    _enforce_create_job_rate_limit(request)
    _ensure_real_generation_provider(provider)
    if provider.name in {"codex", "codex_bridge"}:
        await ensure_codex_usage_allows_generation(get_settings())
    job = job_store.create(payload, provider=provider.name)
    generation_queue.submit_job(job.id, provider)
    return job_to_out(job)


@router.post("/local-revision-jobs", response_model=GenerationJobOut)
async def create_local_revision_job(
    request: Request,
    metadata: str = Form(...),
    base_image: UploadFile = File(...),
    mask_image: UploadFile = File(...),
    reference_files: list[UploadFile] = File(default=[]),
    provider: ImageGenerationProvider = Depends(get_generation_provider),
) -> GenerationJobOut:
    _enforce_create_job_rate_limit(request)
    _ensure_real_generation_provider(provider)
    if provider.name != "codex":
        raise HTTPException(status_code=503, detail="local_revision_provider_unsupported")
    await ensure_codex_usage_allows_generation(get_settings())

    parsed = _parse_local_revision_metadata(metadata)
    if parsed.selected_reference_keys or parsed.reference_descriptions:
        raise HTTPException(status_code=400, detail="local_revision_selected_references_unsupported")

    base_bytes = await base_image.read()
    mask_bytes = await mask_image.read()
    try:
        validate_local_edit_images(base_bytes, mask_bytes)
    except LocalEditValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc

    uploaded_references = await _validate_local_revision_references(
        reference_files,
        parsed.uploaded_reference_descriptions,
    )
    job = job_store.create(
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
        provider=provider.name,
    )

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
    patched_job = job_store.patch_prompt_payload(
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

    generation_queue.submit_job(job.id, provider)
    return job_to_out(patched_job)


@router.get("/jobs/{job_id}", response_model=GenerationJobOut)
async def get_job(job_id: str) -> GenerationJobOut:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return job_to_out(job)


@router.get("/jobs/{job_id}/events", response_model=list[GenerationEventOut])
async def get_job_events(job_id: str) -> list[GenerationEventOut]:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return [event_to_out(event) for event in job.events]


@router.post("/jobs/{job_id}/accept", response_model=GenerationJobOut)
async def accept_job_output(job_id: str, payload: AcceptOutputRequest) -> GenerationJobOut:
    existing_job = job_store.get(job_id)
    if existing_job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")

    job = job_store.accept_output(job_id, payload.output_index)
    if job is None:
        raise HTTPException(status_code=400, detail="Candidate output is not available yet")

    asyncio.create_task(
        request_generation_cancel(job.provider, job.id, job.prompt_payload)
    )
    return job_to_out(job)


@router.post("/projects/{project_id}/jobs", response_model=LegacyJobOut)
async def create_legacy_project_job(
    project_id: str,
    payload: LegacyCreateJobRequest,
    request: Request,
    provider: ImageGenerationProvider = Depends(get_generation_provider),
) -> LegacyJobOut:
    _enforce_create_job_rate_limit(request)
    _ensure_real_generation_provider(provider)
    if provider.name in {"codex", "codex_bridge"}:
        await ensure_codex_usage_allows_generation(get_settings())
    job = job_store.create(
        CreateJobRequest(
            character_session_id=project_id,
            free_text=payload.free_text,
            requirement_ids=payload.chip_ids,
            reference_keys=payload.reference_keys,
        ),
        provider=provider.name,
    )
    if provider.name != "fixture":
        generation_queue.submit_job(job.id, provider)
        return legacy_job_to_out(job, project_id)

    await generation_queue.run_job(job.id, provider)
    stored_job = job_store.get(job.id)
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


def _provider_failure_detail(operation: str, exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    return f"{operation}_provider_failed: {message}"


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
        source_dir.mkdir(parents=True, exist_ok=True)
        suffix = source_path.suffix.lower() or ".img"
        snapshot_path = source_dir / f"reference-{index}{suffix}"
        shutil.copy2(source_path, snapshot_path)
        snapshots[normalized_key] = snapshot_path
        if ":" in normalized_key:
            snapshots[normalized_key.split(":", 1)[1]] = snapshot_path
    return snapshots


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
    client_ip = _client_ip(request, settings.trusted_proxy_hosts)
    attempts = _create_job_attempts[client_ip]

    while attempts and now - attempts[0] > window_seconds:
        attempts.popleft()

    if len(attempts) >= max_requests:
        retry_after_seconds = max(1, int(window_seconds - (now - attempts[0])))
        raise HTTPException(
            status_code=429,
            detail="生成请求过于频繁，请稍后再试。",
            headers={"Retry-After": str(retry_after_seconds)},
        )

    attempts.append(now)


def _client_ip(request: Request, trusted_proxy_hosts: str) -> str:
    direct_host = request.client.host if request.client else "unknown"
    if not _is_trusted_proxy(direct_host, trusted_proxy_hosts):
        return direct_host

    forwarded_for = request.headers.get("x-forwarded-for", "")
    for value in forwarded_for.split(","):
        candidate = value.strip()
        if candidate:
            return candidate

    real_ip = request.headers.get("x-real-ip", "").strip()
    return real_ip or direct_host


def _is_trusted_proxy(host: str, trusted_proxy_hosts: str) -> bool:
    trusted_values = [item.strip() for item in trusted_proxy_hosts.split(",") if item.strip()]
    if "*" in trusted_values:
        return True

    try:
        remote_ip = ip_address(host)
    except ValueError:
        return False

    for trusted_value in trusted_values:
        try:
            if "/" in trusted_value:
                if remote_ip in ip_network(trusted_value, strict=False):
                    return True
            elif remote_ip == ip_address(trusted_value):
                return True
        except ValueError:
            continue
    return False

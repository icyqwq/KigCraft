from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.job_store import StoredJob, job_store

router = APIRouter(prefix="/audit", tags=["audit"])
ADMIN_SESSION_COOKIE = "kig_admin_audit_session"
IMAGE_MEDIA_TYPES = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class QuotaPolicy(BaseModel):
    window_hours: int = Field(default=5, ge=1, le=168)
    normal_window_limit: int = Field(default=8, ge=1, le=1000)
    premium_unlimited: bool = True
    parallel_generation_limit: int = Field(default=8, ge=1, le=64)


class AuditSummaryOut(BaseModel):
    total_users: int
    active_users: int
    job_counts: dict[str, int]
    queue_length: int
    quota_policy: QuotaPolicy
    total_calls: int
    success_rate: float
    failure_rate: float
    parallel_slots_used: int
    token_usage: "AuditTokenUsageOut"
    image_usage: "AuditImageUsageOut"


class AuditTokenUsageOut(BaseModel):
    jobs_with_usage: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0


class AuditImageUsageOut(BaseModel):
    generated_images: int = 0
    jobs_with_outputs: int = 0
    images_with_token_usage: int = 0
    input_tokens_per_image: float = 0
    cached_input_tokens_per_image: float = 0
    output_tokens_per_image: float = 0
    reasoning_output_tokens_per_image: float = 0
    total_tokens_per_image: float = 0


class AuditReferenceImageOut(BaseModel):
    reference_key: str
    image_url: str | None = None
    kind: str | None = None
    description: str | None = None


class AuditGenerationOutputOut(BaseModel):
    index: int
    object_key: str
    image_url: str
    width: int
    height: int


class AuditGenerationJobOut(BaseModel):
    id: str
    character_session_id: str
    generation_mode: str
    status: str
    progress: int
    phase_label: str
    provider: str
    created_at: str | None = None
    updated_at: str | None = None
    user_notes: str = ""
    user_requirements: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    references: list[AuditReferenceImageOut] = Field(default_factory=list)
    outputs: list[AuditGenerationOutputOut] = Field(default_factory=list)
    token_usage: AuditTokenUsageOut | None = None


class AuditLoginRequest(BaseModel):
    password: str = Field(min_length=1)


class AuditSessionOut(BaseModel):
    authenticated: bool


_quota_policy = QuotaPolicy()
_sessions: dict[str, datetime] = {}
_login_failures: dict[str, list[datetime]] = {}

ACTIVE_JOB_STATUSES = {
    "queued",
    "preparing_references",
    "running",
    "codex_generating",
    "saving_outputs",
}


def clear_audit_state() -> None:
    global _quota_policy
    _quota_policy = QuotaPolicy()
    _sessions.clear()
    _login_failures.clear()


def _rate(count: int, total: int) -> float:
    if total == 0:
        return 0
    return round(count / total, 4)


def _unique_user_count(jobs: list[StoredJob]) -> int:
    return len({job.character_session_id for job in jobs if job.character_session_id})


def _token_usage_summary(jobs: list[StoredJob]) -> AuditTokenUsageOut:
    summary = AuditTokenUsageOut()
    for job in jobs:
        usage = job.token_usage
        if usage is None or not usage.has_values():
            continue
        summary.jobs_with_usage += 1
        summary.input_tokens += usage.input_tokens or 0
        summary.cached_input_tokens += usage.cached_input_tokens or 0
        summary.output_tokens += usage.output_tokens or 0
        summary.reasoning_output_tokens += usage.reasoning_output_tokens or 0
        summary.total_tokens += usage.total_tokens or 0
    return summary


def _token_per_image(token_count: int, image_count: int) -> float:
    if image_count <= 0:
        return 0
    return round(token_count / image_count, 2)


def _image_usage_summary(jobs: list[StoredJob], token_usage: AuditTokenUsageOut) -> AuditImageUsageOut:
    generated_images = sum(len(job.outputs) for job in jobs)
    jobs_with_outputs = sum(1 for job in jobs if job.outputs)
    images_with_token_usage = sum(
        len(job.outputs)
        for job in jobs
        if job.outputs and job.token_usage is not None and job.token_usage.has_values()
    )
    denominator = images_with_token_usage or generated_images
    return AuditImageUsageOut(
        generated_images=generated_images,
        jobs_with_outputs=jobs_with_outputs,
        images_with_token_usage=images_with_token_usage,
        input_tokens_per_image=_token_per_image(token_usage.input_tokens, denominator),
        cached_input_tokens_per_image=_token_per_image(token_usage.cached_input_tokens, denominator),
        output_tokens_per_image=_token_per_image(token_usage.output_tokens, denominator),
        reasoning_output_tokens_per_image=_token_per_image(token_usage.reasoning_output_tokens, denominator),
        total_tokens_per_image=_token_per_image(token_usage.total_tokens, denominator),
    )


def _token_usage_out_from_job(job: StoredJob) -> AuditTokenUsageOut | None:
    usage = job.token_usage
    if usage is None or not usage.has_values():
        return None
    return AuditTokenUsageOut(
        jobs_with_usage=1,
        input_tokens=usage.input_tokens or 0,
        cached_input_tokens=usage.cached_input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        reasoning_output_tokens=usage.reasoning_output_tokens or 0,
        total_tokens=usage.total_tokens or 0,
    )


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _job_created_at(job: StoredJob) -> str | None:
    return job.events[0].created_at if job.events else None


def _job_updated_at(job: StoredJob) -> str | None:
    return job.events[-1].created_at if job.events else None


def _effective_job_status(job: StoredJob) -> str:
    return job.status


def _reference_descriptions_by_key(prompt_payload: dict[str, Any]) -> dict[str, str]:
    descriptions: dict[str, str] = {}
    for item in prompt_payload.get("reference_descriptions") or []:
        if not isinstance(item, dict):
            continue
        reference_key = str(item.get("reference_key") or "").strip()
        description = str(item.get("description") or "").strip()
        if reference_key and description:
            descriptions[reference_key] = description
    return descriptions


def _normalized_reference_path(reference_key: Any) -> str | None:
    if not isinstance(reference_key, str) or not reference_key:
        return None
    normalized = reference_key.replace("\\", "/")
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1]
    posix_path = PurePosixPath(normalized)
    if (
        posix_path.is_absolute()
        or any(part == ".." for part in posix_path.parts)
        or len(posix_path.parts) < 3
        or posix_path.parts[0] != "references"
    ):
        return None
    return "/".join(posix_path.parts)


def _reference_kind(reference_key: str) -> str | None:
    if ":" not in reference_key:
        return None
    kind = reference_key.split(":", 1)[0].strip()
    return kind or None


def _reference_image_url(reference_key: Any) -> str | None:
    normalized_path = _normalized_reference_path(reference_key)
    if normalized_path is None:
        return None
    return f"/api/audit/references/{normalized_path}"


def _job_to_generation_record(job: StoredJob) -> AuditGenerationJobOut:
    prompt_payload = job.prompt_payload
    descriptions = _reference_descriptions_by_key(prompt_payload)
    reference_keys = [str(reference_key) for reference_key in prompt_payload.get("reference_keys") or []]
    return AuditGenerationJobOut(
        id=job.id,
        character_session_id=job.character_session_id,
        generation_mode=job.generation_mode,
        status=_effective_job_status(job),
        progress=job.progress,
        phase_label=job.phase_label,
        provider=job.provider,
        created_at=_job_created_at(job),
        updated_at=_job_updated_at(job),
        user_notes=str(prompt_payload.get("user_notes") or ""),
        user_requirements=str(prompt_payload.get("user_requirements") or ""),
        requirement_ids=[str(item) for item in prompt_payload.get("requirement_ids") or []],
        references=[
            AuditReferenceImageOut(
                reference_key=reference_key,
                image_url=_reference_image_url(reference_key),
                kind=_reference_kind(reference_key),
                description=descriptions.get(reference_key),
            )
            for reference_key in reference_keys
        ],
        outputs=[
            AuditGenerationOutputOut(
                index=output.index,
                object_key=output.object_key,
                image_url=output.image_url,
                width=output.width,
                height=output.height,
            )
            for output in job.outputs
        ],
        token_usage=_token_usage_out_from_job(job),
    )


def _resolve_reference_asset(asset_path: str) -> Path:
    normalized = _normalized_reference_path(asset_path)
    if normalized is None:
        raise HTTPException(status_code=404, detail="Reference asset not found")
    posix_path = PurePosixPath(normalized)
    reference_root = resolve_repo_path(get_settings().reference_upload_dir).resolve()
    candidate = (reference_root / Path(*posix_path.parts[1:])).resolve()
    try:
        candidate.relative_to(reference_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Reference asset not found") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Reference asset not found")
    return candidate


def _image_media_type(file_path: Path) -> str:
    return IMAGE_MEDIA_TYPES.get(file_path.suffix.lower(), "application/octet-stream")


def _prune_expired_sessions() -> None:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(hours=settings.admin_audit_session_hours)
    expired_tokens = [token for token, created_at in _sessions.items() if created_at < cutoff]
    for token in expired_tokens:
        _sessions.pop(token, None)


def _prune_login_failures(ip_address: str) -> list[datetime]:
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.admin_audit_retry_window_minutes)
    failures = [created_at for created_at in _login_failures.get(ip_address, []) if created_at >= cutoff]
    if failures:
        _login_failures[ip_address] = failures
    else:
        _login_failures.pop(ip_address, None)
    return failures


def _is_rate_limited(ip_address: str) -> bool:
    settings = get_settings()
    return len(_prune_login_failures(ip_address)) >= settings.admin_audit_max_login_attempts


def _record_login_failure(ip_address: str) -> None:
    failures = _prune_login_failures(ip_address)
    failures.append(datetime.now(UTC))
    _login_failures[ip_address] = failures


def _clear_login_failures(ip_address: str) -> None:
    _login_failures.pop(ip_address, None)


def require_admin_session(request: Request) -> None:
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Admin authentication required")

    _prune_expired_sessions()
    if token not in _sessions:
        raise HTTPException(status_code=401, detail="Admin authentication required")


@router.post("/login", response_model=AuditSessionOut)
async def login_audit_admin(payload: AuditLoginRequest, request: Request, response: Response) -> AuditSessionOut:
    settings = get_settings()
    ip_address = _client_ip(request)
    if _is_rate_limited(ip_address):
        raise HTTPException(status_code=429, detail="Too many audit login attempts")

    if not secrets.compare_digest(payload.password, settings.admin_audit_password):
        _record_login_failure(ip_address)
        if _is_rate_limited(ip_address):
            raise HTTPException(status_code=429, detail="Too many audit login attempts")
        raise HTTPException(status_code=401, detail="Invalid audit password")

    _clear_login_failures(ip_address)
    _prune_expired_sessions()
    token = secrets.token_urlsafe(32)
    _sessions[token] = datetime.now(UTC)
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        token,
        httponly=True,
        max_age=settings.admin_audit_session_hours * 3600,
        path="/",
        samesite="lax",
    )
    return AuditSessionOut(authenticated=True)


@router.post("/logout", response_model=AuditSessionOut)
async def logout_audit_admin(request: Request, response: Response) -> AuditSessionOut:
    token = request.cookies.get(ADMIN_SESSION_COOKIE)
    if token:
        _sessions.pop(token, None)
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    return AuditSessionOut(authenticated=False)


@router.get("/session", response_model=AuditSessionOut)
async def get_audit_session(request: Request) -> AuditSessionOut:
    try:
        require_admin_session(request)
    except HTTPException:
        return AuditSessionOut(authenticated=False)
    return AuditSessionOut(authenticated=True)


@router.get("/summary", response_model=AuditSummaryOut, dependencies=[Depends(require_admin_session)])
async def get_audit_summary() -> AuditSummaryOut:
    jobs = job_store.list()
    active_jobs = [job for job in jobs if _effective_job_status(job) in ACTIVE_JOB_STATUSES]
    job_counts = Counter(_effective_job_status(job) for job in jobs)
    total_calls = len(jobs)
    active_job_count = len(active_jobs)
    token_usage = _token_usage_summary(jobs)

    return AuditSummaryOut(
        total_users=_unique_user_count(jobs),
        active_users=_unique_user_count(active_jobs),
        job_counts=dict(job_counts),
        queue_length=job_counts.get("queued", 0),
        quota_policy=_quota_policy,
        total_calls=total_calls,
        success_rate=_rate(job_counts.get("succeeded", 0), total_calls),
        failure_rate=_rate(job_counts.get("failed", 0), total_calls),
        parallel_slots_used=min(active_job_count, _quota_policy.parallel_generation_limit),
        token_usage=token_usage,
        image_usage=_image_usage_summary(jobs, token_usage),
    )


@router.get(
    "/generation-jobs",
    response_model=list[AuditGenerationJobOut],
    dependencies=[Depends(require_admin_session)],
)
async def list_audit_generation_jobs() -> list[AuditGenerationJobOut]:
    jobs = sorted(
        job_store.list(),
        key=lambda job: _job_updated_at(job) or _job_created_at(job) or "",
        reverse=True,
    )
    return [_job_to_generation_record(job) for job in jobs]


@router.get(
    "/references/{asset_path:path}",
    dependencies=[Depends(require_admin_session)],
    include_in_schema=False,
)
async def get_audit_reference_asset(asset_path: str) -> FileResponse:
    file_path = _resolve_reference_asset(asset_path)
    return FileResponse(file_path, media_type=_image_media_type(file_path))


@router.patch("/quota-policy", response_model=QuotaPolicy, dependencies=[Depends(require_admin_session)])
async def update_quota_policy(policy: QuotaPolicy) -> QuotaPolicy:
    global _quota_policy
    _quota_policy = policy
    return _quota_policy

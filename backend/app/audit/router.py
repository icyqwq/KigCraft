import base64
from collections import Counter
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path, PurePosixPath
import re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.admin.dependencies import require_admin_user
from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.db.models import User
from app.quota.settings import QuotaPolicy, get_quota_policy, save_quota_policy, clear_quota_policy
from app.generation.codex_usage import get_codex_usage_snapshot
from app.generation.codex_app_server import CodexRateLimitWindow, CodexUsageSnapshot
from app.generation.job_store import StoredJob, job_store
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_session
from app.admin.v2_jobs import admin_jobs

router = APIRouter(prefix="/audit", tags=["audit"])
ADMIN_SESSION_COOKIE = "kig_admin_audit_session"
IMAGE_MEDIA_TYPES = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class CodexRateLimitWindowOut(BaseModel):
    used_percent: float | None = None
    remaining_percent: float | None = None
    reset_at: str | None = None
    reset_after_seconds: int | None = None
    window_duration_mins: int | None = None


class CodexQuotaOut(BaseModel):
    available: bool = False
    checked_at: str | None = None
    plan_type: str | None = None
    source: str | None = None
    error: str | None = None
    five_hour: CodexRateLimitWindowOut | None = None
    weekly: CodexRateLimitWindowOut | None = None


class AuditUsageTrendPointOut(BaseModel):
    label: str
    calls: int = 0
    succeeded: int = 0
    failed: int = 0
    queued: int = 0
    active: int = 0
    images: int = 0
    images_30d_avg: float = 0
    tokens: int = 0


class CodexUsagePointOut(BaseModel):
    at: str
    primary_remaining_percent: float
    primary_window_minutes: int | None = None
    secondary_remaining_percent: float | None = None
    secondary_window_minutes: int | None = None


class CodexUsageHistoryOut(BaseModel):
    plan_type: str | None = None
    points: list[CodexUsagePointOut]


class AuditSummaryOut(BaseModel):
    total_users: int
    active_users: int
    job_counts: dict[str, int]
    queue_length: int
    running_job_count: int = 0
    quota_policy: QuotaPolicy
    total_calls: int
    success_rate: float
    failure_rate: float
    parallel_slots_used: int
    token_usage: "AuditTokenUsageOut"
    image_usage: "AuditImageUsageOut"
    codex_quota: CodexQuotaOut
    usage_trend: list[AuditUsageTrendPointOut] = Field(default_factory=list)
    latest_job_updated_at: str | None = None


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


class AuditGenerationJobsPageOut(BaseModel):
    items: list["AuditGenerationJobOut"]
    next_cursor: str | None = None
    has_more: bool = False


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
    failure_reason: str | None = None


class AuditLoginRequest(BaseModel):
    password: str = Field(min_length=1)


class AuditSessionOut(BaseModel):
    authenticated: bool


_sessions: dict[str, datetime] = {}
_login_failures: dict[str, list[datetime]] = {}

QUEUED_JOB_STATUSES = {"queued"}
RUNNING_JOB_STATUSES = {
    "preparing_references",
    "generating",
    "running",
    "codex_generating",
    "saving_outputs",
}
ACTIVE_JOB_STATUSES = QUEUED_JOB_STATUSES | RUNNING_JOB_STATUSES


def clear_audit_state() -> None:
    clear_quota_policy()
    _sessions.clear()
    _login_failures.clear()


def get_codex_usage_min_remaining_percent() -> int:
    return get_quota_policy().codex_usage_min_remaining_percent


def _codex_window_out(window: CodexRateLimitWindow | None) -> CodexRateLimitWindowOut | None:
    if window is None:
        return None
    return CodexRateLimitWindowOut(
        used_percent=round(window.used_percent, 2),
        remaining_percent=round(window.remaining_percent, 2),
        reset_at=window.reset_at.isoformat() if window.reset_at else None,
        reset_after_seconds=window.reset_after_seconds,
        window_duration_mins=window.window_duration_mins,
    )


def _codex_quota_summary(snapshot: CodexUsageSnapshot) -> CodexQuotaOut:
    return CodexQuotaOut(
        available=snapshot.available,
        checked_at=snapshot.checked_at.isoformat(),
        plan_type=snapshot.plan_type,
        source=snapshot.source,
        error=snapshot.error,
        five_hour=_codex_window_out(snapshot.five_hour),
        weekly=_codex_window_out(snapshot.weekly),
    )


def _rate(count: int, total: int) -> float:
    if total == 0:
        return 0
    return round(count / total, 4)


def _encode_jobs_cursor(cursor: str) -> str:
    return base64.urlsafe_b64encode(cursor.encode("utf-8")).decode("ascii")


def _decode_jobs_cursor(encoded_cursor: str) -> tuple[str, str]:
    try:
        decoded = base64.urlsafe_b64decode(encoded_cursor.encode("ascii")).decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid generation jobs cursor") from exc
    if "\x1f" not in decoded:
        raise HTTPException(status_code=400, detail="Invalid generation jobs cursor")
    updated_at, job_id = decoded.split("\x1f", 1)
    if not updated_at or not job_id:
        raise HTTPException(status_code=400, detail="Invalid generation jobs cursor")
    return updated_at, job_id


def _job_trend_datetime(job: StoredJob) -> datetime | None:
    source_time = _job_updated_at(job) or _job_created_at(job)
    if not source_time:
        return None
    try:
        return datetime.fromisoformat(source_time.replace("Z", "+00:00"))
    except ValueError:
        return None


def _usage_trend_summary(jobs: list[StoredJob]) -> list[AuditUsageTrendPointOut]:
    dated_jobs = [(job, parsed) for job in jobs if (parsed := _job_trend_datetime(job)) is not None]
    if not dated_jobs:
        return []

    last_day = max(parsed.date() for _job, parsed in dated_jobs)
    window_days = [last_day - timedelta(days=offset) for offset in range(13, -1, -1)]
    buckets: dict[str, AuditUsageTrendPointOut] = {
        day.isoformat(): AuditUsageTrendPointOut(label=day.strftime("%m-%d"))
        for day in window_days
    }
    daily_images = Counter()
    for job, parsed in dated_jobs:
        daily_images[parsed.date()] += len(job.outputs)
        day_key = parsed.date().isoformat()
        current = buckets.get(day_key)
        if current is None:
            continue
        status = _effective_job_status(job)
        buckets[day_key] = AuditUsageTrendPointOut(
            label=current.label,
            calls=current.calls + 1,
            images=current.images + len(job.outputs),
            tokens=current.tokens + int((job.token_usage.total_tokens if job.token_usage else None) or 0),
            succeeded=current.succeeded + (1 if status in {"succeeded", "accepted"} else 0),
            failed=current.failed + (1 if status == "failed" else 0),
            queued=current.queued + (1 if status in QUEUED_JOB_STATUSES else 0),
            active=current.active + (1 if status in ACTIVE_JOB_STATUSES else 0),
        )
    for day in window_days:
        rolling_start = day - timedelta(days=29)
        active_day_images = [
            image_count
            for image_day, image_count in daily_images.items()
            if rolling_start <= image_day <= day and image_count > 0
        ]
        current = buckets[day.isoformat()]
        current.images_30d_avg = round(sum(active_day_images) / len(active_day_images), 1) if active_day_images else 0
    return [buckets[day.isoformat()] for day in window_days]


def _latest_job_updated_at(jobs: list[StoredJob]) -> str | None:
    timestamps = [
        value
        for job in jobs
        for value in [_job_updated_at(job) or _job_created_at(job)]
        if value
    ]
    if not timestamps:
        return None
    return max(timestamps)


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
    v2_references = [
        AuditReferenceImageOut(
            reference_key=str(ref["asset_id"]),
            image_url=f"/api/audit/v2-assets/{ref['asset_id']}",
            kind=ref.get("role"),
            description=ref.get("usage"),
        )
        for ref in prompt_payload.get("v2_references") or []
    ]
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
        references=v2_references or [
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
        failure_reason=_safe_failure_reason(job),
    )


def _safe_failure_reason(job: StoredJob) -> str | None:
    if _effective_job_status(job) != "failed":
        return None
    message = next((event.message for event in reversed(job.events) if event.type == "failed"), "")
    if not message:
        return "Generation failed without a provider detail."
    sanitized = re.sub(r"(?i)(token|api[_-]?key|authorization|bearer)\s*[=:]\s*\S+", r"\1=[redacted]", message)
    sanitized = re.sub(r"https?://\S+", "[redacted-url]", sanitized)
    sanitized = " ".join(sanitized.split())
    return sanitized[:500]


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
async def login_audit_admin() -> AuditSessionOut:
    raise HTTPException(status_code=410, detail="admin_password_login_removed")


@router.post("/logout", response_model=AuditSessionOut)
async def logout_audit_admin() -> AuditSessionOut:
    raise HTTPException(status_code=410, detail="admin_password_login_removed")


@router.get("/session", response_model=AuditSessionOut)
async def get_audit_session() -> AuditSessionOut:
    raise HTTPException(status_code=410, detail="admin_password_login_removed")


@router.get("/summary", response_model=AuditSummaryOut)
async def get_audit_summary(
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AuditSummaryOut:
    jobs = await admin_jobs(session)
    in_flight_jobs = [job for job in jobs if _effective_job_status(job) in ACTIVE_JOB_STATUSES]
    running_jobs = [job for job in jobs if _effective_job_status(job) in RUNNING_JOB_STATUSES]
    job_counts = Counter(_effective_job_status(job) for job in jobs)
    total_calls = len(jobs)
    token_usage = _token_usage_summary(jobs)
    quota_policy = get_quota_policy()
    codex_snapshot = await get_codex_usage_snapshot(get_settings())

    return AuditSummaryOut(
        total_users=_unique_user_count(jobs),
        active_users=_unique_user_count(in_flight_jobs),
        job_counts=dict(job_counts),
        queue_length=sum(job_counts.get(status, 0) for status in QUEUED_JOB_STATUSES),
        running_job_count=len(running_jobs),
        quota_policy=quota_policy,
        total_calls=total_calls,
        success_rate=_rate(job_counts.get("succeeded", 0), total_calls),
        failure_rate=_rate(job_counts.get("failed", 0), total_calls),
        parallel_slots_used=min(len(running_jobs), quota_policy.parallel_generation_limit),
        token_usage=token_usage,
        image_usage=_image_usage_summary(jobs, token_usage),
        codex_quota=_codex_quota_summary(codex_snapshot),
        usage_trend=_usage_trend_summary(jobs),
        latest_job_updated_at=_latest_job_updated_at(jobs),
    )


@router.get("/codex-usage-history", response_model=CodexUsageHistoryOut)
async def get_codex_usage_history(
    _: User = Depends(require_admin_user),
    hours: int = Query(default=168, ge=1, le=24 * 90),
) -> CodexUsageHistoryOut:
    from app.generation.codex_usage_history import read

    entries = read(get_settings(), hours)
    return CodexUsageHistoryOut(
        plan_type=entries[-1].get("plan") if entries else None,
        points=[
            CodexUsagePointOut(
                at=item["at"], primary_remaining_percent=item["p"], primary_window_minutes=item.get("pw"),
                secondary_remaining_percent=item.get("s"), secondary_window_minutes=item.get("sw"),
            )
            for item in entries
        ],
    )


@router.get(
    "/generation-jobs",
    response_model=AuditGenerationJobsPageOut,
)
async def list_audit_generation_jobs(
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> AuditGenerationJobsPageOut:
    jobs = sorted(
        await admin_jobs(session),
        key=lambda job: (_job_updated_at(job) or _job_created_at(job) or "", job.id),
        reverse=True,
    )
    start_index = 0
    if cursor:
        cursor_updated_at, cursor_id = _decode_jobs_cursor(cursor)
        for index, job in enumerate(jobs):
            if job.id == cursor_id and (_job_updated_at(job) or _job_created_at(job) or "") == cursor_updated_at:
                start_index = index + 1
                break

    page_jobs = jobs[start_index : start_index + limit]
    has_more = start_index + limit < len(jobs)
    next_cursor_raw = None
    if has_more and page_jobs:
        last_job = page_jobs[-1]
        next_cursor_raw = f"{_job_updated_at(last_job) or _job_created_at(last_job) or ''}\x1f{last_job.id}"
    items = [_job_to_generation_record(job) for job in page_jobs]
    return AuditGenerationJobsPageOut(
        items=items,
        next_cursor=_encode_jobs_cursor(next_cursor_raw) if next_cursor_raw else None,
        has_more=has_more,
    )


@router.get(
    "/references/{asset_path:path}",
    include_in_schema=False,
)
async def get_audit_reference_asset(
    asset_path: str,
    _: User = Depends(require_admin_user),
) -> FileResponse:
    file_path = _resolve_reference_asset(asset_path)
    return FileResponse(file_path, media_type=_image_media_type(file_path))


@router.get("/v2-assets/{asset_id}", include_in_schema=False)
async def get_audit_v2_asset(
    asset_id: str,
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Admin-only read of a stored V2 image (no watermarking: this is the operator's audit view)."""
    from app.assets.repository import storage_io
    from app.assets.storage import asset_path
    from app.design.models import Asset
    asset = (await session.execute(select(Asset).where(Asset.id == asset_id, Asset.status == "ready"))).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    path = await storage_io(asset_path, asset, Path(get_settings().v2_storage_dir))
    return FileResponse(path, media_type=asset.mime_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.patch("/quota-policy", response_model=QuotaPolicy)
async def update_quota_policy(
    policy: QuotaPolicy,
    _: User = Depends(require_admin_user),
) -> QuotaPolicy:
    return save_quota_policy(policy)

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.dependencies import require_admin_user
from app.audit.router import (
    ACTIVE_JOB_STATUSES,
    RUNNING_JOB_STATUSES,
    AuditGenerationJobOut,
    AuditImageUsageOut,
    AuditTokenUsageOut,
    QuotaPolicy,
    _image_usage_summary,
    _job_to_generation_record,
    _rate,
    _token_usage_summary,
    get_quota_policy,
    save_quota_policy,
)
from app.auth.models import UserRole
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import AuditCounter, AuthSession, GenerationJob, User, UserGenerationQuotaWindow
from app.db.session import get_session
from app.generation.job_store import StoredJob, job_store
from app.admin.v2_jobs import admin_jobs, v2_job_counts_by_user
from app.generation.presets import (
    ACTIVE_PRESETS,
    ActiveProviderPreset,
    ActiveProviderPresetStore,
    ProviderPresetId,
)
from app.generation.provider_usage import ProviderUsageSummary, summarize_provider_usage
from app.generation.queue import generation_queue
from app.generation.reliability import summarize_reliability
from app.generation.quota import (
    adjust_generation_quota_balance,
    current_generation_quota_window,
    GenerationQuotaResetResult,
    get_generation_quota_state,
    reset_generation_quota,
)


router = APIRouter(prefix="/admin", tags=["admin"])


class AdminUserOut(BaseModel):
    id: str
    email: str
    role: UserRole
    is_banned: bool
    created_at: str
    last_login_at: str | None
    task_count: int = 0
    call_count: int = 0
    generation_count: int = 0
    daily_generation_limit: int = 20
    daily_generation_limit_override: int | None = None
    daily_generation_used: int = 0
    daily_generation_remaining: int = 20
    daily_generation_balance: int = 20
    daily_generation_reset_at: str | None = None


class AdminUsersOut(BaseModel):
    users: list[AdminUserOut]
    total: int = 0
    limit: int = 50
    offset: int = 0
    has_more: bool = False
    sort_by: str = "created_at"
    sort_dir: str = "desc"


class AdminUserRoleUpdateIn(BaseModel):
    role: UserRole


class AdminUserBanUpdateIn(BaseModel):
    is_banned: bool


class AdminRevokeSessionsOut(BaseModel):
    revoked_sessions: int


class AdminDailyGenerationQuotaResetOut(BaseModel):
    reset_count: int
    window_start_at: str


class AdminUserDailyGenerationLimitUpdateIn(BaseModel):
    limit: int | None = Field(default=None, ge=0, le=100000)


class AdminUserDailyGenerationBalanceAdjustIn(BaseModel):
    delta: int = Field(ge=-100000, le=100000)


class AdminQuotaPolicy(BaseModel):
    window_hours: int = Field(default=5, ge=1, le=168)
    window_limit: int = Field(default=8, ge=1, le=1000)
    parallel_generation_limit: int = Field(default=4, ge=1, le=64)
    codex_usage_min_remaining_percent: int = Field(default=25, ge=0, le=100)
    daily_generation_limit: int = Field(default=20, ge=0, le=100000)
    daily_generation_custom_limit_default: int = Field(default=20, ge=0, le=100000)
    daily_generation_reset_hour: int = Field(default=6, ge=0, le=23)


class AdminQueueStatusOut(BaseModel):
    pending_count: int
    active_count: int
    parallelism: int
    high_load: bool = False


class AdminAuditSummaryOut(BaseModel):
    total_users: int
    active_users: int
    total_jobs: int
    job_counts: dict[str, int]
    queue_length: int
    queue_status: AdminQueueStatusOut
    quota_policy: AdminQuotaPolicy
    legacy_counters: dict[str, int]
    total_calls: int
    success_rate: float
    failure_rate: float
    parallel_slots_used: int
    token_usage: AuditTokenUsageOut
    image_usage: AuditImageUsageOut
    reliability: dict = Field(default_factory=dict)


class AdminProviderPresetOut(BaseModel):
    id: ProviderPresetId
    label: str
    analysis_provider: str
    analysis_model: str
    image_provider: str
    image_model: str


class AdminActiveProviderPresetOut(BaseModel):
    preset_id: ProviderPresetId
    updated_by: str = ""
    updated_at: str = ""


class AdminProviderPresetsOut(BaseModel):
    active: AdminActiveProviderPresetOut
    presets: list[AdminProviderPresetOut]


class AdminProviderPresetUpdateIn(BaseModel):
    preset_id: Literal["codex_full"]


class AdminProviderUsageSummaryOut(BaseModel):
    provider: str
    calls: int
    successes: int
    failures: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    generated_images: int


def _datetime_out(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _provider_preset_store() -> ActiveProviderPresetStore:
    return ActiveProviderPresetStore(Path(get_settings().provider_preset_path))


def _active_provider_preset_out(
    active: ActiveProviderPreset,
) -> AdminActiveProviderPresetOut:
    return AdminActiveProviderPresetOut(
        preset_id=active.preset_id,
        updated_by=active.updated_by,
        updated_at=active.updated_at,
    )


def _provider_usage_summary_out(
    summary: ProviderUsageSummary,
) -> AdminProviderUsageSummaryOut:
    return AdminProviderUsageSummaryOut(
        provider=summary.provider,
        calls=summary.calls,
        successes=summary.successes,
        failures=summary.failures,
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
        total_tokens=summary.total_tokens,
        generated_images=summary.generated_images,
    )


def _quota_policy_out(policy: QuotaPolicy) -> AdminQuotaPolicy:
    return AdminQuotaPolicy.model_validate(policy.model_dump())


def _raise_self_lockout(detail: str) -> None:
    raise HTTPException(status_code=409, detail=detail)


async def _admin_user_out(user: User, job_count: int, session: AsyncSession) -> AdminUserOut:
    quota_state = await get_generation_quota_state(session, user)
    return AdminUserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        is_banned=user.is_banned,
        created_at=_datetime_out(user.created_at) or "",
        last_login_at=_datetime_out(user.last_login_at),
        task_count=job_count,
        call_count=job_count,
        generation_count=job_count,
        daily_generation_limit=quota_state.limit,
        daily_generation_limit_override=user.daily_generation_limit_override,
        daily_generation_used=quota_state.used,
        daily_generation_remaining=quota_state.remaining,
        daily_generation_balance=quota_state.remaining,
        daily_generation_reset_at=_datetime_out(quota_state.reset_at),
    )


def _quota_reset_out(result: GenerationQuotaResetResult) -> AdminDailyGenerationQuotaResetOut:
    return AdminDailyGenerationQuotaResetOut(
        reset_count=result.reset_count,
        window_start_at=_datetime_out(result.window_start_at) or "",
    )


async def _job_counts_by_user(session: AsyncSession) -> dict[str, int]:
    if get_settings().v2_enabled:
        return await v2_job_counts_by_user(session)
    result = await session.execute(
        select(GenerationJob.user_id, func.count(GenerationJob.id)).group_by(GenerationJob.user_id)
    )
    return {str(user_id): int(count) for user_id, count in result.all() if user_id is not None}


async def _job_counts_by_user_ids(session: AsyncSession, user_ids: list[str]) -> dict[str, int]:
    if not user_ids:
        return {}
    if get_settings().v2_enabled:
        return await v2_job_counts_by_user(session, user_ids)
    result = await session.execute(
        select(GenerationJob.user_id, func.count(GenerationJob.id))
        .where(GenerationJob.user_id.in_(user_ids))
        .group_by(GenerationJob.user_id)
    )
    return {str(user_id): int(count) for user_id, count in result.all() if user_id is not None}


def _admin_user_filters(query: str | None) -> list:
    if query and query.strip():
        normalized_query = f"%{query.strip().lower()}%"
        return [func.lower(User.email).like(normalized_query)]
    return []


async def _admin_users_total(session: AsyncSession, filters: list) -> int:
    statement = select(func.count(User.id)).select_from(User)
    if filters:
        statement = statement.where(*filters)
    result = await session.execute(statement)
    return int(result.scalar_one() or 0)


def _sort_expression(expression, sort_dir: str):
    return expression.asc() if sort_dir == "asc" else expression.desc()


def _admin_user_remaining_quota_expression(policy: QuotaPolicy):
    window_start_at, _ = current_generation_quota_window(
        reset_hour=policy.daily_generation_reset_hour
    )
    limit_expr = func.coalesce(User.daily_generation_limit_override, policy.daily_generation_limit)
    return (
        window_start_at,
        case(
            (User.role == UserRole.admin, limit_expr),
            (
                UserGenerationQuotaWindow.id.is_(None),
                func.greatest(func.coalesce(User.daily_generation_balance, 0), limit_expr),
            ),
            (
                User.daily_generation_balance.is_(None),
                func.greatest(limit_expr - func.coalesce(UserGenerationQuotaWindow.used_count, 0), 0),
            ),
            else_=func.greatest(func.coalesce(User.daily_generation_balance, 0), 0),
        ),
    )


async def _admin_users_page(
    session: AsyncSession,
    filters: list,
    *,
    limit: int,
    offset: int,
    sort_by: str,
    sort_dir: str,
) -> tuple[list[User], dict[str, int]]:
    if sort_by == "generation_count":
        if get_settings().v2_enabled:
            from app.design.models import Operation
            counted_id, counted_user = Operation.id, Operation.owner_id
        else:
            counted_id, counted_user = GenerationJob.id, GenerationJob.user_id
        job_counts_subquery = (
            select(
                counted_user.label("user_id"),
                func.count(counted_id).label("generation_count"),
            )
            .group_by(counted_user)
            .subquery()
        )
        generation_count_expr = func.coalesce(job_counts_subquery.c.generation_count, 0)
        statement = (
            select(User, generation_count_expr)
            .outerjoin(job_counts_subquery, User.id == job_counts_subquery.c.user_id)
            .order_by(_sort_expression(generation_count_expr, sort_dir), User.email.asc())
            .limit(limit)
            .offset(offset)
        )
        if filters:
            statement = statement.where(*filters)
        result = await session.execute(statement)
        rows = result.all()
        users = [row[0] for row in rows]
        return users, {row[0].id: int(row[1] or 0) for row in rows}

    if sort_by == "remaining_quota":
        policy = get_quota_policy()
        window_start_at, remaining_quota_expr = _admin_user_remaining_quota_expression(policy)
        statement = (
            select(User)
            .outerjoin(
                UserGenerationQuotaWindow,
                and_(
                    UserGenerationQuotaWindow.user_id == User.id,
                    UserGenerationQuotaWindow.window_start_at == window_start_at,
                ),
            )
            .order_by(_sort_expression(remaining_quota_expr, sort_dir), User.email.asc())
            .limit(limit)
            .offset(offset)
        )
        if filters:
            statement = statement.where(*filters)
        result = await session.execute(statement)
        users = list(result.scalars())
        return users, await _job_counts_by_user_ids(session, [user.id for user in users])

    statement = (
        select(User)
        .order_by(_sort_expression(User.created_at, sort_dir), User.email.asc())
        .limit(limit)
        .offset(offset)
    )
    if filters:
        statement = statement.where(*filters)
    result = await session.execute(statement)
    users = list(result.scalars())
    return users, await _job_counts_by_user_ids(session, [user.id for user in users])


async def _get_admin_user_or_404(user_id: str, session: AsyncSession) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _effective_job_status(job: StoredJob) -> str:
    return job.status


def _active_user_count(jobs: list[StoredJob]) -> int:
    return len(
        {
            job.user_id
            for job in jobs
            if job.user_id and _effective_job_status(job) in ACTIVE_JOB_STATUSES
        }
    )


async def _legacy_counters(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(select(AuditCounter.key, AuditCounter.value))
    return {key: int(value) for key, value in result.all()}


def _legacy_counter(counters: dict[str, int], *keys: str) -> int:
    return sum(counters.get(key, 0) for key in keys)


@router.get("/provider-presets", response_model=AdminProviderPresetsOut)
async def get_admin_provider_presets(
    _: User = Depends(require_admin_user),
) -> AdminProviderPresetsOut:
    return AdminProviderPresetsOut(
        active=_active_provider_preset_out(_provider_preset_store().get()),
        presets=[
            AdminProviderPresetOut(
                id=preset.id,
                label=preset.label,
                analysis_provider=preset.analysis_provider,
                analysis_model=preset.analysis_model,
                image_provider=preset.image_provider,
                image_model=preset.image_model,
            )
            for preset in ACTIVE_PRESETS.values()
        ],
    )


@router.put("/provider-presets/active", response_model=AdminActiveProviderPresetOut)
async def update_admin_provider_preset(
    payload: AdminProviderPresetUpdateIn,
    current_user: User = Depends(require_admin_user),
) -> AdminActiveProviderPresetOut:
    active = _provider_preset_store().set(payload.preset_id, updated_by=current_user.id)
    return _active_provider_preset_out(active)


@router.get("/provider-usage", response_model=list[AdminProviderUsageSummaryOut])
async def get_admin_provider_usage(
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> list[AdminProviderUsageSummaryOut]:
    if get_settings().v2_enabled:
        jobs = await admin_jobs(session)
        if not jobs:
            return []
        usages = [job.token_usage for job in jobs if job.token_usage and job.token_usage.has_values()]
        return [AdminProviderUsageSummaryOut(
            provider="codex",
            calls=len(jobs),
            successes=sum(1 for job in jobs if job.status == "succeeded"),
            failures=sum(1 for job in jobs if job.status == "failed"),
            input_tokens=sum(u.input_tokens or 0 for u in usages) if usages else None,
            output_tokens=sum(u.output_tokens or 0 for u in usages) if usages else None,
            total_tokens=sum(u.total_tokens or 0 for u in usages) if usages else None,
            generated_images=sum(len(job.outputs) for job in jobs),
        )]
    summaries = await summarize_provider_usage(session)
    return [_provider_usage_summary_out(summary) for summary in summaries]


@router.get("/users", response_model=AdminUsersOut)
async def list_admin_users(
    query: str | None = Query(default=None, max_length=320),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    sort_by: Literal["created_at", "generation_count", "remaining_quota"] = Query(default="created_at"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminUsersOut:
    filters = _admin_user_filters(query)
    total = await _admin_users_total(session, filters)
    users, counts_by_user = await _admin_users_page(
        session,
        filters,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    user_items = [
        await _admin_user_out(user, counts_by_user.get(user.id, 0), session)
        for user in users
    ]
    return AdminUsersOut(
        users=user_items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + limit < total,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@router.get("/users/{user_id}", response_model=AdminUserOut)
async def get_admin_user(
    user_id: str,
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminUserOut:
    user = await _get_admin_user_or_404(user_id, session)
    counts_by_user = await _job_counts_by_user(session)
    return await _admin_user_out(user, counts_by_user.get(user.id, 0), session)


@router.patch("/users/{user_id}/role", response_model=AdminUserOut)
async def update_admin_user_role(
    user_id: str,
    payload: AdminUserRoleUpdateIn,
    current_user: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminUserOut:
    user = await _get_admin_user_or_404(user_id, session)
    if current_user.id == user.id and payload.role != UserRole.admin:
        _raise_self_lockout("admin_self_role_change_forbidden")
    user.role = payload.role
    user.updated_at = utc_now()
    await session.commit()
    counts_by_user = await _job_counts_by_user(session)
    return await _admin_user_out(user, counts_by_user.get(user.id, 0), session)


@router.patch("/users/{user_id}/ban", response_model=AdminUserOut)
async def update_admin_user_ban(
    user_id: str,
    payload: AdminUserBanUpdateIn,
    current_user: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminUserOut:
    user = await _get_admin_user_or_404(user_id, session)
    if current_user.id == user.id and payload.is_banned:
        _raise_self_lockout("admin_self_ban_forbidden")
    if not payload.is_banned and get_settings().v2_enabled:
        from app.design.models import AccountClosure
        closure = await session.get(AccountClosure, user.id)
        if closure is not None and closure.state in {"pending", "closed"}:
            raise HTTPException(status_code=409, detail="account_closure_prevents_reactivation")
    user.is_banned = payload.is_banned
    user.updated_at = utc_now()
    await session.commit()
    counts_by_user = await _job_counts_by_user(session)
    return await _admin_user_out(user, counts_by_user.get(user.id, 0), session)


@router.patch("/users/{user_id}/quota/daily-generation-limit", response_model=AdminUserOut)
async def update_admin_user_daily_generation_limit(
    user_id: str,
    payload: AdminUserDailyGenerationLimitUpdateIn,
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminUserOut:
    user = await _get_admin_user_or_404(user_id, session)
    from app.generation.quota import require_legacy_quota_writer
    user = await require_legacy_quota_writer(session, user)
    user.daily_generation_limit_override = payload.limit
    user.updated_at = utc_now()
    quota_state = await get_generation_quota_state(session, user)
    if (user.daily_generation_balance or 0) < quota_state.limit:
        user.daily_generation_balance = quota_state.limit
        user.updated_at = utc_now()
    await session.commit()
    counts_by_user = await _job_counts_by_user(session)
    return await _admin_user_out(user, counts_by_user.get(user.id, 0), session)


@router.post("/quota/daily-generation/reset", response_model=AdminDailyGenerationQuotaResetOut)
async def reset_admin_daily_generation_quota(
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminDailyGenerationQuotaResetOut:
    result = await reset_generation_quota(session)
    await session.commit()
    return _quota_reset_out(result)


@router.post(
    "/users/{user_id}/quota/daily-generation/reset",
    response_model=AdminDailyGenerationQuotaResetOut,
)
async def reset_admin_user_daily_generation_quota(
    user_id: str,
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminDailyGenerationQuotaResetOut:
    await _get_admin_user_or_404(user_id, session)
    result = await reset_generation_quota(session, user_id=user_id)
    await session.commit()
    return _quota_reset_out(result)


@router.post("/users/{user_id}/quota/daily-generation/adjust", response_model=AdminUserOut)
async def adjust_admin_user_daily_generation_balance(
    user_id: str,
    payload: AdminUserDailyGenerationBalanceAdjustIn,
    admin: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminUserOut:
    user = await _get_admin_user_or_404(user_id, session)
    await get_generation_quota_state(session, user)
    from app.generation.quota import wallet_if_installed
    wallet = await wallet_if_installed(session, user.id, lock=True)
    if wallet is not None and wallet.write_owner == "v2":
        # V2 accounts keep one auditable ledger; the panel's adjustment becomes an idempotent ledger entry.
        import uuid
        from app.design.service import ApplicationError
        from app.quota.adjustments import adjust
        if payload.delta == 0:
            raise HTTPException(status_code=422, detail="invalid_adjustment")
        try:
            await adjust(session, user.id, delta=payload.delta, key=uuid.uuid4().hex, actor_id=admin.id, reason="admin panel adjustment")
        except ApplicationError as exc:
            raise HTTPException(status_code=409, detail=exc.code) from exc
    else:
        await adjust_generation_quota_balance(session, user, payload.delta)
    await session.commit()
    counts_by_user = await _job_counts_by_user(session)
    return await _admin_user_out(user, counts_by_user.get(user.id, 0), session)


@router.post("/users/{user_id}/revoke-sessions", response_model=AdminRevokeSessionsOut)
async def revoke_admin_user_sessions(
    user_id: str,
    current_user: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminRevokeSessionsOut:
    if current_user.id == user_id:
        _raise_self_lockout("admin_self_session_revoke_forbidden")
    await _get_admin_user_or_404(user_id, session)
    result = await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id)
        .where(AuthSession.revoked_at.is_(None))
        .values(revoked_at=utc_now())
    )
    await session.commit()
    return AdminRevokeSessionsOut(revoked_sessions=int(result.rowcount or 0))


@router.get("/audit/summary", response_model=AdminAuditSummaryOut)
async def get_admin_audit_summary(
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> AdminAuditSummaryOut:
    jobs = await admin_jobs(session)
    job_counts = Counter(_effective_job_status(job) for job in jobs)
    current_job_count = len(jobs)
    # With V2 live the panel reports V2 only: V1 history is neither counted nor shown.
    legacy_counters = {} if get_settings().v2_enabled else await _legacy_counters(session)
    legacy_total_calls = _legacy_counter(legacy_counters, "legacy_total_calls", "total_calls")
    total_calls = current_job_count + legacy_total_calls
    token_usage = _token_usage_summary(jobs)
    image_usage = _image_usage_summary(jobs, token_usage)
    image_usage.generated_images += _legacy_counter(
        legacy_counters,
        "legacy_generated_images",
        "generated_images",
    )
    quota_policy = get_quota_policy()
    queue_status = generation_queue.status()
    if get_settings().v2_enabled:
        # V2 work is queued in the database; the legacy in-process queue is always empty.
        from types import SimpleNamespace
        queue_status = SimpleNamespace(
            pending_count=sum(1 for job in jobs if job.status == "queued"),
            active_count=sum(1 for job in jobs if job.status in RUNNING_JOB_STATUSES),
            parallelism=get_quota_policy().parallel_generation_limit,
        )
    active_job_count = sum(1 for job in jobs if _effective_job_status(job) in ACTIVE_JOB_STATUSES)
    current_success_count = job_counts.get("succeeded", 0)
    current_failure_count = job_counts.get("failed", 0)
    legacy_success_count = _legacy_counter(
        legacy_counters,
        "legacy_status_succeeded",
        "succeeded",
        "success",
    )
    legacy_failure_count = _legacy_counter(
        legacy_counters,
        "legacy_status_failed",
        "failed",
        "failure",
    )

    return AdminAuditSummaryOut(
        total_users=int(await session.scalar(select(func.count()).select_from(User)) or 0),
        active_users=_active_user_count(jobs),
        total_jobs=current_job_count,
        job_counts=dict(job_counts),
        queue_length=max(job_counts.get("queued", 0), queue_status.pending_count),
        queue_status=AdminQueueStatusOut(
            pending_count=queue_status.pending_count,
            active_count=queue_status.active_count,
            parallelism=queue_status.parallelism,
            high_load=queue_status.pending_count >= queue_status.parallelism,
        ),
        quota_policy=_quota_policy_out(quota_policy),
        legacy_counters=legacy_counters,
        total_calls=total_calls,
        success_rate=_rate(current_success_count + legacy_success_count, total_calls),
        failure_rate=_rate(current_failure_count + legacy_failure_count, total_calls),
        parallel_slots_used=max(
            queue_status.active_count,
            min(active_job_count, quota_policy.parallel_generation_limit),
        ),
        token_usage=token_usage,
        image_usage=image_usage,
        reliability=summarize_reliability(jobs),
    )


@router.get("/generation-jobs", response_model=list[AuditGenerationJobOut])
async def list_admin_generation_jobs(
    _: User = Depends(require_admin_user),
    session: AsyncSession = Depends(get_session),
) -> list[AuditGenerationJobOut]:
    jobs = sorted(
        await admin_jobs(session),
        key=lambda job: job.events[-1].created_at if job.events else "",
        reverse=True,
    )
    return [_job_to_generation_record(job) for job in jobs]


@router.patch("/quota-policy", response_model=AdminQuotaPolicy)
async def update_admin_quota_policy(
    policy: AdminQuotaPolicy,
    _: User = Depends(require_admin_user),
) -> AdminQuotaPolicy:
    saved_policy = save_quota_policy(QuotaPolicy.model_validate(policy.model_dump()))
    return _quota_policy_out(saved_policy)

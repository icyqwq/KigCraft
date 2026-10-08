from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.db.models import GenerationEvent, GenerationJob, GenerationOutput, JobStatus, Project
from app.db.session import get_sessionmaker
from app.generation.modes import (
    AI_OUTPUT_LANDMARKS_ENABLED,
    expected_output_count,
    normalize_generation_mode,
)
from app.generation.presets import resolve_preset, snapshot_for_preset
from app.generation.retired import reject_retired
from app.generation.schemas import CreateJobRequest, normalize_locale
from app.generation.usage import TokenUsage, merge_token_usage, parse_token_usage
from app.prompts.router import resolve_requirement_prompt_texts
from app.prompts.safety import compose_generation_prompt, sanitize_user_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredOutput:
    index: int
    object_key: str
    image_url: str
    width: int = 2048
    height: int = 1536
    landmarks: dict[str, dict[str, float]] | None = None


@dataclass(frozen=True)
class StoredEvent:
    sequence: int
    type: str
    progress: int
    message: str
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoredJob:
    id: str
    character_session_id: str
    generation_mode: str
    expected_output_count: int
    status: str
    progress: int
    queue_position: int | None
    phase_label: str
    provider: str
    prompt_payload: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None
    user_id: str | None = None
    accepted_output_index: int | None = None
    stop_requested: bool = False
    token_usage: TokenUsage | None = None
    outputs: list[StoredOutput] = field(default_factory=list)
    events: list[StoredEvent] = field(default_factory=list)


TERMINAL_STATUSES = {"accepted", "cancelled", "failed", "succeeded"}


class JobRepository:
    def __init__(self) -> None:
        self._prepare_lock = asyncio.Lock()
        self._prepared = False

    async def create(
        self,
        payload: CreateJobRequest,
        provider: str,
        user_id: str,
        provider_snapshot: dict[str, Any] | None = None,
        quota_window_start_at: datetime | None = None,
    ) -> StoredJob:
        reject_retired(provider, provider_snapshot)
        await self._prepare()
        job_id = str(uuid.uuid4())
        character_session_id = payload.character_session_id or str(uuid.uuid4())
        generation_mode = normalize_generation_mode(payload.generation_mode)
        output_count = expected_output_count(generation_mode)
        prompt_payload = self._compose_prompt_payload(
            payload,
            provider=provider,
            provider_snapshot=provider_snapshot,
            character_session_id=character_session_id,
            generation_mode=generation_mode,
            output_count=output_count,
        )
        if quota_window_start_at is not None:
            prompt_payload["quota_reservation"] = {
                "window_start_at": quota_window_start_at.isoformat(),
            }
        async with self._session() as session:
            project = Project(id=job_id, user_id=user_id, name="Generation Job")
            session.add(project)
            await session.flush()
            job = GenerationJob(
                id=job_id,
                project_id=project.id,
                user_id=user_id,
                status=JobStatus.queued,
                runtime_status="queued",
                provider=provider,
                character_session_id=character_session_id,
                generation_mode=generation_mode,
                expected_output_count=output_count,
                prompt_payload=prompt_payload,
                progress=0,
                queue_position=1,
                phase_label="Queued",
                stop_requested=False,
            )
            session.add(job)
            await session.flush()
            await self._append_event(
                session,
                job,
                event_type="queued",
                progress=0,
                message="Queued",
            )
            await session.commit()
        stored_job = await self.get(job_id)
        if stored_job is None:
            raise RuntimeError("Generation job was not persisted")
        return stored_job

    async def get(self, job_id: str) -> StoredJob | None:
        await self._prepare()
        async with self._session() as session:
            job = await session.get(GenerationJob, job_id)
            if job is None:
                return None
            return await self._stored_job_from_row(session, job)

    async def list_for_user(self, user_id: str) -> list[StoredJob]:
        await self._prepare()
        async with self._session() as session:
            result = await session.execute(
                select(GenerationJob)
                .where(GenerationJob.user_id == user_id)
                .order_by(GenerationJob.updated_at.desc(), GenerationJob.created_at.desc())
            )
            return [await self._stored_job_from_row(session, row) for row in result.scalars()]

    async def list_all(self) -> list[StoredJob]:
        await self._prepare()
        async with self._session() as session:
            result = await session.execute(
                select(GenerationJob).order_by(
                    GenerationJob.updated_at.desc(), GenerationJob.created_at.desc()
                )
            )
            return [await self._stored_job_from_row(session, row) for row in result.scalars()]

    async def list_page(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        generation_mode: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[StoredJob], int]:
        await self._prepare()
        filters = []
        if user_id is not None:
            filters.append(GenerationJob.user_id == user_id)
        if status:
            filters.append(GenerationJob.runtime_status == status)
        if generation_mode:
            filters.append(GenerationJob.generation_mode == generation_mode)

        async with self._session() as session:
            total_statement = select(func.count()).select_from(GenerationJob)
            page_statement = select(GenerationJob).order_by(
                GenerationJob.updated_at.desc(),
                GenerationJob.created_at.desc(),
            )
            if filters:
                total_statement = total_statement.where(*filters)
                page_statement = page_statement.where(*filters)
            total = int(await session.scalar(total_statement) or 0)
            result = await session.execute(page_statement.limit(limit).offset(offset))
            jobs = [await self._stored_job_from_row(session, row) for row in result.scalars()]
            return jobs, total

    async def list(self) -> list[StoredJob]:
        return await self.list_all()

    async def update(
        self,
        job_id: str,
        *,
        status: str,
        progress: int,
        phase_label: str,
        event_type: str | None = None,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> StoredJob | None:
        await self._prepare()
        async with self._session() as session:
            job = await session.get(GenerationJob, job_id)
            if job is None:
                return None
            if status == "failed":
                await self._refund_reserved_quota(session, job)
            job.runtime_status = status
            job.status = _db_status_for_runtime(status)
            job.progress = progress
            job.phase_label = phase_label
            job.updated_at = utc_now()
            if status != "queued":
                job.queue_position = None
            await self._append_event(
                session,
                job,
                event_type=event_type or status,
                progress=progress,
                message=message or phase_label,
                payload=payload,
            )
            await session.commit()
        return await self.get(job_id)

    async def set_queue_position(self, job_id: str, queue_position: int | None) -> StoredJob | None:
        await self._prepare()
        async with self._session() as session:
            job = await session.get(GenerationJob, job_id)
            if job is None or job.runtime_status != "queued":
                return await self._stored_job_from_row(session, job) if job is not None else None
            job.queue_position = queue_position
            job.updated_at = utc_now()
            await session.commit()
        return await self.get(job_id)

    async def set_outputs(self, job_id: str, outputs: list[StoredOutput]) -> StoredJob | None:
        await self._prepare()
        async with self._session() as session:
            job = await session.get(GenerationJob, job_id)
            if job is None:
                return None
            await session.execute(delete(GenerationOutput).where(GenerationOutput.job_id == job_id))
            for output in outputs:
                session.add(_output_row(job_id, output))
            job.updated_at = utc_now()
            await session.commit()
        return await self.get(job_id)

    async def append_output(self, job_id: str, output: StoredOutput) -> StoredJob | None:
        await self._prepare()
        async with self._session() as session:
            job = await session.get(GenerationJob, job_id)
            if job is None:
                return None
            await session.execute(
                delete(GenerationOutput)
                .where(GenerationOutput.job_id == job_id)
                .where(GenerationOutput.index == output.index)
            )
            session.add(_output_row(job_id, output))
            job.updated_at = utc_now()
            await session.commit()
        return await self.get(job_id)

    async def record_token_usage(self, job_id: str, usage: TokenUsage) -> StoredJob | None:
        await self._prepare()
        async with self._session() as session:
            job = await session.get(GenerationJob, job_id)
            if job is None:
                return None
            current_usage = parse_token_usage(job.token_usage)
            merged_usage = merge_token_usage(current_usage, usage)
            job.token_usage = merged_usage.to_dict() if merged_usage is not None else None
            job.updated_at = utc_now()
            if merged_usage is not None:
                await self._append_event(
                    session,
                    job,
                    event_type="token_usage",
                    progress=job.progress,
                    message="Token usage recorded",
                    payload=merged_usage.to_dict(),
                )
            await session.commit()
        return await self.get(job_id)

    async def patch_prompt_payload(self, job_id: str, patch: dict[str, Any]) -> StoredJob | None:
        await self._prepare()
        async with self._session() as session:
            job = await session.get(GenerationJob, job_id)
            if job is None:
                return None
            job.prompt_payload = {**(job.prompt_payload or {}), **patch}
            job.updated_at = utc_now()
            await session.commit()
        return await self.get(job_id)

    async def accept_output(self, job_id: str, output_index: int) -> StoredJob | None:
        await self._prepare()
        async with self._session() as session:
            job = await session.get(GenerationJob, job_id)
            if job is None:
                return None
            result = await session.execute(
                select(GenerationOutput)
                .where(GenerationOutput.job_id == job_id)
                .where(GenerationOutput.index == output_index)
            )
            if result.scalar_one_or_none() is None:
                return None
            job.accepted_output_index = output_index
            job.stop_requested = True
            job.runtime_status = "accepted"
            job.status = JobStatus.succeeded
            job.progress = 100
            job.queue_position = None
            job.phase_label = "Accepted output; stopped remaining generation"
            job.updated_at = utc_now()
            await session.execute(
                delete(GenerationOutput)
                .where(GenerationOutput.job_id == job_id)
                .where(GenerationOutput.index != output_index)
            )
            await self._append_event(
                session,
                job,
                event_type="accepted",
                progress=100,
                message="Accepted output; stopped remaining generation",
            )
            await session.commit()
        return await self.get(job_id)

    async def cancel(self, job_id: str) -> StoredJob | None:
        await self._prepare()
        async with self._session() as session:
            job = await session.get(GenerationJob, job_id)
            if job is None:
                return None
            if job.runtime_status in TERMINAL_STATUSES:
                return await self._stored_job_from_row(session, job)
            job.stop_requested = True
            job.runtime_status = "cancelled"
            job.status = JobStatus.cancelled
            job.queue_position = None
            job.phase_label = "Cancelled"
            job.updated_at = utc_now()
            await self._append_event(
                session,
                job,
                event_type="cancelled",
                progress=job.progress,
                message="Cancelled",
            )
            await session.commit()
        return await self.get(job_id)

    async def should_stop(self, job_id: str) -> bool:
        await self._prepare()
        async with self._session() as session:
            value = await session.scalar(
                select(GenerationJob.stop_requested).where(GenerationJob.id == job_id)
            )
            return bool(value)

    async def clear(self) -> None:
        await self._prepare()
        async with self._session() as session:
            project_ids = list(
                await session.scalars(select(GenerationJob.project_id))
            )
            await session.execute(delete(GenerationOutput))
            await session.execute(delete(GenerationEvent))
            if project_ids:
                await session.execute(delete(GenerationOutput).where(GenerationOutput.job_id.in_(project_ids)))
                await session.execute(delete(GenerationEvent).where(GenerationEvent.job_id.in_(project_ids)))
            await session.execute(delete(GenerationJob))
            if project_ids:
                await session.execute(delete(Project).where(Project.id.in_(project_ids)))
            await session.commit()

    async def _prepare(self) -> None:
        if self._prepared:
            return
        async with self._prepare_lock:
            if self._prepared:
                return
            await self._ensure_schema_columns()
            await self._fail_interrupted_jobs()
            self._prepared = True

    async def _ensure_schema_columns(self) -> None:
        async with get_sessionmaker()() as session:
            ddl = [
                "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS runtime_status VARCHAR(80) NOT NULL DEFAULT 'queued'",
                "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS character_session_id VARCHAR(160) NOT NULL DEFAULT ''",
                "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS generation_mode VARCHAR(80) NOT NULL DEFAULT 'front_design'",
                "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS expected_output_count INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS queue_position INTEGER",
                "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS phase_label VARCHAR(200) NOT NULL DEFAULT ''",
                "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS accepted_output_index INTEGER",
                "ALTER TABLE generation_jobs ADD COLUMN IF NOT EXISTS stop_requested BOOLEAN NOT NULL DEFAULT false",
                "ALTER TABLE generation_events ADD COLUMN IF NOT EXISTS sequence INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE generation_events ADD COLUMN IF NOT EXISTS progress INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE generation_events ADD COLUMN IF NOT EXISTS message TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE generation_outputs ADD COLUMN IF NOT EXISTS image_url VARCHAR(512) NOT NULL DEFAULT ''",
                "ALTER TABLE generation_outputs ADD COLUMN IF NOT EXISTS landmarks JSON",
                "CREATE INDEX IF NOT EXISTS generation_jobs_user_updated_idx ON generation_jobs(user_id, updated_at DESC)",
                "CREATE INDEX IF NOT EXISTS generation_events_job_sequence_idx ON generation_events(job_id, sequence)",
                "CREATE INDEX IF NOT EXISTS generation_outputs_job_index_idx ON generation_outputs(job_id, index)",
            ]
            try:
                for statement in ddl:
                    await session.execute(text(statement))
                await session.commit()
            except SQLAlchemyError:
                await session.rollback()
                raise

    async def _refund_reserved_quota(self, session: AsyncSession, job: GenerationJob) -> None:
        reservation = (job.prompt_payload or {}).get("quota_reservation")
        if not isinstance(reservation, dict) or reservation.get("refunded"):
            return
        raw_window_start_at = reservation.get("window_start_at")
        if not isinstance(raw_window_start_at, str):
            return
        try:
            window_start_at = datetime.fromisoformat(raw_window_start_at)
        except ValueError:
            logger.warning("Invalid quota reservation window job_id=%s", job.id)
            return
        from app.generation.quota import refund_generation_quota

        if await refund_generation_quota(session, job.user_id, window_start_at, legacy_job_id=job.id):
            job.prompt_payload = {
                **(job.prompt_payload or {}),
                "quota_reservation": {
                    **reservation,
                    "refunded": True,
                },
            }
            job.updated_at = utc_now()

    async def _fail_interrupted_jobs(self) -> None:
        async with self._session() as session:
            result = await session.execute(
                select(GenerationJob).where(GenerationJob.runtime_status.not_in(TERMINAL_STATUSES))
            )
            interrupted_jobs = list(result.scalars())
            for job in interrupted_jobs:
                await self._refund_reserved_quota(session, job)
                job.runtime_status = "failed"
                job.status = JobStatus.failed
                job.queue_position = None
                job.phase_label = "Service restarted; job interrupted"
                job.updated_at = utc_now()
                await self._append_event(
                    session,
                    job,
                    event_type="failed",
                    progress=job.progress,
                    message="Service restarted; job interrupted",
                )
            if interrupted_jobs:
                await session.commit()

    def _session(self) -> AsyncSession:
        return get_sessionmaker()()

    def _compose_prompt_payload(
        self,
        payload: CreateJobRequest,
        *,
        provider: str,
        provider_snapshot: dict[str, Any] | None,
        character_session_id: str,
        generation_mode: str,
        output_count: int,
    ) -> dict[str, Any]:
        prompt_payload = compose_generation_prompt(
            free_text=payload.free_text,
            requirement_texts=resolve_requirement_prompt_texts(payload.requirement_ids),
            reference_keys=payload.reference_keys,
        )
        prompt_payload.update(
            _provider_snapshot_fields(
                provider_snapshot
                if provider_snapshot is not None
                else _provider_snapshot_for_generation_provider(provider)
            )
        )
        reference_key_set = set(payload.reference_keys)
        prompt_payload["reference_descriptions"] = [
            {
                "reference_key": item.reference_key,
                "description": description,
            }
            for item in payload.reference_descriptions
            if item.reference_key in reference_key_set
            and (description := sanitize_user_text(item.description.strip()))
        ]
        detail_lock = _sanitize_detail_lock(payload.detail_lock, payload.reference_keys)
        if detail_lock is not None:
            prompt_payload["detail_lock"] = detail_lock
        prompt_payload["character_session_id"] = character_session_id
        prompt_payload["locale"] = normalize_locale(payload.locale)
        prompt_payload["requirement_ids"] = list(payload.requirement_ids)
        prompt_payload["generation_mode"] = generation_mode
        prompt_payload["expected_output_count"] = output_count
        prompt_payload["system_constraints"] = _system_constraints_for_mode(
            generation_mode,
            prompt_payload.get("system_constraints"),
            provider_preset=prompt_payload.get("provider_preset"),
        )
        return prompt_payload

    async def _stored_job_from_row(self, session: AsyncSession, job: GenerationJob) -> StoredJob:
        outputs_result = await session.execute(
            select(GenerationOutput)
            .where(GenerationOutput.job_id == job.id)
            .order_by(GenerationOutput.index)
        )
        events_result = await session.execute(
            select(GenerationEvent)
            .where(GenerationEvent.job_id == job.id)
            .order_by(GenerationEvent.sequence, GenerationEvent.created_at)
        )
        return StoredJob(
            id=job.id,
            user_id=job.user_id,
            character_session_id=job.character_session_id
            or str((job.prompt_payload or {}).get("character_session_id") or job.project_id),
            generation_mode=job.generation_mode
            or str((job.prompt_payload or {}).get("generation_mode") or "front_design"),
            expected_output_count=job.expected_output_count
            or int((job.prompt_payload or {}).get("expected_output_count") or 1),
            status=job.runtime_status or _runtime_status_from_db(job.status),
            progress=job.progress,
            queue_position=job.queue_position,
            phase_label=job.phase_label or "",
            provider=job.provider,
            prompt_payload=job.prompt_payload or {},
            created_at=job.created_at.isoformat() if job.created_at else None,
            updated_at=job.updated_at.isoformat() if job.updated_at else None,
            accepted_output_index=job.accepted_output_index,
            stop_requested=bool(job.stop_requested),
            token_usage=parse_token_usage(job.token_usage),
            outputs=[
                StoredOutput(
                    index=output.index,
                    object_key=output.object_key,
                    image_url=output.image_url or _image_url_from_object_key(output.object_key),
                    width=output.width,
                    height=output.height,
                    landmarks=output.landmarks,
                )
                for output in outputs_result.scalars()
            ],
            events=[
                StoredEvent(
                    sequence=event.sequence,
                    type=event.type,
                    progress=event.progress,
                    message=event.message,
                    created_at=event.created_at.isoformat(),
                    payload=event.payload or {},
                )
                for event in events_result.scalars()
            ],
        )

    async def _append_event(
        self,
        session: AsyncSession,
        job: GenerationJob,
        *,
        event_type: str,
        progress: int,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await session.execute(
            select(GenerationJob.id).where(GenerationJob.id == job.id).with_for_update()
        )
        sequence = (
            await session.scalar(
                select(func.max(GenerationEvent.sequence)).where(GenerationEvent.job_id == job.id)
            )
            or 0
        ) + 1
        session.add(
            GenerationEvent(
                job_id=job.id,
                sequence=sequence,
                type=event_type,
                progress=progress,
                message=message,
                payload=payload or {},
            )
        )


def _output_row(job_id: str, output: StoredOutput) -> GenerationOutput:
    return GenerationOutput(
        job_id=job_id,
        index=output.index,
        object_key=output.object_key,
        image_url=output.image_url,
        width=output.width,
        height=output.height,
        landmarks=output.landmarks,
    )


def _db_status_for_runtime(status: str) -> JobStatus:
    if status == "queued":
        return JobStatus.queued
    if status == "succeeded" or status == "accepted":
        return JobStatus.succeeded
    if status == "failed":
        return JobStatus.failed
    if status == "cancelled":
        return JobStatus.cancelled
    if status == "saving_outputs":
        return JobStatus.uploading_outputs
    return JobStatus.running


def _runtime_status_from_db(status: JobStatus | str) -> str:
    value = status.value if isinstance(status, JobStatus) else str(status)
    if value == JobStatus.running.value:
        return "generating"
    if value == JobStatus.uploading_outputs.value:
        return "saving_outputs"
    return value


def _image_url_from_object_key(object_key: str) -> str:
    if object_key.startswith("fixture/"):
        return "/api/static/fixtures/" + object_key.removeprefix("fixture/")
    return "/api/generated/" + object_key.lstrip("/")


def _sanitize_detail_lock(
    value: Any, submitted_reference_keys: list[str]
) -> dict[str, Any] | None:
    if value is None:
        return None
    detail_lock = value.model_dump() if hasattr(value, "model_dump") else dict(value)
    safe_reference_keys = {
        key for key in submitted_reference_keys if _is_safe_detail_reference_key(key)
    }
    features = [
        {
            "id": str(item.get("id") or ""),
            "kind": str(item.get("kind") or "other"),
            "label": sanitize_user_text(str(item.get("label") or "")),
            "description": sanitize_user_text(str(item.get("description") or "")),
        }
        for item in (detail_lock.get("features") or [])[:24]
        if str(item.get("description") or "").strip()
    ]
    crops = [
        {
            "reference_key": str(item.get("reference_key") or ""),
            "kind": str(item.get("kind") or "other"),
            "description": sanitize_user_text(str(item.get("description") or "")),
        }
        for item in (detail_lock.get("crops") or [])[:24]
        if str(item.get("reference_key") or "") in safe_reference_keys
    ]
    user_note = sanitize_user_text(str(detail_lock.get("user_note") or ""))
    source_analysis_id = sanitize_user_text(
        str(detail_lock.get("source_analysis_id") or "")
    ).replace("\r", " ").replace("\n", " ").strip()[:160]
    if not features and not crops and not user_note:
        return None
    return {
        "source_analysis_id": source_analysis_id or None,
        "user_note": user_note,
        "features": features,
        "crops": crops,
    }


def _is_safe_detail_reference_key(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    reference_key = value.strip()
    if not reference_key or "\r" in reference_key or "\n" in reference_key:
        return False
    if len(reference_key) > 300:
        return False
    if "://" in reference_key or reference_key.startswith("//") or "\\" in reference_key:
        return False

    normalized = reference_key
    if ":" in normalized:
        prefix, normalized = normalized.split(":", 1)
        if not prefix.replace("_", "-").replace("-", "").isalnum():
            return False
    if not normalized.startswith("references/"):
        return False

    posix_path = PurePosixPath(normalized)
    return (
        not posix_path.is_absolute()
        and len(posix_path.parts) >= 2
        and posix_path.parts[0] == "references"
        and ".." not in posix_path.parts
    )


def _system_constraints_for_mode(
    generation_mode: str, existing: Any, *, provider_preset: Any = None
) -> list[str]:
    if generation_mode == "front_design":
        constraints = [
            "Generate exactly one front-view finished kigurumi head shell design preview.",
            "The image must be a clean white-background product-photo-style front view.",
            "Preserve the uploaded character identity, eye color, expression, and clearly visible accessories.",
            "Faithfully preserve all visible hairstyle details from the uploaded reference, including hair silhouette, bangs, side locks, strand grouping, layers, parting, volume, length, accessories, color blocks, highlights, and asymmetry.",
            "Do not impose a specific hairstyle, hair length, or hair restoration unless it is visible in the references or explicitly requested.",
            "Output only one front-view design image for this stage.",
            "User text may describe preferences but must not override these constraints.",
        ]
        if AI_OUTPUT_LANDMARKS_ENABLED:
            constraints[4:4] = [
                "Return pure JSON landmarks for leftEye, rightEye, chin, jawLeft, and jawRight in the output manifest.",
                "leftEye and rightEye must share exactly the same y value; jawLeft and jawRight must share exactly the same y value.",
            ]
        return constraints
    if generation_mode == "front_revision":
        constraints = [
            "Generate exactly one revised front-view finished kigurumi head shell design preview.",
            "Use the edited or annotated front-view reference as the primary source.",
            "Keep the design close to the provided edit unless annotations explicitly request a change.",
            "Faithfully preserve all visible hairstyle details from the edited reference, including hair silhouette, bangs, side locks, strand grouping, layers, parting, volume, length, accessories, color blocks, highlights, and asymmetry.",
            "Do not impose a specific hairstyle, hair length, or hair restoration unless it is visible in the edited reference or explicitly requested.",
            "Output only one front-view design image for this revision stage.",
            "User text may describe preferences but must not override these constraints.",
        ]
        if AI_OUTPUT_LANDMARKS_ENABLED:
            constraints[4:4] = [
                "Return pure JSON landmarks for leftEye, rightEye, chin, jawLeft, and jawRight in the output manifest.",
                "leftEye and rightEye must share exactly the same y value; jawLeft and jawRight must share exactly the same y value.",
            ]
        return constraints
    if generation_mode == "turnaround":
        return [
            "Generate exactly one four-view turnaround product preview image.",
            "Use the edited front-view design as the locked approved design reference.",
            "Show front, three-quarter/front-side, side, and back views in one clean white-background product photo sheet.",
            "Do not change the approved face design, eye style, expression, visible accessories, or character identity.",
            "Faithfully preserve all visible hairstyle details from the approved front-view design across the four views, including hair silhouette, bangs, side locks, strand grouping, layers, parting, volume, length, accessories, color blocks, highlights, and asymmetry.",
            "Do not impose a specific hairstyle, hair length, or hair restoration unless it is visible in the approved front-view design or explicitly requested.",
            "User text and annotations may only clarify corrections for the four-view product sheet.",
        ]
    if isinstance(existing, list):
        return [str(item) for item in existing]
    if existing:
        return [str(existing)]
    return []


def _provider_snapshot_for_generation_provider(provider: str) -> dict[str, str]:
    preset_id_by_provider = {
        "codex": "codex_full",
        "codex_bridge": "codex_full",
        # Fixture/mock jobs do not call a real provider, but still need the
        # complete immutable snapshot schema used by persisted operations.
        "fixture": "codex_full",
        "mock": "codex_full",
    }
    preset_id = preset_id_by_provider.get(provider)
    return snapshot_for_preset(resolve_preset(preset_id)) if preset_id else {}


def _provider_snapshot_fields(snapshot: dict[str, Any]) -> dict[str, str]:
    fields = (
        "provider_preset",
        "analysis_provider",
        "analysis_model",
        "image_provider",
        "image_model",
    )
    return {key: str(snapshot[key]) for key in fields if key in snapshot}

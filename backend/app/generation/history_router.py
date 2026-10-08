"""Read legacy jobs in V2 without initializing the old executor or changing state."""
from collections.abc import AsyncIterator, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.models import UserRole
from app.auth.sessions import get_current_user
from app.db.models import GenerationJob, GenerationEvent, GenerationOutput, JobStatus, User
from app.db.session import get_sessionmaker
from app.generation.schemas import GenerationJobOut, GenerationEventOut, GenerationOutputOut
from app.generation.usage import parse_token_usage

router = APIRouter(prefix="/generation", tags=["legacy-history"])

class GenerationJobListOut(BaseModel):
    jobs: list[GenerationJobOut]
    total: int
    limit: int
    offset: int

async def readonly() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as db:
        async with db.begin():
            await db.execute(text("SET TRANSACTION READ ONLY"))
            yield db

async def accessible(db: AsyncSession, job_id: str, user: User) -> GenerationJob:
    job = await db.get(GenerationJob, job_id)
    if job is None or (user.role != UserRole.admin and job.user_id != user.id):
        raise HTTPException(404, detail="Generation job not found")
    return job

async def job_outputs(
    db: AsyncSession, jobs: Sequence[GenerationJob],
) -> dict[str, list[GenerationOutputOut]]:
    by_job: dict[str, list[GenerationOutputOut]] = {job.id: [] for job in jobs}
    if by_job:
        rows = (await db.scalars(select(GenerationOutput).where(
            GenerationOutput.job_id.in_(by_job)).order_by(GenerationOutput.index))).all()
        for row in rows:
            by_job[row.job_id].append(GenerationOutputOut(index=row.index,object_key=row.object_key,
                image_url=row.image_url,width=row.width,height=row.height,landmarks=row.landmarks))
    return by_job

def project_job(job: GenerationJob, outputs: list[GenerationOutputOut]) -> GenerationJobOut:
    payload = job.prompt_payload or {}
    usage = parse_token_usage(job.token_usage)
    status = job.status.value if isinstance(job.status, JobStatus) else str(job.status)
    status = {"running": "generating", "uploading_outputs": "saving_outputs"}.get(status, status)
    return GenerationJobOut(id=job.id,
        character_session_id=job.character_session_id or str(payload.get("character_session_id") or job.project_id),
        generation_mode=job.generation_mode or str(payload.get("generation_mode") or "front_design"),
        expected_output_count=job.expected_output_count or int(payload.get("expected_output_count") or 1),
        status=job.runtime_status or status,progress=job.progress,queue_position=job.queue_position,
        phase_label=job.phase_label or "",provider=job.provider,
        created_at=job.created_at.isoformat() if job.created_at else None,
        updated_at=job.updated_at.isoformat() if job.updated_at else None,
        accepted_output_index=job.accepted_output_index,
        token_usage=usage.to_dict() if usage else None,outputs=outputs)

@router.get("/jobs", response_model=GenerationJobListOut)
async def list_jobs(response: Response, user: User = Depends(get_current_user),
    status: str | None = Query(default=None,min_length=1,max_length=80),
    generation_mode: str | None = Query(default=None,min_length=1,max_length=80),
    limit: int = Query(default=50,ge=1,le=100), offset: int = Query(default=0,ge=0),
    db: AsyncSession = Depends(readonly)):
    filters = [GenerationJob.user_id == user.id]
    if status:
        filters.append(GenerationJob.runtime_status == status)
    if generation_mode:
        filters.append(GenerationJob.generation_mode == generation_mode)
    total = await db.scalar(select(func.count()).select_from(GenerationJob).where(*filters))
    rows = list((await db.scalars(select(GenerationJob).where(*filters).order_by(
        GenerationJob.updated_at.desc(),GenerationJob.created_at.desc(),GenerationJob.id).limit(limit).offset(offset))).all())
    outputs = await job_outputs(db, rows)
    response.headers["Cache-Control"] = "private, no-store"
    return GenerationJobListOut(jobs=[project_job(row,outputs[row.id]) for row in rows],total=total,limit=limit,offset=offset)

@router.get("/jobs/{job_id}", response_model=GenerationJobOut)
async def get_job(job_id: str, response: Response, user: User = Depends(get_current_user),
    db: AsyncSession = Depends(readonly)):
    job = await accessible(db,job_id,user)
    outputs = await job_outputs(db,[job])
    response.headers["Cache-Control"] = "private, no-store"
    return project_job(job,outputs[job.id])

@router.get("/jobs/{job_id}/events", response_model=list[GenerationEventOut])
async def get_events(job_id: str, response: Response, user: User = Depends(get_current_user),
    db: AsyncSession = Depends(readonly)):
    await accessible(db,job_id,user)
    rows = (await db.scalars(select(GenerationEvent).where(GenerationEvent.job_id==job_id).order_by(
        GenerationEvent.sequence,GenerationEvent.created_at,GenerationEvent.id))).all()
    response.headers["Cache-Control"] = "private, no-store"
    return [GenerationEventOut(sequence=row.sequence,type=row.type,progress=row.progress,message=row.message,
        created_at=row.created_at.isoformat(),payload=row.payload or {}) for row in rows]

async def retired():
    raise HTTPException(503, detail="legacy_execution_disabled")

for path in ["/jobs","/detail-analysis","/local-revision-jobs","/projects/{project_id}/jobs",
             "/jobs/{job_id}/accept","/jobs/{job_id}/cancel"]:
    router.add_api_route(path, retired, methods=["POST"], include_in_schema=False)
router.add_api_route("/queue-status", retired, methods=["GET"], include_in_schema=False)

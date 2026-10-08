"""Read-only drain report before deploying removal of legacy providers."""
import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import and_, cast, func, or_, select, String, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import Settings
from app.db.models import GenerationJob
from app.db.v2_schema import require_v2_schema
from app.design.models import RuntimeControl
from app.maintenance.orphan_assets import database_identity


def settled_legacy_jobs():
    # Only mutually consistent terminal state pairs establish recorded completion.
    stored = cast(GenerationJob.status, String)
    return func.coalesce(or_(
        and_(stored == "succeeded", GenerationJob.runtime_status.in_(["succeeded", "accepted"])),
        and_(stored == "failed", GenerationJob.runtime_status == "failed"),
        and_(stored == "cancelled", GenerationJob.runtime_status == "cancelled"),
        and_(stored == "expired", GenerationJob.runtime_status == "expired"),
    ), False)


async def inspect_retirement(db, *, writers_stopped=False):
    settled = settled_legacy_jobs()
    total = await db.scalar(select(func.count()).select_from(GenerationJob))
    pending = await db.scalar(select(func.count()).select_from(GenerationJob).where(~settled))
    ids = list(await db.scalars(select(GenerationJob.id).where(~settled).order_by(GenerationJob.id).limit(25)))
    control = await db.get(RuntimeControl, "global")
    paused = control is not None and control.blocked
    return {"scope":"v2_provider_retirement", "database":await database_identity(db),
        "status":"records_drained" if writers_stopped and paused and pending == 0 else "blocked",
        "writers_stopped_acknowledged":bool(writers_stopped), "global_paused":paused,
        "legacy_jobs":int(total), "unsettled_jobs":int(pending), "sample_job_ids":ids,
        "state_changes":False, "upstream_or_process_state_verified":False}


async def run(settings, writers_stopped):
    if not settings.v2_enabled:
        raise ValueError("v2_configuration_required")
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            await db.execute(text("SET LOCAL statement_timeout = '5s'"))
            return await inspect_retirement(db, writers_stopped=writers_stopped)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise ValueError()
        report = asyncio.run(run(Settings(_env_file=args.env_file), args.writers_stopped))
    except Exception:
        print(json.dumps({"status":"unavailable","state_changes":False}))
        raise SystemExit(1) from None
    print(json.dumps(report))
    if report["status"] != "records_drained":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

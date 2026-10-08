from sqlalchemy import text
from app.admin.v2_controls import set_control
from app.db.models import GenerationJob, Project
from app.maintenance.provider_retirement import inspect_retirement
from .test_operations import factory, setup
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints


async def test_drain_checks_all_accounts_and_keeps_unknown_states(factory):
    owner, _, _ = await setup(factory)
    other, _, _ = await setup(factory)
    async with factory.begin() as db:
        await set_control(db, "global", True)
        project = Project(user_id=owner, name="private title")
        db.add(project)
        await db.flush()
        cases = [("succeeded","accepted"), ("failed","failed"), ("cancelled","cancelled"),
                 ("running","accepted"), ("succeeded","running"), ("succeeded",""),
                 ("queued","queued")]
        jobs = [GenerationJob(user_id=owner if i < 3 else other, project_id=project.id,
            status=stored, runtime_status=runtime, provider="qwen_seedream") for i,(stored,runtime) in enumerate(cases)]
        db.add_all(jobs)
    before = await fingerprints(factory)
    async with factory.begin() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        report = await inspect_retirement(db, writers_stopped=True)
    assert report["unsettled_jobs"] == 4 and report["legacy_jobs"] == 7
    assert set(report["sample_job_ids"]) == {job.id for job in jobs[3:]}
    assert report["status"] == "blocked" and report["state_changes"] is False
    assert owner not in repr(report) and "private title" not in repr(report)
    assert await fingerprints(factory) == before


async def test_stop_acknowledgement_and_global_pause_required_even_without_old_jobs(factory):
    async with factory.begin() as db:
        assert (await inspect_retirement(db))["status"] == "blocked"
        await set_control(db, "global", True)
    async with factory.begin() as db:
        assert (await inspect_retirement(db))["status"] == "blocked"
        report = await inspect_retirement(db, writers_stopped=True)
    assert report["status"] == "records_drained"
    assert report["upstream_or_process_state_verified"] is False

"""Minimal owner-only provenance projection; raw legacy metadata stays private."""
from sqlalchemy import select
from app.design.models import LegacyImport,Revision
from app.design.service import ApplicationError,owned_design


async def provenance(db,owner_id,revision_id=None,*,design_id=None,cursor=None,limit=50):
    if revision_id is not None:
        revision=await db.get(Revision,revision_id)
        if revision is None:
            raise ApplicationError("not_found")
        design_id=revision.design_id
    await owned_design(db,owner_id,design_id)
    query=select(LegacyImport).where(LegacyImport.owner_id==owner_id,LegacyImport.design_id==design_id)
    if revision_id is not None:
        query=query.where(LegacyImport.revision_id==revision_id)
    if cursor:
        query=query.where(LegacyImport.id>cursor)
    rows=list((await db.scalars(query.order_by(LegacyImport.id).limit(limit+1))).all())
    selected=rows[:limit]
    job_ids={row.source_summary.get("job_id") for row in selected if row.source_summary.get("job_id")}
    # Album aliases may point at the same output; the output mapping carries the exact job link.
    output_ids={row.source_summary.get("linked_output_id") for row in selected if row.source_summary.get("linked_output_id")}
    aliases=list((await db.scalars(select(LegacyImport).where(
        LegacyImport.owner_id==owner_id,LegacyImport.source_kind=="output",LegacyImport.source_id.in_(output_ids)))).all())
    for row in aliases:
        if row.source_summary.get("job_id"):job_ids.add(row.source_summary["job_id"])
    jobs={row.source_id:row.source_summary for row in (await db.scalars(select(LegacyImport).where(
        LegacyImport.owner_id==owner_id,LegacyImport.source_kind=="job",LegacyImport.source_id.in_(job_ids)))).all()}
    alias_jobs={row.source_id:row.source_summary.get("job_id") for row in aliases}
    items=[]
    for row in selected:
        source=row.source_summary
        job=source if row.source_kind=="job" else jobs.get(source.get("job_id") or alias_jobs.get(source.get("linked_output_id")),{})
        provider=job.get("provider") or {"provider":source.get("provider")}
        items.append({"id":row.id,"source_kind":row.source_kind,"source_id":row.source_id,
            "provider":{field:provider.get(field) for field in (
                "provider","analysis_provider","analysis_model","image_provider","image_model")},
            "legacy_execution":job.get("status",{}).get("execution","unknown"),
            "legacy_selection":job.get("status",{}).get("selection","none"),
            "view":source.get("view","unknown"),
            "recipe_state":source.get("recipe",{}).get("state","absent"),
            "recipe_replayed":False,"issues":source.get("issues",[])})
    return {"items":items,"next_cursor":selected[-1].id if len(rows)>limit else None}

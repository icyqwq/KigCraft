"""Resolve a view-set source without advancing any branch or reserving quota."""
from sqlalchemy import select
from app.contracts.v2 import SnapshotReference
from app.contracts.viewsets import ViewSetOptions, ViewSetSnapshot, ViewSetSource
from app.design.models import Asset, Revision
from app.design.service import ApplicationError, owned_branch


async def freeze_viewset(db, owner: str, branch_id: str, *, source_revision_id: str,
                         expected_branch_version: int, options: ViewSetOptions) -> ViewSetSnapshot:
    design, branch = await owned_branch(db, owner, branch_id, lock=True)
    if design.archived_at:
        raise ApplicationError("design_archived")
    if branch.state_version != expected_branch_version:
        raise ApplicationError("state_conflict")
    revision = (await db.execute(select(Revision).where(
        Revision.id == source_revision_id, Revision.design_id == design.id))).scalar_one_or_none()
    if revision is None:
        raise ApplicationError("not_found")
    # Historical fronts are valid sources; generated side/back/collage results are not.
    if revision.view != "front":
        raise ApplicationError("viewset_front_required")
    asset = (await db.execute(select(Asset).where(
        Asset.id == revision.clean_asset_id, Asset.owner_id == owner, Asset.status == "ready"
    ))).scalar_one_or_none()
    if asset is None:
        raise ApplicationError("input_asset_unavailable")
    if not options.back_details.strip() and not options.allow_back_inference:
        raise ApplicationError("viewset_inference_required")
    from app.core.config import get_settings
    if 1 + len(options.references) > get_settings().v2_max_input_images:
        raise ApplicationError("reference_limit_exceeded")
    extras = []
    for ordinal, item in enumerate(options.references, 2):
        extra = (await db.execute(select(Asset).where(
            Asset.id == item.asset_id, Asset.owner_id == owner, Asset.status == "ready"))).scalar_one_or_none()
        if extra is None:
            raise ApplicationError("input_asset_unavailable")
        extras.append(SnapshotReference(asset_id=extra.id, sha256=extra.sha256, ordinal=ordinal, role="reference",
                                        usage=item.usage.strip() or "Additional reference supplied by the user"))
    return ViewSetSnapshot(reference_images=tuple(extras), design_id=design.id, branch_id=branch.id, branch_version=branch.state_version,
        source=ViewSetSource(revision_id=revision.id, number=revision.number, width=asset.width, height=asset.height,
            image=SnapshotReference(asset_id=asset.id, sha256=asset.sha256, ordinal=1, role="base",
                usage="Original selected front; preserve character identity and physical materials")),
        options=options)
